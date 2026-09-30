"""Movie Buddy endpoints — opt-in, spoiler-safe Gemini chat.

The per-user flag lives under /api/app/buddy/*; the chat itself sits beside
the other AI routes under /api/app/ai/buddy/*. All four require a logged-in
user and a configured GEMINI_API_KEY; chat/history additionally require the
user's buddy flag.
"""

from __future__ import annotations

import time
from typing import Optional

from aiohttp import web

from main.utils import buddy_context, buddy_store, gemini
from main.utils.user_auth import get_user
from main.vars import Var

routes = web.RouteTableDef()

_MESSAGE_CAP = 2000  # chars; longer messages are rejected with 400

# Per-user token bucket, copied from ai_rec_routes and tuned for chat
# (burst 8, ~1 token per 10s). ponytail: in-memory / per process — fine for
# the single-instance deploy; move to a shared store if multi-instance.
_RATE_CAPACITY = 8.0
_RATE_REFILL_PER_SEC = 0.1
_RATE_RETRY_AFTER = 10
_buckets: dict[int, tuple[float, float]] = {}


def _uid(request: web.Request) -> Optional[int]:
    user = get_user(request)
    if not user:
        return None
    try:
        return int(user["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def _take_token(user_id: int) -> bool:
    now = time.monotonic()
    tokens, last = _buckets.get(user_id, (_RATE_CAPACITY, now))
    tokens = min(_RATE_CAPACITY, tokens + (now - last) * _RATE_REFILL_PER_SEC)
    if tokens < 1:
        _buckets[user_id] = (tokens, now)
        return False
    _buckets[user_id] = (tokens - 1, now)
    return True


def _rate_limited() -> web.Response:
    return web.json_response(
        {"error": "Too many requests — give the buddy a moment."},
        status=429,
        headers={"Retry-After": str(_RATE_RETRY_AFTER)},
    )


def _not_configured() -> web.Response:
    return web.json_response({"error": "Movie Buddy is not enabled"}, status=404)


def _disabled() -> web.Response:
    return web.json_response(
        {"error": "Movie Buddy is turned off — enable it in the Buddy tab first."},
        status=403,
    )


def _message_id(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _quota_retry_after() -> str:
    """Seconds until the UTC date rolls over and the daily quota resets."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                 microsecond=0)
    return str(max(1, int((midnight - now).total_seconds())))


def _public_context(context: Optional[dict]) -> Optional[dict]:
    """Strip prompt-only metadata (underscore-prefixed) from the response."""
    if context is None:
        return None
    return {k: v for k, v in context.items() if not k.startswith("_")}


@routes.get("/api/app/buddy/prefs")
async def buddy_prefs_get(request: web.Request) -> web.Response:
    if not gemini.available():
        return _not_configured()
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    return web.json_response({"enabled": await buddy_store.get_enabled(uid)})


@routes.post("/api/app/buddy/prefs")
async def buddy_prefs_set(request: web.Request) -> web.Response:
    if not gemini.available():
        return _not_configured()
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    enabled = body.get("enabled") is True
    if not await buddy_store.set_enabled(uid, enabled):
        return web.json_response(
            {"error": "Movie Buddy needs the database — try again shortly."},
            status=503,
        )
    return web.json_response({"ok": True, "enabled": enabled})


@routes.post("/api/app/ai/buddy/chat")
async def buddy_chat(request: web.Request) -> web.Response:
    if not gemini.available():
        return _not_configured()
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    if not await buddy_store.get_enabled(uid):
        return _disabled()
    # Daily quota first (cheap store counter), then the burst bucket.
    if not await buddy_store.consume_daily(uid, Var.BUDDY_DAILY_LIMIT):
        return web.json_response(
            {"error": "Daily Movie Buddy limit reached — see you tomorrow."},
            status=429,
            headers={"Retry-After": _quota_retry_after()},
        )
    if not _take_token(uid):  # every chat message calls Gemini
        return _rate_limited()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    message = str(body.get("message") or "").strip()
    if not message or len(message) > _MESSAGE_CAP:
        return web.json_response(
            {"error": f"message must be 1-{_MESSAGE_CAP} characters"}, status=400,
        )
    item_id = body.get("itemId")
    message_id = _message_id(body.get("messageId"))
    context = await buddy_context.resolve_context(uid, item_id, message_id)
    session_key = buddy_store.session_key_for(item_id, message_id)
    history = await buddy_store.get_history(uid, session_key)
    system_instruction, contents = buddy_context.build_prompt(context, history, message)
    data = await gemini.generate_content(
        contents,
        model=Var.GEMINI_BUDDY_MODEL,
        timeout=45,
        system_instruction=system_instruction,
        # Hard output cap: even a jailbroken buddy stays useless as a
        # general-purpose assistant.
        max_output_tokens=400,
    )
    reply = _extract_reply(data)
    if not reply:
        # Never persist a dangling user message — the pair goes in atomically
        # only once a reply exists.
        return web.json_response(
            {"error": "The buddy couldn't think of a reply — try again."},
            status=502,
        )
    await buddy_store.append_exchange(uid, session_key, message, reply)
    return web.json_response({"reply": reply, "context": _public_context(context)})


def _extract_reply(data: Optional[dict]) -> Optional[str]:
    """Pull the text out of a generateContent response, or None."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(str(p.get("text", "")) for p in parts).strip()
        return text or None
    except (AttributeError, IndexError, KeyError, TypeError):
        return None


@routes.get("/api/app/ai/buddy/history")
async def buddy_history(request: web.Request) -> web.Response:
    if not gemini.available():
        return _not_configured()
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    if not await buddy_store.get_enabled(uid):
        return _disabled()
    session_key = buddy_store.session_key_for(
        request.query.get("itemId"), _message_id(request.query.get("messageId")),
    )
    return web.json_response(
        {"messages": await buddy_store.get_history(uid, session_key)},
    )


@routes.delete("/api/app/ai/buddy/history")
async def buddy_history_delete(request: web.Request) -> web.Response:
    """Clear one session (itemId/messageId given) or every session (bare)."""
    if not gemini.available():
        return _not_configured()
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    if not await buddy_store.get_enabled(uid):
        return _disabled()
    item_id = request.query.get("itemId")
    message_id = _message_id(request.query.get("messageId"))
    if item_id or message_id is not None:
        await buddy_store.delete_history(
            uid, buddy_store.session_key_for(item_id, message_id),
        )
    else:
        await buddy_store.delete_history(uid)
    # Idempotent clear: a store hiccup self-heals via the 90-day TTL, so the
    # response stays contract-simple.
    return web.json_response({"ok": True})

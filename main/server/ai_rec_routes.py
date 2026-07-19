"""Per-user AI recommendation endpoints (Gemini-backed, catalogue-grounded).

Both routes require a logged-in user and a configured GEMINI_API_KEY.
"""

from __future__ import annotations

import time
from typing import Optional

from aiohttp import web

from main.utils import ai_rec, gemini
from main.utils.user_auth import get_user

routes = web.RouteTableDef()

# Per-user token bucket over the Gemini-invoking actions (chat + refresh), so an
# authenticated user can't drain the API quota. ponytail: in-memory / per
# process — fine for the single-instance deploy; move to a shared store if this
# ever runs multi-instance.
_RATE_CAPACITY = 8.0
_RATE_REFILL_PER_SEC = 0.1  # ~1 token every 10s, burst of 8
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
        {"error": "Too many requests — give the recommender a moment."},
        status=429,
        headers={"Retry-After": str(_RATE_RETRY_AFTER)},
    )


@routes.get("/api/app/ai/recommendations")
async def ai_recommendations(request: web.Request) -> web.Response:
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    refresh = request.query.get("refresh") in ("1", "true", "yes")
    # Only the (expensive) refresh path spends a token; a plain open is cache-served.
    if refresh and not _take_token(uid):
        return _rate_limited()
    result = await ai_rec.get_ai_recommendations(uid, refresh=refresh)
    return web.json_response(result)


@routes.post("/api/app/ai/recommendations")
async def ai_recommendations_chat(request: web.Request) -> web.Response:
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    if not _take_token(uid):  # every chat query calls Gemini (cache-bypassed)
        return _rate_limited()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):  # a JSON list/scalar would break body.get()
        body = {}
    query = (str(body.get("query") or "")).strip()[:300]
    result = await ai_rec.get_ai_recommendations(uid, query=query or None)
    return web.json_response(result)

"""Movie Buddy persistence — MongoDB-backed, reuses the catalogue Motor client.

Schema — collection ``buddy_prefs``:
  { user_id: int, buddy_enabled: bool, day: str (UTC date), day_count: int,
    updated_at: datetime }
  unique index on user_id. NO TTL: prefs are a durable setting — a TTL here
  would silently switch an active user's buddy off while their chat sessions
  (which do carry a TTL) outlive the flag. ``day``/``day_count`` implement the
  per-user daily message quota, reset by date rollover.

Schema — collection ``buddy_sessions``:
  { user_id: int, session_key: str, messages: [{role, text, t}], touched: float,
    updated_at: datetime }
  unique index on (user_id, session_key); TTL on updated_at (90 days).
  ``session_key`` is derived from the opaque item reference the client sends:
  "m:<messageId>" when a specific upload is pinned, "i:<itemId>" for a bare
  item reference, "general" for the item-less AI-panel conversation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

_MESSAGE_CAP = 40    # max messages kept per session (oldest dropped)
_SESSION_CAP = 20    # max sessions kept per user (oldest-touched evicted)
_TTL_DAYS = 90
_indexed = False
# ponytail: single-process lock guards the append-then-evict below, matching
# cw_store. The deploy runs one instance; scale out → replace with a
# conditional Mongo update or transaction.
_mutation_lock = asyncio.Lock()


def session_key_for(item_id: Optional[str] = None,
                    message_id: Optional[int] = None) -> str:
    """Derive the chat-session key from the client's opaque item reference."""
    if message_id is not None:
        try:
            return f"m:{int(message_id)}"
        except (TypeError, ValueError):
            pass
    item = str(item_id or "").strip()
    if item:
        return f"i:{item[:120]}"
    return "general"


def _get_db():
    try:
        from main.utils import media_index as _mi
        s = _mi._store
        if s is None or not hasattr(s, "_client"):
            return None
        return s._client[s._db_name]
    except Exception:
        return None


async def _ensure_indexes() -> None:
    global _indexed
    if _indexed:
        return
    db = _get_db()
    if db is None:
        return
    try:
        prefs = db["buddy_prefs"]
        await prefs.create_index("user_id", unique=True)
        sessions = db["buddy_sessions"]
        await sessions.create_index([("user_id", 1), ("session_key", 1)], unique=True)
        await sessions.create_index([("user_id", 1), ("touched", -1)])
        await sessions.create_index("updated_at", expireAfterSeconds=_TTL_DAYS * 86400)
        _indexed = True
    except Exception:
        logging.exception("buddy_store: ensure_indexes failed")


async def get_enabled(user_id: int) -> bool:
    """Return the user's opt-in flag; False by default and on any failure."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        doc = await db["buddy_prefs"].find_one(
            {"user_id": user_id}, projection={"buddy_enabled": 1},
        )
        return bool(doc and doc.get("buddy_enabled"))
    except Exception:
        logging.exception("buddy_store: get_enabled failed uid=%d", user_id)
        return False


async def set_enabled(user_id: int, enabled: bool) -> bool:
    """Persist the opt-in flag. Returns False when the store is unavailable."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        await db["buddy_prefs"].update_one(
            {"user_id": user_id},
            {"$set": {"buddy_enabled": bool(enabled),
                      "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return True
    except Exception:
        logging.exception("buddy_store: set_enabled failed uid=%d", user_id)
        return False


async def get_history(user_id: int, session_key: str) -> list:
    """Return [{role, text, t}] oldest→newest, capped at _MESSAGE_CAP."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        doc = await db["buddy_sessions"].find_one(
            {"user_id": user_id, "session_key": session_key},
            projection={"messages": 1},
        )
        messages = (doc or {}).get("messages") or []
        return [
            {"role": m.get("role"), "text": m.get("text", ""), "t": m.get("t", 0)}
            for m in messages[-_MESSAGE_CAP:]
            if m.get("role") in ("user", "buddy") and m.get("text")
        ]
    except Exception:
        logging.exception("buddy_store: get_history failed uid=%d key=%s",
                          user_id, session_key)
        return []


async def append_exchange(user_id: int, session_key: str,
                          user_text: str, buddy_text: str) -> bool:
    """Atomically append a user+buddy pair so history never dangles one side.

    A single $push/$each/$slice keeps the cap at _MESSAGE_CAP without a
    read-modify-write round-trip. Returns False when nothing was persisted.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        now = time.time()
        pair = [
            {"role": "user", "text": user_text, "t": int(now)},
            {"role": "buddy", "text": buddy_text, "t": int(now)},
        ]
        async with _mutation_lock:
            await db["buddy_sessions"].update_one(
                {"user_id": user_id, "session_key": session_key},
                {
                    "$push": {"messages": {"$each": pair, "$slice": -_MESSAGE_CAP}},
                    "$set": {"touched": now,
                             "updated_at": datetime.now(timezone.utc)},
                    "$setOnInsert": {"user_id": user_id, "session_key": session_key},
                },
                upsert=True,
            )
            # Evict sessions beyond the cap, oldest-touched first.
            cursor = db["buddy_sessions"].find(
                {"user_id": user_id},
                projection={"session_key": 1, "touched": 1},
                sort=[("touched", -1)],
            ).skip(_SESSION_CAP)
            stale = await cursor.to_list(length=_SESSION_CAP)
            if stale:
                await db["buddy_sessions"].delete_many({
                    "user_id": user_id,
                    "session_key": {"$in": [d["session_key"] for d in stale]},
                })
        return True
    except Exception:
        logging.exception("buddy_store: append_exchange failed uid=%d key=%s",
                          user_id, session_key)
        return False


async def consume_daily(user_id: int, limit: int) -> bool:
    """Consume one message from the user's daily quota. False when exhausted.

    The counter lives on the prefs doc keyed by UTC date, so the quota resets
    on date rollover without any sweeper. Called after the flag check, so the
    store is expected to be reachable; failures deny (consistent with the
    flag's fail-closed behaviour).
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        now = datetime.now(timezone.utc)
        async with _mutation_lock:
            prefs = db["buddy_prefs"]
            doc = await prefs.find_one(
                {"user_id": user_id}, projection={"day": 1, "day_count": 1},
            )
            if doc and doc.get("day") == today:
                if int(doc.get("day_count", 0) or 0) >= limit:
                    return False
                await prefs.update_one(
                    {"user_id": user_id},
                    {"$inc": {"day_count": 1}, "$set": {"updated_at": now}},
                )
            else:
                await prefs.update_one(
                    {"user_id": user_id},
                    {"$set": {"day": today, "day_count": 1, "updated_at": now}},
                    upsert=True,
                )
        return True
    except Exception:
        logging.exception("buddy_store: consume_daily failed uid=%d", user_id)
        return False


async def delete_history(user_id: int, session_key: Optional[str] = None) -> bool:
    """Delete one session, or every session when ``session_key`` is None."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        if session_key is None:
            await db["buddy_sessions"].delete_many({"user_id": user_id})
        else:
            await db["buddy_sessions"].delete_one(
                {"user_id": user_id, "session_key": session_key},
            )
        return True
    except Exception:
        logging.exception("buddy_store: delete_history failed uid=%d key=%s",
                          user_id, session_key)
        return False


def is_available() -> bool:
    return _get_db() is not None

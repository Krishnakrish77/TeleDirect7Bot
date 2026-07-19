"""Per-user cache for AI recommendations — stores the resolved item list.

TTL ~8h. Keyed by user_id. Falls back gracefully when MongoDB is absent.
Mirrors rec_store.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

_TTL_SECONDS = 8 * 3600
_indexed = False


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
        coll = db["ai_recommendations"]
        await coll.create_index("user_id", unique=True)
        await coll.create_index("cached_at", expireAfterSeconds=_TTL_SECONDS)
        _indexed = True
    except Exception:
        logging.exception("ai_rec_store: ensure_indexes failed")


async def get_cached(user_id: int) -> Optional[list]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db["ai_recommendations"].find_one({"user_id": user_id})
        return doc.get("items") if doc else None
    except Exception:
        logging.exception("ai_rec_store: get_cached failed for user %d", user_id)
        return None


async def set_cached(user_id: int, items: list) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["ai_recommendations"].update_one(
            {"user_id": user_id},
            {"$set": {"items": items, "cached_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception:
        logging.exception("ai_rec_store: set_cached failed for user %d", user_id)


async def clear_cached(user_id: int) -> None:
    db = _get_db()
    if db is None:
        return
    try:
        await db["ai_recommendations"].delete_one({"user_id": user_id})
    except Exception:
        logging.exception("ai_rec_store: clear_cached failed for user %d", user_id)

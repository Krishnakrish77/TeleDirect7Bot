"""Recommendations cache — stores resolved (tmdb_id, kind) tuples per user.

TTL: 24 hours. Keyed by user_id. Falls back gracefully when MongoDB absent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

_TTL_SECONDS = 24 * 3600
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
        coll = db["recommendations"]
        await coll.create_index("user_id", unique=True)
        await coll.create_index("cached_at", expireAfterSeconds=_TTL_SECONDS)
        _indexed = True
    except Exception:
        logging.exception("rec_store: ensure_indexes failed")


async def get_cached(user_id: int) -> Optional[List[Tuple[int, str]]]:
    """Return cached [(tmdb_id, kind)] if present, else None."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db["recommendations"].find_one({"user_id": user_id})
        if not doc:
            return None
        return [(int(r["id"]), r["kind"]) for r in doc.get("items", [])]
    except Exception:
        logging.exception("rec_store: get_cached failed for user %d", user_id)
        return None


async def clear_cached(user_id: int) -> None:
    """Delete the cache entry so the next request regenerates recommendations."""
    db = _get_db()
    if db is None:
        return
    try:
        await db["recommendations"].delete_one({"user_id": user_id})
    except Exception:
        logging.exception("rec_store: clear_cached failed for user %d", user_id)


async def set_cached(user_id: int, items: List[Tuple[int, str]]) -> None:
    """Upsert the recommendation cache for the user."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        await db["recommendations"].update_one(
            {"user_id": user_id},
            {"$set": {"items": [{"id": tid, "kind": k} for tid, k in items],
                      "cached_at": now}},
            upsert=True,
        )
    except Exception:
        logging.exception("rec_store: set_cached failed for user %d", user_id)

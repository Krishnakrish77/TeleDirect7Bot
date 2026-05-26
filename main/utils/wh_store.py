"""Watch-history persistence — records completed video views.

Schema — collection ``watch_history``:
  { user_id: int, cw_key: str, title: str, watched_at: datetime }
  unique index on (user_id, cw_key) — re-watching the same item updates watched_at.
  TTL index on watched_at: 365 days.
  Per-user cap: 200 entries (oldest evicted on overflow).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

_WH_CAP = 200
_TTL_DAYS = 365
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
        coll = db["watch_history"]
        await coll.create_index([("user_id", 1), ("cw_key", 1)], unique=True)
        await coll.create_index([("user_id", 1), ("watched_at", -1)])
        await coll.create_index("watched_at", expireAfterSeconds=_TTL_DAYS * 86400)
        _indexed = True
    except Exception:
        logging.exception("wh_store: ensure_indexes failed")


async def record(user_id: int, cw_key: str, title: str) -> None:
    """Record or refresh a completed watch. Idempotent — re-watching updates timestamp."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        await db["watch_history"].update_one(
            {"user_id": user_id, "cw_key": cw_key},
            {"$set": {"title": title, "watched_at": now}},
            upsert=True,
        )
        # Evict oldest beyond cap — same skip-based approach as cw_store
        cursor = db["watch_history"].find(
            {"user_id": user_id},
            projection={"_id": 1, "watched_at": 1},
            sort=[("watched_at", -1)],
        ).skip(_WH_CAP)
        cutoffs = await cursor.to_list(length=1)
        if cutoffs:
            cutoff_dt = cutoffs[0]["watched_at"]
            await db["watch_history"].delete_many(
                {"user_id": user_id,
                 "watched_at": {"$lte": cutoff_dt},
                 "_id": {"$ne": cutoffs[0]["_id"]}}
            )
    except Exception:
        logging.exception("wh_store: record failed uid=%d key=%s", user_id, cw_key)


async def get_recent(user_id: int, limit: int = 20) -> list:
    """Return [{cw_key, title, watched_at}] newest-first."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        docs = await db["watch_history"].find(
            {"user_id": user_id},
            projection={"cw_key": 1, "title": 1, "watched_at": 1, "_id": 0},
            sort=[("watched_at", -1)],
        ).to_list(length=limit)
        return docs
    except Exception:
        logging.exception("wh_store: get_recent failed for user %d", user_id)
        return []


def is_available() -> bool:
    return _get_db() is not None

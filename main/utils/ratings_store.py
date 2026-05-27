"""Per-title user ratings (thumbs up / thumbs down).

Schema — collection ``ratings``:
  { user_id: int, message_id: int, rating: str ("up"|"down"), rated_at: datetime }
  unique index on (user_id, message_id)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

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
        coll = db["ratings"]
        await coll.create_index([("user_id", 1), ("message_id", 1)], unique=True)
        await coll.create_index("message_id")
        _indexed = True
    except Exception:
        logging.exception("ratings_store: ensure_indexes failed")


async def set_rating(user_id: int, message_id: int, rating: str) -> None:
    """Set a rating ('up' or 'down'). Upserts."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        await db["ratings"].update_one(
            {"user_id": user_id, "message_id": message_id},
            {"$set": {"rating": rating, "rated_at": now}},
            upsert=True,
        )
    except Exception:
        logging.exception("ratings_store: set_rating failed uid=%d mid=%d", user_id, message_id)


async def delete_rating(user_id: int, message_id: int) -> None:
    """Remove a rating (toggle off)."""
    db = _get_db()
    if db is None:
        return
    try:
        await db["ratings"].delete_one({"user_id": user_id, "message_id": message_id})
    except Exception:
        logging.exception("ratings_store: delete_rating failed uid=%d mid=%d", user_id, message_id)


async def get_rating(user_id: int, message_id: int) -> Optional[str]:
    """Return 'up', 'down', or None."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db["ratings"].find_one(
            {"user_id": user_id, "message_id": message_id},
            projection={"rating": 1, "_id": 0},
        )
        return doc["rating"] if doc else None
    except Exception:
        logging.exception("ratings_store: get_rating failed")
        return None


async def get_counts(message_id: int) -> Dict[str, int]:
    """Return {"up": N, "down": N} aggregate counts for a title."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return {"up": 0, "down": 0}
    try:
        pipeline = [
            {"$match": {"message_id": message_id}},
            {"$group": {"_id": "$rating", "n": {"$sum": 1}}},
        ]
        docs = await db["ratings"].aggregate(pipeline).to_list(length=5)
        result = {"up": 0, "down": 0}
        for d in docs:
            if d["_id"] in result:
                result[d["_id"]] = d["n"]
        return result
    except Exception:
        logging.exception("ratings_store: get_counts failed for mid=%d", message_id)
        return {"up": 0, "down": 0}


def is_available() -> bool:
    return _get_db() is not None

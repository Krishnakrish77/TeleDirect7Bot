"""Watchlist persistence backed by the same MongoDB database as the catalogue.

Reuses the Motor client already held by the MongoStore so we don't open a
second connection pool. Falls back gracefully (empty lists, no-ops) when
MongoDB is not configured.

Schema — collection ``watchlist``:
  { user_id: int, item_id: str, added_at: datetime }
  unique index on (user_id, item_id)

item_id encoding:
  "<digits>"          — individual HubItem (message_id as string)
  "movie:<key>"       — MovieGroup
  "series:<key>"      — SeriesGroup
  "album:<key>"       — AlbumGroup
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional


def _get_db():
    """Return the Motor database object if MongoDB backend is active."""
    try:
        from main.utils import media_index as _mi
        s = _mi._store
        if s is None or not hasattr(s, "_client"):
            return None
        return s._client[s._db_name]
    except Exception:
        return None


_indexed = False
_WL_CAP = 1000  # max bookmarks per user


async def _ensure_indexes() -> None:
    global _indexed
    if _indexed:
        return
    db = _get_db()
    if db is None:
        return
    try:
        coll = db["watchlist"]
        await coll.create_index([("user_id", 1), ("item_id", 1)], unique=True)
        await coll.create_index("user_id")
        _indexed = True
    except Exception:
        logging.exception("watchlist: ensure_indexes failed")


async def get_ids(user_id: int) -> List[str]:
    """Return all saved item_ids for the user, newest first."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        cursor = db["watchlist"].find(
            {"user_id": user_id},
            projection={"item_id": 1, "_id": 0},
            sort=[("added_at", -1)],
        )
        docs = await cursor.to_list(length=_WL_CAP)
        return [d["item_id"] for d in docs]
    except Exception:
        logging.exception("watchlist: get_ids failed for user %d", user_id)
        return []


async def get_entries(user_id: int) -> List[dict]:
    """Return saved IDs with their optional explicit completed state."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        cursor = db["watchlist"].find(
            {"user_id": user_id},
            projection={"item_id": 1, "completed_at": 1, "_id": 0},
            sort=[("added_at", -1)],
        )
        return await cursor.to_list(length=_WL_CAP)
    except Exception:
        logging.exception("watchlist: get_entries failed for user %d", user_id)
        return []


async def mark_completed(user_id: int, item_id: str) -> bool:
    """Mark one saved title completed without inventing individual play events."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["watchlist"].update_one(
            {"user_id": user_id, "item_id": item_id},
            {"$set": {"completed_at": datetime.now(timezone.utc)}},
        )
        return bool(result.matched_count)
    except Exception:
        logging.exception("watchlist: mark_completed failed uid=%d iid=%s", user_id, item_id)
        return False


async def add(user_id: int, item_id: str) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        await db["watchlist"].update_one(
            {"user_id": user_id, "item_id": item_id},
            {"$setOnInsert": {"user_id": user_id, "item_id": item_id, "added_at": now}},
            upsert=True,
        )
        # Evict oldest beyond cap using _id-only approach (no timestamp collisions)
        keep_cursor = db["watchlist"].find(
            {"user_id": user_id},
            projection={"_id": 1},
            sort=[("added_at", -1)],
        ).limit(_WL_CAP)
        keep_docs = await keep_cursor.to_list(length=_WL_CAP)
        if len(keep_docs) == _WL_CAP:
            keep_ids = [d["_id"] for d in keep_docs]
            await db["watchlist"].delete_many(
                {"user_id": user_id, "_id": {"$nin": keep_ids}}
            )
    except Exception:
        logging.exception("watchlist: add failed uid=%d iid=%s", user_id, item_id)


async def remove(user_id: int, item_id: str) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["watchlist"].delete_one({"user_id": user_id, "item_id": item_id})
    except Exception:
        logging.exception("watchlist: remove failed uid=%d iid=%s", user_id, item_id)


def is_available() -> bool:
    return _get_db() is not None

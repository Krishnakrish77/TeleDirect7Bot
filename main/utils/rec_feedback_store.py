"""Private per-user feedback for recommendation quality.

Events are deliberately minimal: no query text, titles, or device identifiers.
They describe only how a signed-in user engaged with a card already present in
their own library. Raw events expire automatically after 180 days.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

_TTL_SECONDS = 180 * 24 * 3600
_indexed = False


def _get_db():
    try:
        from main.utils import media_index as _mi
        store = _mi._store
        if store is None or not hasattr(store, "_client"):
            return None
        return store._client[store._db_name]
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
        collection = db["recommendation_feedback"]
        await collection.create_index([("user_id", 1), ("created_at", -1)])
        await collection.create_index([("user_id", 1), ("action", 1), ("created_at", -1)])
        await collection.create_index("created_at", expireAfterSeconds=_TTL_SECONDS)
        _indexed = True
    except Exception:
        logging.exception("rec_feedback_store: ensure_indexes failed")


async def record_many(user_id: int, events: list[dict]) -> int:
    """Persist validated feedback events, returning the accepted count."""
    if not events:
        return 0
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    now = datetime.now(timezone.utc)
    docs = [{**event, "user_id": user_id, "created_at": now} for event in events]
    try:
        result = await db["recommendation_feedback"].insert_many(docs, ordered=False)
        return len(result.inserted_ids)
    except Exception:
        logging.exception("rec_feedback_store: record_many failed uid=%d", user_id)
        return 0


async def get_recent_opens(user_id: int, limit: int = 100) -> list[dict]:
    """Return affirmative recommendation engagements for future ranking."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        return await db["recommendation_feedback"].find(
            {"user_id": user_id, "action": {"$in": ["open", "play", "save"]}},
            projection={"item_id": 1, "tmdb_id": 1, "tmdb_kind": 1, "action": 1, "created_at": 1, "_id": 0},
            sort=[("created_at", -1)],
        ).to_list(length=max(1, min(int(limit), 300)))
    except Exception:
        logging.exception("rec_feedback_store: get_recent_opens failed uid=%d", user_id)
        return []

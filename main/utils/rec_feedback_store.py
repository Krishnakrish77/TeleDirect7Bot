"""Private per-user feedback for recommendation quality.

Events are deliberately minimal: no query text, titles, or device identifiers.
They describe only how a signed-in user engaged with a card already present in
their own library. Raw events expire automatically after 180 days.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

_TTL_SECONDS = 180 * 24 * 3600
_MAX_EVENTS_PER_MINUTE = 120
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
        # Existing pre-idempotency documents have no event_key. A partial index
        # keeps this migration safe while enforcing uniqueness for new writes.
        await collection.create_index(
            [("user_id", 1), ("event_key", 1)],
            unique=True,
            partialFilterExpression={"event_key": {"$type": "string"}},
        )
        await collection.create_index("created_at", expireAfterSeconds=_TTL_SECONDS)
        _indexed = True
    except Exception:
        logging.exception("rec_feedback_store: ensure_indexes failed")


async def record_many(user_id: int, events: list[dict]) -> int:
    """Persist bounded, idempotent feedback events and return new-event count."""
    if not events:
        return 0
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    now = datetime.now(timezone.utc)
    collection = db["recommendation_feedback"]
    try:
        recent = await collection.count_documents({
            "user_id": user_id,
            "created_at": {"$gte": now - timedelta(minutes=1)},
        })
        remaining = max(0, _MAX_EVENTS_PER_MINUTE - recent)
        if not remaining:
            return 0
        accepted = 0
        for event in events[:remaining]:
            # Position is presentation data, not a distinct engagement. It
            # changes when cards are dismissed and would defeat deduplication.
            fingerprint = {key: value for key, value in event.items() if key != "position"}
            canonical = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            event_key = hashlib.sha256(f"{user_id}:{canonical}".encode()).hexdigest()
            result = await collection.update_one(
                {"user_id": user_id, "event_key": event_key},
                {"$setOnInsert": {**event, "user_id": user_id, "event_key": event_key, "created_at": now}},
                upsert=True,
            )
            if result.upserted_id is not None:
                accepted += 1
        return accepted
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

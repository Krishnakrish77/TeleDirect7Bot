"""Watch-history persistence — records completed video views.

Schema — collection ``watch_history``:
  { user_id: int, cw_key: str, title: str, watched_at: datetime }
  unique index on (user_id, cw_key) — re-watching the same item updates watched_at.
  TTL index on watched_at: 365 days.
  Per-user cap: 200 entries (oldest evicted on overflow).

``watch_events`` complements that compact per-title summary with one immutable
completion event per play. Stats use it for accurate day/month activity while
the existing history collection remains the fast source for resume and hub
features.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

_WH_CAP = 200
_TTL_DAYS = 365
_EVENT_CAP = 5_000
_EVENTS = "watch_events"
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
        events = db[_EVENTS]
        await events.create_index([("user_id", 1), ("watched_at", -1)])
        await events.create_index("watched_at", expireAfterSeconds=_TTL_DAYS * 86400)
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
        title = title[:200]
        now = datetime.now(timezone.utc)
        await db["watch_history"].update_one(
            {"user_id": user_id, "cw_key": cw_key},
            {"$set": {"title": title, "watched_at": now},
             "$inc": {"play_count": 1}},
            upsert=True,
        )
        await db[_EVENTS].insert_one({
            "user_id": user_id,
            "cw_key": cw_key,
            "title": title,
            "watched_at": now,
        })
        # Evict oldest beyond cap: fetch the IDs to keep, delete everything else.
        # _id-only approach avoids timestamp-collision false deletions.
        keep_cursor = db["watch_history"].find(
            {"user_id": user_id},
            projection={"_id": 1},
            sort=[("watched_at", -1)],
        ).limit(_WH_CAP)
        keep_docs = await keep_cursor.to_list(length=_WH_CAP)
        if len(keep_docs) == _WH_CAP:
            keep_ids = [d["_id"] for d in keep_docs]
            await db["watch_history"].delete_many(
                {"user_id": user_id, "_id": {"$nin": keep_ids}}
            )
        # Keep event storage bounded even for a user who loops a short track
        # all day. The time TTL remains the primary retention policy.
        event_cursor = db[_EVENTS].find(
            {"user_id": user_id}, projection={"_id": 1, "watched_at": 1},
            sort=[("watched_at", -1)],
        ).skip(_EVENT_CAP)
        event_cutoff = await event_cursor.to_list(length=1)
        if event_cutoff:
            await db[_EVENTS].delete_many({
                "user_id": user_id,
                "watched_at": {"$lte": event_cutoff[0]["watched_at"]},
            })
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
            projection={"cw_key": 1, "title": 1, "watched_at": 1, "play_count": 1, "_id": 0},
            sort=[("watched_at", -1)],
        ).to_list(length=limit)
        return docs
    except Exception:
        logging.exception("wh_store: get_recent failed for user %d", user_id)
        return []


async def get_events(user_id: int, limit: int = _EVENT_CAP) -> list:
    """Return immutable completion events, newest first, for statistics."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        return await db[_EVENTS].find(
            {"user_id": user_id},
            projection={"cw_key": 1, "title": 1, "watched_at": 1, "_id": 0},
            sort=[("watched_at", -1)],
        ).to_list(length=max(1, min(int(limit), _EVENT_CAP)))
    except Exception:
        logging.exception("wh_store: get_events failed for user %d", user_id)
        return []


_top_plays_cache: dict = {"data": None, "at": 0.0}
_TOP_PLAYS_TTL = 4 * 3600  # 4 hours
_top_plays_refresh_task: "asyncio.Task | None" = None


async def get_top_plays(limit: int = 20) -> list:
    """Return [{cw_key, play_count}] sorted by total plays across all users.

    Stale-while-revalidate: the aggregation regularly outlives the hub's
    1 s shelf budget on slow Mongo links, and a plain cache held no value
    until the first *successful* run — so the shelf could stay missing
    forever (every request times out, cancels the aggregate, and the next
    one starts from scratch). Now a timeout serves the last known result
    (even an expired one) and lets the in-flight refresh finish in the
    background so the cache warms for subsequent loads.
    """
    global _top_plays_cache, _top_plays_refresh_task
    fresh = time.time() - _top_plays_cache["at"] < _TOP_PLAYS_TTL
    if fresh and _top_plays_cache["data"] is not None:
        return _top_plays_cache["data"][:limit]
    # Expired or never-filled: refresh at most one aggregation at a time.
    if _top_plays_refresh_task is None or _top_plays_refresh_task.done():
        _top_plays_refresh_task = asyncio.create_task(_refresh_top_plays())
    if _top_plays_cache["data"] is not None:
        # Stale but present — answer immediately; refresh completes meanwhile.
        return _top_plays_cache["data"][:limit]
    # Never ran: await the refresh with a hard ceiling so the hub's own
    # timeout fires first and cancels us, not the refresh task.
    # Polling instead of asyncio.wait_for: when the caller is cancelled
    # while suspended in wait_for(shield(...)), CPython 3.13+/3.14 can
    # discard the wait_for coroutine un-awaited (RuntimeWarning spam).
    # sleep() is cancellation-safe, and this path only runs on a cold cache.
    deadline = time.time() + 0.9
    while True:
        if _top_plays_refresh_task.done():
            break
        if time.time() >= deadline:
            return (_top_plays_cache["data"] or [])[:limit]
        await asyncio.sleep(0.05)
    try:
        return (await asyncio.shield(_top_plays_refresh_task))[:limit]
    except (asyncio.TimeoutError, Exception):
        return (_top_plays_cache["data"] or [])[:limit]


async def _refresh_top_plays() -> list:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        pipeline = [
            {"$group": {
                "_id": "$cw_key",
                "play_count": {"$sum": "$play_count"},
            }},
            {"$sort": {"play_count": -1}},
            {"$limit": 120},  # over-fetch so dedup in caller still has enough
        ]
        docs = await db["watch_history"].aggregate(pipeline).to_list(length=120)
        result = [{"cw_key": d["_id"], "play_count": d["play_count"]} for d in docs]
        global _top_plays_cache
        _top_plays_cache = {"data": result, "at": time.time()}
        return result
    except Exception:
        logging.exception("wh_store: get_top_plays failed")
        return []


def is_available() -> bool:
    return _get_db() is not None

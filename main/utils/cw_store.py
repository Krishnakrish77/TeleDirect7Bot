"""Continue-Watching persistence — MongoDB-backed, reuses the catalogue Motor client.

Schema — collection ``continue_watching``:
  { user_id: int, cw_key: str, pos: float, dur: float, t: int (epoch-ms),
    title: str, updated_at: datetime }
  unique index on (user_id, cw_key); TTL on updated_at (90 days).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict

_CW_CAP = 50        # max entries kept per user (oldest evicted)
_TTL_DAYS = 90
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
        coll = db["continue_watching"]
        await coll.create_index([("user_id", 1), ("cw_key", 1)], unique=True)
        await coll.create_index([("user_id", 1), ("t", -1)])
        await coll.create_index("updated_at", expireAfterSeconds=_TTL_DAYS * 86400)
        _indexed = True
    except Exception:
        logging.exception("cw_store: ensure_indexes failed")


async def get_all(user_id: int) -> Dict[str, dict]:
    """Return {cw_key: {pos, dur, t, title}} newest-first, capped at _CW_CAP."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return {}
    try:
        docs = await db["continue_watching"].find(
            {"user_id": user_id},
            projection={"cw_key": 1, "pos": 1, "dur": 1, "t": 1, "title": 1, "_id": 0},
            sort=[("t", -1)],
        ).to_list(length=_CW_CAP)
        return {
            d["cw_key"]: {
                "pos": d.get("pos", 0),
                "dur": d.get("dur", 0),
                "t": d.get("t", 0),
                "title": d.get("title", ""),
            }
            for d in docs
        }
    except Exception:
        logging.exception("cw_store: get_all failed for user %d", user_id)
        return {}


async def upsert(user_id: int, cw_key: str, pos: float, dur: float,
                 t: int, title: str) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        await db["continue_watching"].update_one(
            {"user_id": user_id, "cw_key": cw_key},
            {"$set": {"pos": pos, "dur": dur, "t": t,
                      "title": title, "updated_at": now}},
            upsert=True,
        )
        # Evict entries beyond the cap: fetch the (_CW_CAP+1)th oldest id and
        # delete everything older. Single-pass, no separate count round-trip.
        cursor = db["continue_watching"].find(
            {"user_id": user_id},
            projection={"_id": 1, "t": 1},
            sort=[("t", -1)],
        ).skip(_CW_CAP)
        cutoffs = await cursor.to_list(length=1)
        if cutoffs:
            cutoff_t = cutoffs[0]["t"]
            await db["continue_watching"].delete_many(
                {"user_id": user_id, "t": {"$lte": cutoff_t},
                 "_id": {"$ne": cutoffs[0]["_id"]}}
            )
    except Exception:
        logging.exception("cw_store: upsert failed uid=%d key=%s", user_id, cw_key)


async def delete_one(user_id: int, cw_key: str) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["continue_watching"].delete_one({"user_id": user_id, "cw_key": cw_key})
    except Exception:
        logging.exception("cw_store: delete_one failed uid=%d key=%s", user_id, cw_key)


async def delete_all(user_id: int) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["continue_watching"].delete_many({"user_id": user_id})
    except Exception:
        logging.exception("cw_store: delete_all failed uid=%d", user_id)


def is_available() -> bool:
    return _get_db() is not None

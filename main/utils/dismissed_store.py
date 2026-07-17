"""Per-user dismissed recommendation records.

When a user taps "Not for me" on a rec card, we store the tmdb_id so
the rec engine excludes it from future recommendations.

Schema — collection ``dismissed``:
  { user_id: int, tmdb_id: int, kind: str ("movie"|"tv"),
    dismissed_at: datetime }
  unique index on (user_id, tmdb_id)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Set, Tuple

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
        coll = db["dismissed"]
        # Older releases keyed dismissals only by numeric TMDB ID. Movie and
        # TV IDs use separate namespaces, so remove that obsolete index before
        # creating the type-safe replacement. Existing documents retain their
        # stored kind and continue to work.
        try:
            await coll.drop_index("user_id_1_tmdb_id_1")
        except Exception:
            pass  # absent on fresh databases or already migrated
        await coll.create_index([("user_id", 1), ("tmdb_id", 1), ("kind", 1)], unique=True)
        await db["dismissed"].create_index("user_id")
        _indexed = True
    except Exception:
        logging.exception("dismissed_store: ensure_indexes failed")


async def dismiss(user_id: int, tmdb_id: int, kind: str) -> None:
    """Mark a title as 'not for me'."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        await db["dismissed"].update_one(
            {"user_id": user_id, "tmdb_id": tmdb_id, "kind": kind},
            {"$set": {"kind": kind, "dismissed_at": now}},
            upsert=True,
        )
    except Exception:
        logging.exception("dismissed_store: dismiss failed uid=%d tid=%d", user_id, tmdb_id)


async def undismiss(user_id: int, tmdb_id: int, kind: str = "") -> None:
    """Remove a dismissal (toggle off)."""
    db = _get_db()
    if db is None:
        return
    try:
        query = {"user_id": user_id, "tmdb_id": tmdb_id}
        if kind in ("movie", "tv"):
            query["kind"] = kind
        await db["dismissed"].delete_one(query)
    except Exception:
        logging.exception("dismissed_store: undismiss failed uid=%d tid=%d", user_id, tmdb_id)


async def get_dismissed_ids(user_id: int) -> Set[Tuple[int, str]]:
    """Return all ``(tmdb_id, kind)`` pairs the user has dismissed."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return set()
    try:
        docs = await db["dismissed"].find(
            {"user_id": user_id},
            projection={"tmdb_id": 1, "kind": 1, "_id": 0},
        ).to_list(length=2000)
        return {
            (int(d["tmdb_id"]), str(d.get("kind") or "movie"))
            for d in docs if d.get("tmdb_id")
        }
    except Exception:
        logging.exception("dismissed_store: get_dismissed_ids failed for user %d", user_id)
        return set()

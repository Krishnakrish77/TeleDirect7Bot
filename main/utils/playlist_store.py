"""Playlist persistence — user-created ordered collections of audio tracks.

Schema — collection ``playlists``:
  {
    user_id: int,
    playlist_id: str,        # UUID hex (32 chars)
    name: str,
    tracks: [
      {message_id: int, secure_hash: str, title: str, artist: str, added_at: datetime}
    ],
    created_at: datetime,
    updated_at: datetime,
  }
  Unique index on (user_id, playlist_id).
  Per-user cap: 50 playlists, 500 tracks per playlist.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

_MAX_PLAYLISTS = 50
_MAX_TRACKS = 500
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
        coll = db["playlists"]
        await coll.create_index([("user_id", 1), ("playlist_id", 1)], unique=True)
        await coll.create_index([("user_id", 1), ("updated_at", -1)])
        _indexed = True
    except Exception:
        logging.exception("playlist_store: ensure_indexes failed")


async def create(user_id: int, name: str) -> Optional[str]:
    """Create a new playlist. Returns playlist_id or None on failure/cap exceeded."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    count = await db["playlists"].count_documents({"user_id": user_id})
    if count >= _MAX_PLAYLISTS:
        return None
    playlist_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    try:
        await db["playlists"].insert_one({
            "user_id": user_id,
            "playlist_id": playlist_id,
            "name": name[:100].strip() or "Untitled",
            "tracks": [],
            "created_at": now,
            "updated_at": now,
        })
        return playlist_id
    except Exception:
        logging.exception("playlist_store: create failed uid=%d", user_id)
        return None


async def get_all(user_id: int) -> List[dict]:
    """Return all playlists newest-first. Each entry has track_count."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        docs = await db["playlists"].find(
            {"user_id": user_id},
            projection={"playlist_id": 1, "name": 1, "tracks": 1, "updated_at": 1, "_id": 0},
            sort=[("updated_at", -1)],
        ).to_list(length=_MAX_PLAYLISTS)
        return [
            {
                "playlist_id": d["playlist_id"],
                "name": d["name"],
                "track_count": len(d.get("tracks") or []),
                "first_track": ((d.get("tracks") or [])[0]) if d.get("tracks") else None,
                "updated_at": d.get("updated_at"),
            }
            for d in docs
        ]
    except Exception:
        logging.exception("playlist_store: get_all failed uid=%d", user_id)
        return []


async def get_one(user_id: int, playlist_id: str) -> Optional[dict]:
    """Return the full playlist document including tracks, or None."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        return await db["playlists"].find_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            projection={"_id": 0},
        )
    except Exception:
        logging.exception("playlist_store: get_one failed uid=%d pid=%s", user_id, playlist_id)
        return None


async def add_track(user_id: int, playlist_id: str,
                    message_id: int, secure_hash: str,
                    title: str, artist: str) -> bool:
    """Add a track. Re-adding an existing track moves it to the end. True on success."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        # Remove any existing entry for the same message_id first (idempotent)
        await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            {"$pull": {"tracks": {"message_id": message_id}}},
        )
        now = datetime.now(timezone.utc)
        result = await db["playlists"].update_one(
            {
                "user_id": user_id,
                "playlist_id": playlist_id,
                # Enforce max tracks: only update if there's room
                f"tracks.{_MAX_TRACKS - 1}": {"$exists": False},
            },
            {
                "$push": {"tracks": {
                    "message_id": message_id,
                    "secure_hash": secure_hash,
                    "title": title[:200],
                    "artist": artist[:200],
                    "added_at": now,
                }},
                "$set": {"updated_at": now},
            },
        )
        return result.modified_count > 0
    except Exception:
        logging.exception("playlist_store: add_track failed uid=%d", user_id)
        return False


async def remove_track(user_id: int, playlist_id: str, message_id: int) -> bool:
    """Remove a track from a playlist."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            {
                "$pull": {"tracks": {"message_id": message_id}},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )
        return result.modified_count > 0
    except Exception:
        logging.exception("playlist_store: remove_track failed uid=%d", user_id)
        return False


async def rename(user_id: int, playlist_id: str, new_name: str) -> bool:
    """Rename a playlist."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            {"$set": {"name": new_name[:100].strip() or "Untitled",
                      "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count > 0
    except Exception:
        logging.exception("playlist_store: rename failed uid=%d", user_id)
        return False


async def delete(user_id: int, playlist_id: str) -> bool:
    """Delete a playlist and all its tracks."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["playlists"].delete_one(
            {"user_id": user_id, "playlist_id": playlist_id}
        )
        return result.deleted_count > 0
    except Exception:
        logging.exception("playlist_store: delete failed uid=%d", user_id)
        return False


def is_available() -> bool:
    return _get_db() is not None

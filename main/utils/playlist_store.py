"""Playlist persistence for user-owned ordered audio collections.

Collection ``playlists``:
  {
    user_id: int,
    playlist_id: str,
    name: str,
    tracks: [
      {message_id: int, secure_hash: str, title: str, artist: str, added_at: datetime}
    ],
    created_at: datetime,
    updated_at: datetime,
  }
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

_MAX_PLAYLISTS = 50
_MAX_TRACKS = 500
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
        coll = db["playlists"]
        await coll.create_index([("user_id", 1), ("playlist_id", 1)], unique=True)
        await coll.create_index([("user_id", 1), ("updated_at", -1)])
        _indexed = True
    except Exception:
        logging.exception("playlist_store: ensure_indexes failed")


def is_available() -> bool:
    return _get_db() is not None


async def create(user_id: int, name: str) -> Optional[str]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        count = await db["playlists"].count_documents({"user_id": user_id})
        if count >= _MAX_PLAYLISTS:
            return None
        now = datetime.now(timezone.utc)
        playlist_id = uuid.uuid4().hex
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


async def get_all(user_id: int) -> list[dict]:
    """Return compact playlist summaries newest-first.

    The aggregation avoids transferring full track arrays for the library page.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$sort": {"updated_at": -1}},
            {"$project": {
                "_id": 0,
                "playlist_id": 1,
                "name": 1,
                "created_at": 1,
                "updated_at": 1,
                "track_count": {"$size": {"$ifNull": ["$tracks", []]}},
                "cover_tracks": {"$slice": [{"$ifNull": ["$tracks", []]}, 4]},
            }},
            {"$limit": _MAX_PLAYLISTS},
        ]
        return await db["playlists"].aggregate(pipeline).to_list(length=_MAX_PLAYLISTS)
    except Exception:
        logging.exception("playlist_store: get_all failed uid=%d", user_id)
        return []


async def get_one(user_id: int, playlist_id: str) -> Optional[dict]:
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


async def add_track(
    user_id: int,
    playlist_id: str,
    message_id: int,
    secure_hash: str,
    title: str,
    artist: str,
) -> bool:
    """Add a track atomically. Re-adding an existing track moves it to the end."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        now = datetime.now(timezone.utc)
        new_track = {
            "message_id": message_id,
            "secure_hash": secure_hash,
            "title": title[:200],
            "artist": artist[:200],
            "added_at": now,
        }
        _has_room = {"$lt": [
            {"$size": {"$filter": {
                "input": {"$ifNull": ["$tracks", []]},
                "as": "track",
                "cond": {"$ne": ["$$track.message_id", message_id]},
            }}},
            _MAX_TRACKS,
        ]}
        result = await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            [
                {
                    "$set": {
                        "tracks": {
                            "$let": {
                                "vars": {
                                    "without": {
                                        "$filter": {
                                            "input": {"$ifNull": ["$tracks", []]},
                                            "as": "track",
                                            "cond": {"$ne": ["$$track.message_id", message_id]},
                                        }
                                    }
                                },
                                "in": {
                                    "$cond": [
                                        {"$lt": [{"$size": "$$without"}, _MAX_TRACKS]},
                                        {"$concatArrays": ["$$without", [new_track]]},
                                        "$$without",
                                    ]
                                },
                            }
                        },
                        # Only advance updated_at when a track is actually appended.
                        # Using $$NOW so the timestamp matches when the write occurs.
                        "updated_at": {"$cond": [_has_room, "$$NOW", "$updated_at"]},
                    }
                }
            ],
        )
        return result.modified_count > 0
    except Exception:
        logging.exception("playlist_store: add_track failed uid=%d pid=%s", user_id, playlist_id)
        return False


async def remove_track(user_id: int, playlist_id: str, message_id: int) -> bool:
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
        logging.exception("playlist_store: remove_track failed uid=%d pid=%s", user_id, playlist_id)
        return False


async def reorder_tracks(user_id: int, playlist_id: str, message_ids: list[int]) -> bool:
    """Reorder known tracks and append any omitted existing tracks afterward."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        playlist = await get_one(user_id, playlist_id)
        if playlist is None:
            return False
        current = playlist.get("tracks") or []
        by_id = {int(track.get("message_id")): track for track in current if track.get("message_id") is not None}
        seen: set[int] = set()
        ordered: list[dict] = []
        for message_id in message_ids:
            if message_id in by_id and message_id not in seen:
                ordered.append(by_id[message_id])
                seen.add(message_id)
        ordered.extend(track for track in current if int(track.get("message_id", -1)) not in seen)
        result = await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            {"$set": {"tracks": ordered[:_MAX_TRACKS], "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count > 0
    except Exception:
        logging.exception("playlist_store: reorder_tracks failed uid=%d pid=%s", user_id, playlist_id)
        return False


async def rename(user_id: int, playlist_id: str, new_name: str) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["playlists"].update_one(
            {"user_id": user_id, "playlist_id": playlist_id},
            {"$set": {
                "name": new_name[:100].strip() or "Untitled",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return result.modified_count > 0
    except Exception:
        logging.exception("playlist_store: rename failed uid=%d pid=%s", user_id, playlist_id)
        return False


async def delete(user_id: int, playlist_id: str) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["playlists"].delete_one({"user_id": user_id, "playlist_id": playlist_id})
        return result.deleted_count > 0
    except Exception:
        logging.exception("playlist_store: delete failed uid=%d pid=%s", user_id, playlist_id)
        return False

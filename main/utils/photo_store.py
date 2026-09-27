"""Photos (Darkroom) persistence — per-user photo libraries in MongoDB.

Reuses the Motor client already held by the catalogue's MongoStore so we
don't open a second connection pool. Falls back gracefully (empty results,
no-ops) when MongoDB is not configured.

Privacy model: every document carries ``owner_user_id`` and every read in
``photo_routes.py`` filters on it server-side. Nothing here trusts a
client-supplied owner — callers must resolve it from the verified session.

Collections:
  photos        — one doc per ingested photo/video (metadata only; bytes
                  live in the user's Telegram channel)
  photo_channels— one bound channel per user (the byte vault)
  photo_albums  — pure metadata grouping
  photo_thumbs  — generated webp thumbs, keyed "{channel_id}:{message_id}:{size}"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_PAGE_CAP = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


async def _ensure_indexes() -> None:
    global _indexed
    if _indexed:
        return
    db = _get_db()
    if db is None:
        return
    try:
        photos = db["photos"]
        await photos.create_index([("owner_user_id", 1), ("taken_at", -1)])
        await photos.create_index(
            [("owner_user_id", 1), ("sha256", 1)], unique=True, sparse=True
        )
        await photos.create_index([("owner_user_id", 1), ("favorite", 1)])
        await photos.create_index([("owner_user_id", 1), ("deleted", 1), ("taken_at", -1)])
        await photos.create_index(
            [("channel_id", 1), ("message_id", 1)], unique=True
        )
        channels = db["photo_channels"]
        await channels.create_index("channel_id", unique=True)
        await channels.create_index("owner_user_id", unique=True)
        albums = db["photo_albums"]
        await albums.create_index([("owner_user_id", 1), ("created_at", 1)])
        thumbs = db["photo_thumbs"]
        await thumbs.create_index("key", unique=True)
        await thumbs.create_index("owner_user_id")
        _indexed = True
    except Exception:
        logging.exception("photo_store: ensure_indexes failed")


def is_available() -> bool:
    return _get_db() is not None


# ── Channel binding ───────────────────────────────────────────────────────


async def get_channel_by_owner(owner_user_id: int) -> Optional[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        return await db["photo_channels"].find_one({"owner_user_id": owner_user_id})
    except Exception:
        logging.exception("photo_store: get_channel_by_owner failed uid=%d", owner_user_id)
        return None


async def get_channel(channel_id: int) -> Optional[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        return await db["photo_channels"].find_one({"channel_id": channel_id})
    except Exception:
        logging.exception("photo_store: get_channel failed cid=%d", channel_id)
        return None


async def bind_channel(
    channel_id: int, owner_user_id: int, creator_user_id: int
) -> Optional[str]:
    """Insert the channel binding. Returns an error string on conflict,
    None on success."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return "MongoDB is not configured"
    try:
        existing_owner = await db["photo_channels"].find_one(
            {"owner_user_id": owner_user_id}
        )
        if existing_owner and existing_owner["channel_id"] != channel_id:
            return "You already have a channel connected"
        existing_channel = await db["photo_channels"].find_one(
            {"channel_id": channel_id}
        )
        if existing_channel:
            if existing_channel["owner_user_id"] != owner_user_id:
                return "That channel is already connected by another user"
            # Idempotent re-connect: refresh status.
            await db["photo_channels"].update_one(
                {"channel_id": channel_id},
                {"$set": {"status": "active", "creator_user_id": creator_user_id}},
            )
            return None
        await db["photo_channels"].insert_one(
            {
                "channel_id": channel_id,
                "owner_user_id": owner_user_id,
                "creator_user_id": creator_user_id,
                "status": "active",
                "linked_at": _now(),
            }
        )
        return None
    except Exception:
        logging.exception(
            "photo_store: bind_channel failed cid=%d uid=%d", channel_id, owner_user_id
        )
        return "Could not save the channel binding"


async def set_channel_status(channel_id: int, status: str) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["photo_channels"].update_one(
            {"channel_id": channel_id}, {"$set": {"status": status}}
        )
        return result.matched_count > 0
    except Exception:
        logging.exception(
            "photo_store: set_channel_status failed cid=%d status=%s", channel_id, status
        )
        return False


async def unbind_channel(owner_user_id: int) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        result = await db["photo_channels"].delete_one({"owner_user_id": owner_user_id})
        return result.deleted_count > 0
    except Exception:
        logging.exception("photo_store: unbind_channel failed uid=%d", owner_user_id)
        return False


# ── Photo documents ───────────────────────────────────────────────────────


def _serialize_photo(doc: dict) -> dict:
    """Public shape for the SPA. ObjectId and internal fields stripped."""
    return {
        "id": str(doc["_id"]),
        "messageId": doc.get("message_id"),
        "kind": doc.get("kind"),
        "fileName": doc.get("file_name"),
        "mime": doc.get("mime"),
        "size": doc.get("size"),
        "width": doc.get("width"),
        "height": doc.get("height"),
        "duration": doc.get("duration"),
        "takenAt": doc.get("taken_at").isoformat() if doc.get("taken_at") else None,
        "camera": doc.get("camera"),
        "gps": doc.get("gps"),
        "favorite": bool(doc.get("favorite")),
        "albumIds": doc.get("album_ids", []),
        "deleted": bool(doc.get("deleted")),
        "thumbsReady": bool(doc.get("thumb", {}).get("grid")),
        "uploadedAt": doc.get("uploaded_at").isoformat() if doc.get("uploaded_at") else None,
    }


async def upsert_photo(doc: dict) -> Optional[str]:
    """Insert a photo doc keyed by (channel_id, message_id).

    Returns an error string on failure, None on success. ``doc`` must carry
    channel_id, message_id, owner_user_id. Returns the string "duplicate"
    when the exact (channel_id, message_id) already exists.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return "MongoDB is not configured"
    try:
        existing = await db["photos"].find_one(
            {
                "channel_id": doc["channel_id"],
                "message_id": doc["message_id"],
            },
            projection={"_id": 1, "deleted": 1},
        )
        if existing:
            if existing.get("deleted"):
                # Re-ingest of a trashed message (user restored/reposted):
                # undelete rather than stay trashed.
                await db["photos"].update_one(
                    {"_id": existing["_id"]}, {"$set": {"deleted": False}}
                )
            return "duplicate"
        doc.setdefault("uploaded_at", _now())
        doc.setdefault("album_ids", [])
        doc.setdefault("favorite", False)
        doc.setdefault("deleted", False)
        doc.setdefault("thumb", {"grid": False, "preview": False})
        await db["photos"].insert_one(dict(doc))
        return None
    except Exception:
        logging.exception(
            "photo_store: upsert_photo failed cid=%s mid=%s",
            doc.get("channel_id"), doc.get("message_id"),
        )
        return "Could not save photo metadata"


async def find_by_sha(owner_user_id: int, sha256: str) -> Optional[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        return await db["photos"].find_one(
            {"owner_user_id": owner_user_id, "sha256": sha256}
        )
    except Exception:
        return None


async def get_photo(owner_user_id: int, message_id: int) -> Optional[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        return await db["photos"].find_one(
            {"owner_user_id": owner_user_id, "message_id": message_id}
        )
    except Exception:
        logging.exception("photo_store: get_photo failed uid=%d mid=%d", owner_user_id, message_id)
        return None


async def get_photo_by_id(owner_user_id: int, photo_id: str) -> Optional[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        from bson import ObjectId
        return await db["photos"].find_one(
            {"_id": ObjectId(photo_id), "owner_user_id": owner_user_id}
        )
    except Exception:
        return None


async def set_thumb_flags(owner_user_id: int, channel_id: int, message_id: int,
                          grid: bool = True, preview: bool = False) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        update: Dict[str, Any] = {}
        if grid:
            update["thumb.grid"] = True
        if preview:
            update["thumb.preview"] = True
        if not update:
            return
        await db["photos"].update_one(
            {"owner_user_id": owner_user_id, "channel_id": channel_id,
             "message_id": message_id},
            {"$set": update},
        )
    except Exception:
        logging.exception("photo_store: set_thumb_flags failed mid=%d", message_id)


async def mark_hash(owner_user_id: int, channel_id: int, message_id: int,
                    sha256: str, width: Optional[int] = None,
                    height: Optional[int] = None, duration: Optional[float] = None,
                    taken_at: Optional[datetime] = None, camera: Optional[str] = None,
                    gps: Optional[dict] = None) -> None:
    """Write pipeline results (hash, dimensions, EXIF) onto the photo doc."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        update: Dict[str, Any] = {"sha256": sha256}
        if width:
            update["width"] = width
        if height:
            update["height"] = height
        if duration is not None:
            update["duration"] = duration
        if taken_at:
            update["taken_at"] = taken_at
        if camera:
            update["camera"] = camera
        if gps:
            update["gps"] = gps
        await db["photos"].update_one(
            {"owner_user_id": owner_user_id, "channel_id": channel_id,
             "message_id": message_id},
            {"$set": update},
        )
    except Exception:
        logging.exception("photo_store: mark_hash failed mid=%d", message_id)


async def timeline_page(
    owner_user_id: int,
    *,
    cursor: Optional[str] = None,
    favorites: bool = False,
    album_id: str = "",
    trash: bool = False,
    limit: int = 120,
) -> dict:
    """Cursor-paginated timeline, newest first.

    Cursor is the opaque ``taken_at`` ISO string of the last item; items
    strictly older than the cursor are returned. Day-grouping happens in
    the route layer (it is a display concern over the same sorted list).
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return {"items": [], "nextCursor": None}
    limit = max(1, min(int(limit), _PAGE_CAP))
    query: Dict[str, Any] = {"owner_user_id": owner_user_id}
    if trash:
        query["deleted"] = True
    else:
        query["deleted"] = False
    if favorites:
        query["favorite"] = True
    if album_id:
        query["album_ids"] = album_id
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
            query["taken_at"] = {"$lt": cursor_dt}
        except ValueError:
            pass
    try:
        docs = await db["photos"].find(
            query,
            projection={"file_name": 1, "kind": 1, "mime": 1, "size": 1, "width": 1,
                        "height": 1, "duration": 1, "taken_at": 1, "camera": 1,
                        "gps": 1, "favorite": 1, "album_ids": 1, "deleted": 1,
                        "thumb": 1, "message_id": 1, "uploaded_at": 1},
        ).sort("taken_at", -1).to_list(length=limit + 1)
        next_cursor = None
        if len(docs) > limit:
            docs = docs[:limit]
            last_taken = docs[-1].get("taken_at")
            next_cursor = last_taken.isoformat() if last_taken else None
        return {
            "items": [_serialize_photo(d) for d in docs],
            "nextCursor": next_cursor,
        }
    except Exception:
        logging.exception("photo_store: timeline_page failed uid=%d", owner_user_id)
        return {"items": [], "nextCursor": None}


async def count_photos(owner_user_id: int) -> int:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        return await db["photos"].count_documents(
            {"owner_user_id": owner_user_id, "deleted": False}
        )
    except Exception:
        return 0


async def set_favorite(owner_user_id: int, photo_id: str, favorite: bool) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        from bson import ObjectId
        result = await db["photos"].update_one(
            {"_id": ObjectId(photo_id), "owner_user_id": owner_user_id},
            {"$set": {"favorite": bool(favorite)}},
        )
        return result.matched_count > 0
    except Exception:
        logging.exception("photo_store: set_favorite failed uid=%d pid=%s", owner_user_id, photo_id)
        return False


async def soft_delete(owner_user_id: int, photo_ids: List[str], deleted: bool) -> int:
    """Trash / restore. Bytes stay in the channel; this only hides metadata."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        from bson import ObjectId
        ids = [ObjectId(p) for p in photo_ids if p]
        if not ids:
            return 0
        result = await db["photos"].update_many(
            {"_id": {"$in": ids}, "owner_user_id": owner_user_id},
            {"$set": {"deleted": bool(deleted)}},
        )
        return result.modified_count
    except Exception:
        logging.exception("photo_store: soft_delete failed uid=%d", owner_user_id)
        return 0


# ── Albums ────────────────────────────────────────────────────────────────


async def create_album(owner_user_id: int, name: str) -> Optional[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        doc = {
            "owner_user_id": owner_user_id,
            "name": name,
            "cover_message_id": None,
            "created_at": _now(),
            "sort": 0,
        }
        result = await db["photo_albums"].insert_one(dict(doc))
        doc["_id"] = result.inserted_id
        return _serialize_album(doc)
    except Exception:
        logging.exception("photo_store: create_album failed uid=%d", owner_user_id)
        return None


def _serialize_album(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name"),
        "coverMessageId": doc.get("cover_message_id"),
        "createdAt": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "sort": doc.get("sort", 0),
    }


async def list_albums(owner_user_id: int) -> List[dict]:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return []
    try:
        docs = await db["photo_albums"].find(
            {"owner_user_id": owner_user_id}
        ).sort("created_at", 1).to_list(length=_PAGE_CAP)
        return [_serialize_album(d) for d in docs]
    except Exception:
        logging.exception("photo_store: list_albums failed uid=%d", owner_user_id)
        return []


async def rename_album(owner_user_id: int, album_id: str, name: str) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        from bson import ObjectId
        result = await db["photo_albums"].update_one(
            {"_id": ObjectId(album_id), "owner_user_id": owner_user_id},
            {"$set": {"name": name}},
        )
        return result.matched_count > 0
    except Exception:
        return False


async def delete_album(owner_user_id: int, album_id: str) -> bool:
    """Mongo-only: pull the album id from member photos, drop the album doc."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        from bson import ObjectId
        result = await db["photo_albums"].delete_one(
            {"_id": ObjectId(album_id), "owner_user_id": owner_user_id}
        )
        if result.deleted_count:
            await db["photos"].update_many(
                {"owner_user_id": owner_user_id, "album_ids": album_id},
                {"$pull": {"album_ids": album_id}},
            )
        return result.deleted_count > 0
    except Exception:
        logging.exception("photo_store: delete_album failed uid=%d", owner_user_id)
        return False


async def set_album_photos(owner_user_id: int, album_id: str,
                           photo_ids: List[str], member: bool) -> int:
    """Bulk add/remove photos to/from an album. Returns modified count."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        from bson import ObjectId
        ids = [ObjectId(p) for p in photo_ids if p]
        if not ids:
            return 0
        update = (
            {"$addToSet": {"album_ids": album_id}}
            if member
            else {"$pull": {"album_ids": album_id}}
        )
        result = await db["photos"].update_many(
            {"_id": {"$in": ids}, "owner_user_id": owner_user_id}, update
        )
        return result.modified_count
    except Exception:
        logging.exception("photo_store: set_album_photos failed uid=%d", owner_user_id)
        return 0


async def set_album_cover(owner_user_id: int, album_id: str, message_id: int) -> bool:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return False
    try:
        from bson import ObjectId
        result = await db["photo_albums"].update_one(
            {"_id": ObjectId(album_id), "owner_user_id": owner_user_id},
            {"$set": {"cover_message_id": message_id}},
        )
        return result.matched_count > 0
    except Exception:
        return False


# ── Thumbnails ────────────────────────────────────────────────────────────


def thumb_key(channel_id: int, message_id: int, size: str) -> str:
    return f"{channel_id}:{message_id}:{size}"


async def get_thumb(key: str) -> Optional[bytes]:
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db["photo_thumbs"].find_one({"key": key}, projection={"data": 1})
        data = doc.get("data") if doc else None
        return bytes(data) if data else None
    except Exception:
        logging.exception("photo_store: get_thumb failed key=%s", key)
        return None


_THUMB_MAX_BYTES = 15 * 1024 * 1024


async def put_thumb(owner_user_id: int, key: str, data: bytes) -> None:
    db = _get_db()
    if db is None:
        return
    if not data or len(data) > _THUMB_MAX_BYTES:
        return
    try:
        await db["photo_thumbs"].replace_one(
            {"key": key},
            {"key": key, "owner_user_id": owner_user_id, "data": _binary(data)},
            upsert=True,
        )
    except Exception:
        logging.exception("photo_store: put_thumb failed key=%s", key)


def _binary(data: bytes):
    from bson.binary import Binary
    return Binary(data)

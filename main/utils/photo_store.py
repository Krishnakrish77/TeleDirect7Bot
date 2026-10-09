"""TeleDirect Photos persistence — per-user photo libraries in MongoDB.

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
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from main.vars import Var

try:
    from bson.son import SON
except ImportError:  # pragma: no cover — only matters for Mongo deployments
    SON = dict  # type: ignore[assignment,misc]

_PAGE_CAP = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


_db_error_logged = False


def _get_db():
    """Return the Motor database object if MongoDB backend is active.

    Every failure here means the whole Photos feature reports "MongoDB is not
    configured", so the underlying error is logged once — a swallowed
    ImportError/attribute error is otherwise impossible to diagnose from the
    API responses alone.
    """
    global _db_error_logged
    try:
        from main.utils import media_index as _mi
        s = _mi._store
        if s is None or not hasattr(s, "_client"):
            return None
        return s._client[s._db_name]
    except Exception:
        if not _db_error_logged:
            _db_error_logged = True
            logging.exception("photo_store: Mongo client unavailable; Photos is disabled")
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
        await photos.create_index([("owner_user_id", 1), ("taken_at", -1), ("_id", -1)])
        await photos.create_index(
            [("owner_user_id", 1), ("sha256", 1)], unique=True, sparse=True
        )
        await photos.create_index([("owner_user_id", 1), ("favorite", 1)])
        await photos.create_index([("owner_user_id", 1), ("deleted", 1), ("taken_at", -1)])
        await photos.create_index(
            [("channel_id", 1), ("message_id", 1)], unique=True
        )
        # Server-side search: one text index over the human-meaningful fields.
        # Filenames like IMG_2047 tokenize poorly, but camera ("Apple iPhone
        # 15"), place ("Lisbon, Portugal") and later labels tokenize well —
        # and the tokenized filename still catches "portugal" in
        # "Portugal 2026.jpg".
        # place was added after the 2-field version shipped: Mongo refuses to
        # create a same-name index with a different spec, so detect the old
        # one and drop it once (idempotent — create_index after drop).
        existing_text = await photos.index_information()
        old_spec = existing_text.get("photos_text_search")
        if old_spec and "place" not in str(old_spec.get("key", old_spec.get("weights", ""))):
            await photos.drop_index("photos_text_search")
        await photos.create_index(
            [("file_name", "text"), ("camera", "text"), ("place", "text")],
            default_language="english",
            name="photos_text_search",
        )
        # Places: $near / $geoWithin need the GeoJSON point.
        await photos.create_index([("location", "2dsphere")], sparse=True)
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
    """Fetch a channel binding.

    Raises on DB errors — the stream.py caller is fail-closed and MUST be
    able to distinguish "not a vault" (None) from "cannot tell" (exception).
    Other callers wrap this in try/except themselves.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    return await db["photo_channels"].find_one({"channel_id": channel_id})


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


async def get_scan_cursor(channel_id: int) -> int:
    """Backfill watermark for a vault: next message id still to probe.

    Bots cannot page history, so the scan walks ids in batches; persisting the
    cursor lets a pass interrupted by FloodWait / a full queue / a restart
    continue instead of restarting from id 1. 0 means "never scanned".
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        doc = await db["photo_channels"].find_one(
            {"channel_id": channel_id}, projection={"scan_cursor": 1}
        )
        return int((doc or {}).get("scan_cursor") or 0)
    except Exception:
        logging.exception("photo_store: get_scan_cursor failed cid=%d", channel_id)
        return 0


async def set_scan_cursor(channel_id: int, next_id: int) -> None:
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["photo_channels"].update_one(
            {"channel_id": channel_id}, {"$set": {"scan_cursor": int(next_id)}}
        )
    except Exception:
        logging.exception(
            "photo_store: set_scan_cursor failed cid=%d next=%d", channel_id, next_id
        )


async def rewind_scan_cursor(channel_id: int, message_id: int) -> None:
    """Pull the scan cursor back below ``message_id`` when an upload landed
    under it.

    The backfill only scans forward (cursor − overlap). Connect-time scans
    sweep the empty channel and park the cursor high; uploads to a fresh
    channel then land at LOW message ids the cursor has already passed, and
    a failed self-index would leave them invisible to every future rescan.
    Rewinding the cursor makes the next rescan re-cover the id.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        doc = await db["photo_channels"].find_one(
            {"channel_id": channel_id}, projection={"scan_cursor": 1}
        )
        cursor = int((doc or {}).get("scan_cursor") or 0)
        target = max(1, int(message_id) - 200)  # 200 = photos plugin _RESCAN_OVERLAP
        if cursor and cursor > target:
            await db["photo_channels"].update_one(
                {"channel_id": channel_id}, {"$set": {"scan_cursor": target}}
            )
    except Exception:
        logging.exception(
            "photo_store: rewind_scan_cursor failed cid=%d mid=%d", channel_id, message_id
        )


async def set_scan_status(channel_id: int, *, state: str, enqueued: int = 0,
                          scanned_to: int = 0, error: str = "") -> None:
    """Record the last backfill outcome.

    Background imports used to fail invisibly (the route returned ok while the
    scan died), so the result lives on the channel doc and the UI reports it.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["photo_channels"].update_one(
            {"channel_id": channel_id},
            {"$set": {"scan": {
                "state": state,
                "enqueued": int(enqueued),
                "scanned_to": int(scanned_to),
                "error": error[:200],
                "at": _now(),
            }}},
        )
    except Exception:
        logging.exception("photo_store: set_scan_status failed cid=%d", channel_id)


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


async def update_file_id(owner_user_id: int, channel_id: int, message_id: int,
                         file_id: str) -> None:
    """Persist a refreshed Telegram file reference (stored ones expire)."""
    db = _get_db()
    if db is None:
        return
    try:
        await db["photos"].update_one(
            {"owner_user_id": owner_user_id, "channel_id": channel_id,
             "message_id": message_id},
            {"$set": {"file_id": file_id}},
        )
    except Exception:
        logging.exception("photo_store: update_file_id failed mid=%d", message_id)


async def list_indexed_message_ids(owner_user_id: int, channel_id: int) -> set:
    """All message ids already indexed for this vault — used by rescan to
    find posts the live channel_post handler missed (downtime, full queue)."""
    db = _get_db()
    if db is None:
        return set()
    try:
        cursor = db["photos"].find(
            {"owner_user_id": owner_user_id, "channel_id": channel_id},
            projection={"message_id": 1},
        )
        return {doc["message_id"] async for doc in cursor}
    except Exception:
        logging.exception("photo_store: list_indexed_message_ids failed cid=%d", channel_id)
        return set()


def iso_utc(value) -> Optional[str]:
    """ISO-8601 with an explicit UTC offset for the SPA.

    Motor decodes BSON datetimes as NAIVE UTC (the shared client is not
    ``tz_aware``), so a bare ``isoformat()`` hands the browser a
    timezone-less instant that ``new Date()`` reads as LOCAL time — which
    shifts day grouping for every non-UTC user.
    """
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def scan_payload(scan: Optional[dict]) -> Optional[dict]:
    """JSON-safe view of the background scan record.

    Mongo stores the timestamp as a datetime, so returning the raw subdoc made
    /api/photos/status fail to serialise and 500 for connected users.
    """
    if not scan:
        return None
    return {
        "state": scan.get("state"),
        "enqueued": int(scan.get("enqueued") or 0),
        "error": scan.get("error") or "",
        "at": iso_utc(scan.get("at")),
    }


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
        "takenAt": iso_utc(doc.get("taken_at")),
        "camera": doc.get("camera"),
        "gps": doc.get("gps"),
        "place": doc.get("place") or None,
        "favorite": bool(doc.get("favorite")),
        "albumIds": doc.get("album_ids", []),
        "deleted": bool(doc.get("deleted")),
        "thumbsReady": bool(doc.get("thumb", {}).get("grid")),
        "uploadedAt": iso_utc(doc.get("uploaded_at")),
    }


async def upsert_photo(doc: dict) -> Optional[str]:
    """Insert a photo doc keyed by (channel_id, message_id).

    The insert is unconditional and the unique indexes arbitrate races:
    a concurrent upload tagging `album_ids` via append_album_ids writes
    directly to the existing doc, and this function never touches
    `album_ids` on the merge path — tags survive regardless of ordering.

    Returns an error string on failure, None on success, "duplicate" when
    the (channel_id, message_id) or (owner, sha256) already exists.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return "MongoDB is not configured"
    try:
        doc.setdefault("uploaded_at", _now())
        doc.setdefault("album_ids", [])
        doc.setdefault("favorite", False)
        doc.setdefault("deleted", False)
        doc.setdefault("thumb", {"grid": False, "preview": False})
        invalidate_facets(doc.get("owner_user_id", 0))
        try:
            await db["photos"].insert_one(dict(doc))
            return None
        except Exception as exc:
            # DuplicateKeyError (pymongo.errors) — imported at call time:
            # pymongo 4.x moved it out of bson.errors, and a module-level
            # import inside a shared try/except poisoned every name in the
            # block when the import failed (prod NameError: TEXT).
            from pymongo.errors import DuplicateKeyError
            if not isinstance(exc, DuplicateKeyError):
                raise
        # already exists — fall through to the merge below
        existing = await db["photos"].find_one(
            {"channel_id": doc["channel_id"], "message_id": doc["message_id"]},
            projection={"_id": 1, "deleted": 1, "sha256": 1},
        )
        if not existing:
            # DuplicateKeyError came from (owner, sha256) — same bytes,
            # different message. That is a genuine per-user duplicate.
            return "duplicate"
        if existing.get("deleted"):
            # Re-ingest of a trashed message: undelete rather than stay
            # trashed.
            await db["photos"].update_one(
                {"_id": existing["_id"]}, {"$set": {"deleted": False}}
            )
        return "duplicate"
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
        logging.exception("photo_store: find_by_sha failed uid=%d", owner_user_id)
        return None


async def get_photo(owner_user_id: int, message_id: int,
                    channel_id: Optional[int] = None) -> Optional[dict]:
    """Fetch one photo doc.

    Telegram message ids are scoped per channel, so callers that know the
    channel (file/thumb routes via the active binding) must pass it —
    otherwise a reconnect to a different channel could surface a stale doc
    sharing the same message id. Ingest dedup passes channel_id too.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        query: Dict[str, Any] = {"owner_user_id": owner_user_id, "message_id": message_id}
        if channel_id is not None:
            query["channel_id"] = channel_id
        return await db["photos"].find_one(query)
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
        logging.exception(
            "photo_store: get_photo_by_id failed uid=%d pid=%s", owner_user_id, photo_id
        )
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


def _parse_iso_date(value: str) -> Optional[datetime]:
    """Parse a client-supplied ISO date/datetime into an aware UTC datetime.

    Bare dates ("2026-01-31") become midnight UTC. None on garbage — the
    caller drops the filter rather than 400ing a rough UI control.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_timeline_query(
    owner_user_id: int,
    *,
    trash: bool = False,
    favorites: bool = False,
    album_id: str = "",
    q: str = "",
    kind: str = "",
    mime: str = "",
    camera: str = "",
    place: str = "",
    taken_after: str = "",
    taken_before: str = "",
    min_size: int = 0,
    near: str = "",
    radius_km: float = 0.0,
) -> Dict[str, Any]:
    """Pure Mongo filter builder for the timeline — the search/filter API.

    Kept free of I/O so tests can assert the exact query shape without a
    database. ``q`` goes through $text (the photos_text_search index);
    every other parameter composes as plain equality/range clauses.
    ``near`` ("lat,lon") + ``radius_km`` become a $geoWithin $centerSphere
    — chosen over $near because $near cannot compose inside $and/$or with
    the cursor clauses.
    """
    query: Dict[str, Any] = {"owner_user_id": owner_user_id}
    query["deleted"] = True if trash else False
    if favorites:
        query["favorite"] = True
    if album_id:
        query["album_ids"] = album_id
    if kind:
        query["kind"] = kind
    if mime:
        query["mime"] = mime
    if camera:
        # Exact camera string comes from the facets endpoint, which reads
        # the same stored values — no regex needed.
        query["camera"] = camera
    if place:
        # Same logic as camera: exact value from the facets endpoint.
        query["place"] = place
    if min_size > 0:
        query["size"] = {"$gte": min_size}
    after = _parse_iso_date(taken_after)
    if after:
        query.setdefault("taken_at", {})["$gte"] = after
    before = _parse_iso_date(taken_before)
    if before:
        query.setdefault("taken_at", {})["$lte"] = before
    needle = q.strip()
    if needle:
        # Quoted phrases stay a phrase ("beach day"); bare words are ORed
        # by the server's text index — good enough for token search.
        query["$text"] = {"$search": needle}
    center = _parse_latlon(near)
    if center and radius_km > 0:
        query["location"] = {
            "$geoWithin": {
                "$centerSphere": [
                    [center[1], center[0]],  # GeoJSON order: lon, lat
                    radius_km / 6378.1,      # radians (Earth radius)
                ],
            },
        }
    return query


def _parse_latlon(value: str) -> Optional[tuple]:
    """Parse "lat,lon" floats. None on garbage — the caller drops the filter."""
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (lat, lon)


async def timeline_page(
    owner_user_id: int,
    *,
    cursor: Optional[str] = None,
    favorites: bool = False,
    album_id: str = "",
    trash: bool = False,
    limit: int = 120,
    q: str = "",
    kind: str = "",
    mime: str = "",
    camera: str = "",
    place: str = "",
    taken_after: str = "",
    taken_before: str = "",
    min_size: int = 0,
    near: str = "",
    radius_km: float = 0.0,
) -> dict:
    """Cursor-paginated timeline, newest first.

    Cursor is opaque: "<taken_at ISO>|<_id>". Photos commonly share an EXIF
    second, so paging on taken_at alone would skip the tie — the _id
    tie-breaker makes pagination stable. Day-grouping is a display concern
    in the route layer.

    Search/filter params feed build_timeline_query(); they compose with the
    cursor so a search result set pages exactly like the plain timeline.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return {"items": [], "nextCursor": None}
    limit = max(1, min(int(limit), _PAGE_CAP))
    query = build_timeline_query(
        owner_user_id,
        trash=trash,
        favorites=favorites,
        album_id=album_id,
        q=q,
        kind=kind,
        mime=mime,
        camera=camera,
        place=place,
        taken_after=taken_after,
        taken_before=taken_before,
        min_size=min_size,
        near=near,
        radius_km=radius_km,
    )
    if cursor:
        try:
            cursor_taken, cursor_id = cursor.split("|", 1)
            from bson import ObjectId
            query["$or"] = [
                {"taken_at": {"$lt": datetime.fromisoformat(cursor_taken)}},
                {"taken_at": datetime.fromisoformat(cursor_taken),
                 "_id": {"$lt": ObjectId(cursor_id)}},
            ]
        except (ValueError, TypeError):
            pass
    try:
        docs = await db["photos"].find(
            query,
            projection={"file_name": 1, "kind": 1, "mime": 1, "size": 1, "width": 1,
                        "height": 1, "duration": 1, "taken_at": 1, "camera": 1,
                        "gps": 1, "place": 1, "favorite": 1, "album_ids": 1, "deleted": 1,
                        "thumb": 1, "message_id": 1, "uploaded_at": 1},
        ).sort([("taken_at", -1), ("_id", -1)]).to_list(length=limit + 1)
        next_cursor = None
        if len(docs) > limit:
            docs = docs[:limit]
            last = docs[-1]
            last_taken = last.get("taken_at")
            if last_taken:
                next_cursor = f"{last_taken.isoformat()}|{last['_id']}"
        return {
            "items": [_serialize_photo(d) for d in docs],
            "nextCursor": next_cursor,
        }
    except Exception:
        logging.exception("photo_store: timeline_page failed uid=%d", owner_user_id)
        return {"items": [], "nextCursor": None}


async def set_place(owner_user_id: int, channel_id: int, message_id: int,
                    place: str) -> None:
    """Persist a reverse-geocoded place label on one photo. Best-effort
    (the geocode backfill re-labels later if this fails)."""
    db = _get_db()
    if db is None:
        return
    try:
        await db["photos"].update_one(
            {"channel_id": channel_id, "message_id": message_id,
             "owner_user_id": owner_user_id},
            {"$set": {"place": place}},
        )
    except Exception:
        logging.exception("photo_store: set_place failed cid=%d mid=%d", channel_id, message_id)


async def backfill_geo(owner_user_id: int, channel_id: int, *, batch: int = 200) -> int:
    """One-time migration: build `location` (GeoJSON) for photos that have
    `gps` but no `location`. Place labels come later (geocode backfill) —
    this only creates the indexable geometry. Returns docs updated.

    Called from the startup scan loop in main/__main__.py per vault.
    """
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        cursor = db["photos"].find(
            {
                "owner_user_id": owner_user_id,
                "channel_id": channel_id,
                "gps.lat": {"$type": "number"},
                "gps.lon": {"$type": "number"},
                "location": {"$exists": False},
            },
            projection={"gps": 1},
            limit=batch,
        )
        updated = 0
        async for doc in cursor:
            gps = doc.get("gps") or {}
            lat, lon = gps.get("lat"), gps.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            await db["photos"].update_one(
                {"_id": doc["_id"]},
                {"$set": {"location": {"type": "Point", "coordinates": [lon, lat]}}},
            )
            updated += 1
        if updated:
            logging.info("photo_store: backfilled %d geo points uid=%d cid=%d", updated, owner_user_id, channel_id)
        return updated
    except Exception:
        logging.exception("photo_store: backfill_geo failed uid=%d cid=%d", owner_user_id, channel_id)
        return 0


async def backfill_places(owner_user_id: int, channel_id: int, *, batch: int = 40) -> int:
    """Reverse-geocode photos that have geometry but no place label yet.

    Deliberately small batches: Nominatim's 1 req/s policy means a big
    library takes a while, and this runs alongside the hourly scan loop —
    progress over urgency. Returns photos labelled this round.
    """
    if not Var.PHOTOS_PLACES:
        return 0
    from main.utils import geocode

    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return 0
    try:
        docs = await db["photos"].find(
            {
                "owner_user_id": owner_user_id,
                "channel_id": channel_id,
                "location": {"$exists": True},
                "$or": [{"place": {"$exists": False}}, {"place": ""}],
            },
            projection={"location": 1},
            limit=batch,
        ).to_list(length=batch)
        labelled = 0
        for doc in docs:
            coords = (doc.get("location") or {}).get("coordinates") or []
            if len(coords) != 2:
                continue
            label = await geocode.reverse_geocode(coords[1], coords[0])
            if not label:
                continue
            await db["photos"].update_one(
                {"_id": doc["_id"]}, {"$set": {"place": label}}
            )
            labelled += 1
        if labelled:
            logging.info("photo_store: labelled %d places uid=%d cid=%d", labelled, owner_user_id, channel_id)
        return labelled
    except Exception:
        logging.exception("photo_store: backfill_places failed uid=%d cid=%d", owner_user_id, channel_id)
        return 0


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
        logging.exception("photo_store: count_photos failed uid=%d", owner_user_id)
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
        invalidate_facets(owner_user_id)
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
        invalidate_facets(owner_user_id)
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


def _serialize_album(doc: dict, count: int = 0, cover_id=None) -> dict:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name"),
        "coverMessageId": doc.get("cover_message_id"),
        "photoCount": int(count),
        "coverPhotoId": str(cover_id) if cover_id else None,
        "createdAt": iso_utc(doc.get("created_at")),
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
        if not docs:
            return []
        album_ids = [str(d["_id"]) for d in docs]
        # One aggregation over this owner's non-deleted photos: per-album
        # member count plus the most recently taken member (taken_at desc,
        # _id desc) as the fallback cover.
        pipeline = [
            {"$match": {
                "owner_user_id": owner_user_id,
                "deleted": False,
                "album_ids": {"$in": album_ids},
            }},
            {"$unwind": "$album_ids"},
            {"$match": {"album_ids": {"$in": album_ids}}},
            {"$sort": {"taken_at": -1, "_id": -1}},
            {"$group": {
                "_id": "$album_ids",
                "count": {"$sum": 1},
                "cover": {"$first": "$_id"},
            }},
        ]
        stats: Dict[str, dict] = {}
        async for row in db["photos"].aggregate(pipeline):
            stats[row["_id"]] = row
        # Albums with an explicit cover_message_id: resolve the member doc
        # carrying that message id in one batched query (explicit cover wins
        # over the aggregation's latest-member fallback).
        wanted = {
            str(d["_id"]): d["cover_message_id"]
            for d in docs
            if d.get("cover_message_id") is not None
        }
        explicit: Dict[str, Any] = {}
        if wanted:
            cursor = db["photos"].find(
                {
                    "owner_user_id": owner_user_id,
                    "deleted": False,
                    "album_ids": {"$in": list(wanted)},
                    "message_id": {"$in": list(wanted.values())},
                },
                {"_id": 1, "album_ids": 1, "message_id": 1},
            )
            async for photo in cursor:
                for aid in photo.get("album_ids") or []:
                    if wanted.get(aid) == photo.get("message_id"):
                        explicit[aid] = photo["_id"]
        return [
            _serialize_album(
                d,
                count=stats.get(str(d["_id"]), {}).get("count", 0),
                cover_id=explicit.get(str(d["_id"]))
                or stats.get(str(d["_id"]), {}).get("cover"),
            )
            for d in docs
        ]
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
        logging.exception(
            "photo_store: rename_album failed uid=%d aid=%s", owner_user_id, album_id
        )
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


async def get_album(owner_user_id: int, album_id: str) -> Optional[dict]:
    """Ownership-checked album fetch — used to validate upload album tags."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return None
    try:
        from bson import ObjectId
        return await db["photo_albums"].find_one(
            {"_id": ObjectId(album_id), "owner_user_id": owner_user_id}
        )
    except Exception:
        logging.exception(
            "photo_store: get_album failed uid=%d aid=%s", owner_user_id, album_id
        )
        return None


async def append_album_ids(owner_user_id: int, channel_id: int, message_id: int,
                           album_ids: List[str]) -> None:
    """Tag an uploaded message with album ids (upload path). Merges —
    the async ingest must not overwrite this with its empty default.
    Channel-scoped: message ids repeat across channels after a reconnect."""
    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return
    try:
        await db["photos"].update_many(
            {"owner_user_id": owner_user_id, "channel_id": channel_id,
             "message_id": message_id},
            {"$addToSet": {"album_ids": {"$each": album_ids}}},
        )
    except Exception:
        logging.exception(
            "photo_store: append_album_ids failed uid=%d mid=%d", owner_user_id, message_id
        )


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
        logging.exception(
            "photo_store: set_album_cover failed uid=%d aid=%s", owner_user_id, album_id
        )
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


# Facet caches: owner key → (computed_at, payload). The aggregation walks
# the whole library per call, so a short TTL keeps repeated timeline visits
# cheap without staleness that matters (counts, not truth).
_FACETS_TTL = 60.0
_facets_cache: Dict[int, tuple] = {}


def invalidate_facets(owner_user_id: int) -> None:
    """Drop the facet cache after any ingest/delete that changes counts."""
    _facets_cache.pop(owner_user_id, None)


async def photo_facets(
    owner_user_id: int,
    *,
    album_id: str = "",
    q: str = "",
) -> dict:
    """Counts for the filter-chip row: kind, camera, month, place buckets.

    Aggregates over the non-deleted library (optionally scoped to an album
    or a search query so chips reflect the current context). Cached 60s per
    (owner, album, q) — facet counts are navigational, not live.
    """
    cache_key = (owner_user_id, album_id, q.strip())
    cached = _facets_cache.get(owner_user_id)
    if cached and cached[0] == cache_key and time.monotonic() - cached[1] < _FACETS_TTL:
        return cached[2]

    await _ensure_indexes()
    db = _get_db()
    if db is None:
        return {"kinds": {}, "cameras": [], "months": [], "places": []}
    match = build_timeline_query(owner_user_id, album_id=album_id, q=q)
    try:
        pipeline = [
            {"$match": match},
            {
                "$facet": {
                    "kinds": [
                        {"$group": {"_id": "$kind", "n": {"$sum": 1}}},
                    ],
                    "cameras": [
                        {"$match": {"camera": {"$nin": [None, ""]}}},
                        {"$group": {"_id": "$camera", "n": {"$sum": 1}}},
                        {"$sort": SON([("n", -1), ("_id", 1)])},
                        {"$limit": 12},
                    ],
                    "places": [
                        {"$match": {"place": {"$nin": [None, ""]}}},
                        {"$group": {"_id": "$place", "n": {"$sum": 1}}},
                        {"$sort": SON([("n", -1), ("_id", 1)])},
                        {"$limit": 8},
                    ],
                    "months": [
                        {"$match": {"taken_at": {"$ne": None}}},
                        {"$group": {
                            "_id": {
                                "year": {"$year": "$taken_at"},
                                "month": {"$month": "$taken_at"},
                            },
                            "n": {"$sum": 1},
                        }},
                        {"$sort": SON([("_id.year", -1), ("_id.month", -1)])},
                        {"$limit": 24},
                    ],
                },
            },
        ]
        [agg] = await db["photos"].aggregate(pipeline).to_list(length=1)
        result = {
            "kinds": {d["_id"]: d["n"] for d in agg.get("kinds", []) if d["_id"]},
            "cameras": [
                {"camera": d["_id"], "count": d["n"]}
                for d in agg.get("cameras", [])
            ],
            "places": [
                {"place": d["_id"], "count": d["n"]}
                for d in agg.get("places", [])
            ],
            "months": [
                {"year": d["_id"]["year"], "month": d["_id"]["month"], "count": d["n"]}
                for d in agg.get("months", [])
            ],
        }
        _facets_cache[owner_user_id] = (cache_key, time.monotonic(), result)
        return result
    except Exception:
        logging.exception("photo_store: photo_facets failed uid=%d", owner_user_id)
        return {"kinds": {}, "cameras": [], "months": [], "places": []}

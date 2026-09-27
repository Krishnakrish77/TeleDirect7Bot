"""TeleDirect Photos HTTP API — session-guarded, owner-scoped.

Every handler resolves the user from the ``td_session`` JWT and filters all
reads/writes by that user id server-side. Photos never appear on the public
bearer-by-hash stream routes.

Serving:
  * thumbs  — generated webp from Mongo (photo_thumbs)
  * originals — /api/photos/file/{message_id}: ownership check, then
    byte-range stream from the user's own channel via ByteStreamer.

Uploads stream through the multipart reader directly into the bot's
send_document call — original bytes never touch disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
from typing import Optional

from aiohttp import web

from main import Var
from main.bot import StreamBot, multi_clients
from main.utils import photo_store
from main.utils.custom_dl import ByteStreamer, MediaSessionUnavailable
from main.utils.user_auth import get_user

routes = web.RouteTableDef()

# Channel input: @username, t.me/username link, or -100… numeric id.
_CHANNEL_INPUT_RE = re.compile(r"^(?:@|https?://t\.me/)?([A-Za-z0-9_]{4,64})$")
_CHANNEL_ID_RE = re.compile(r"^-100\d{6,}$")

_channels_cache_ttl = 30.0
_channels_cache: dict[int, tuple[float, dict]] = {}


def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _session_user(request: web.Request) -> Optional[dict]:
    user = get_user(request)
    if not user or not user.get("sub"):
        return None
    return user


def _require_user(request: web.Request) -> dict:
    user = _session_user(request)
    if not user:
        raise web.HTTPUnauthorized(text="Sign in required")
    return user


def _photos_disabled() -> Optional[web.Response]:
    if not Var.PHOTOS_ENABLED:
        return _json({"error": "Photos feature is disabled"}, status=503)
    if not photo_store.is_available():
        return _json({"error": "MongoDB is required for photos"}, status=503)
    return None


# ── Channel resolution + verification ─────────────────────────────────────


def _parse_channel_input(raw: str) -> Optional[int | str]:
    """Return an int channel id or a username string, None if malformed."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("-100"):
        if _CHANNEL_ID_RE.match(text):
            return int(text)
        return None
    if text.lstrip("-").isdigit():
        return None  # raw channel ids must be -100… form
    match = _CHANNEL_INPUT_RE.match(text)
    if match:
        return match.group(1)
    return None


async def _verify_channel_access(channel_ref: int | str, requester_id: int) -> tuple[Optional[dict], str]:
    """Run the three trust checks from the plan (§4).

    Returns (chat_dict, "") on success or (None, reason).
    """
    bot = multi_clients.get(0) or StreamBot
    try:
        chat = await bot.get_chat(channel_ref)
    except Exception as exc:
        return None, f"Could not resolve that channel: {exc}"

    chat_id = chat.id
    # Must be a channel (broadcast), not a user/group/supergroup. Public
    # channels also carry -100 ids, so the type check is what enforces the
    # private-vault invariant — and Telegram only offers private channels
    # here since bots cannot join public ones, but verify explicitly.
    chat_type = getattr(chat, "type", None)
    type_name = getattr(chat_type, "name", "") or str(chat_type or "")
    if type_name.upper() != "CHANNEL":
        return None, "Only channels (not groups or users) can be used"
    if not str(chat_id).startswith("-100"):
        return None, "Only private channels can be used"

    try:
        bot_member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
    except Exception:
        return None, "Add the bot as an administrator of the channel first"
    bot_status = getattr(bot_member, "status", "")
    if bot_status not in ("administrator", "creator"):
        return None, "The bot must be a channel administrator with post rights"
    privileges = getattr(bot_member, "privileges", None)
    if bot_status == "administrator" and privileges is not None:
        if not getattr(privileges, "can_post_messages", True):
            return None, "The bot needs post-messages rights to ingest uploads"

    try:
        member = await bot.get_chat_member(chat_id, requester_id)
    except Exception:
        return None, "Could not verify your membership in that channel"
    member_status = getattr(member, "status", "")
    if member_status not in ("administrator", "creator"):
        return None, "You must be the channel creator (or an admin) to connect it"

    # The requester just proved admin/creator status via get_chat_member.
    # Anonymous-admin channels can hide who the real creator is from the
    # API, so record the verified requester — re-verify (reverify_channel)
    # re-runs the same membership check, which is the actual trust bound.
    creator_id = requester_id
    return {
        "chat_id": chat_id,
        "creator_id": creator_id or requester_id,
        "title": getattr(chat, "title", "") or "",
    }, ""


async def _channel_doc(owner_user_id: int, *, fresh: bool = False) -> Optional[dict]:
    cached = _channels_cache.get(owner_user_id)
    if not fresh and cached and (time.monotonic() - cached[0]) < _channels_cache_ttl:
        return cached[1]
    doc = await photo_store.get_channel_by_owner(owner_user_id)
    _channels_cache[owner_user_id] = (time.monotonic(), doc)
    return doc


def _invalidate_channel_cache(owner_user_id: int) -> None:
    _channels_cache.pop(owner_user_id, None)


# ── Connect / disconnect / status ────────────────────────────────────────


@routes.post("/api/photos/connect")
async def connect_channel(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    channel_ref = _parse_channel_input(str(body.get("channel", "")))
    if channel_ref is None:
        return _json(
            {"error": "Paste the channel @username or the -100… id"},
            status=400,
        )
    user_id = int(user["sub"])

    verified, reason = await _verify_channel_access(channel_ref, user_id)
    if verified is None:
        return _json({"error": reason}, status=400)

    err = await photo_store.bind_channel(
        verified["chat_id"], user_id, verified["creator_id"]
    )
    if err:
        return _json({"error": err}, status=409 if "another user" in err else 400)
    _invalidate_channel_cache(user_id)
    return _json({"ok": True, "channelId": verified["chat_id"], "title": verified["title"]})


@routes.post("/api/photos/disconnect")
async def disconnect_channel(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    ok = await photo_store.unbind_channel(user_id)
    _invalidate_channel_cache(user_id)
    return _json({"ok": ok})


@routes.get("/api/photos/status")
async def photos_status(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    doc = await _channel_doc(user_id, fresh=True)
    if not doc:
        return _json({"connected": False})
    return _json({
        "connected": True,
        "channelId": doc.get("channel_id"),
        "status": doc.get("status", "active"),
        "photoCount": await photo_store.count_photos(user_id),
    })


# ── Timeline / favorites / trash ─────────────────────────────────────────


@routes.get("/api/photos/timeline")
async def photos_timeline(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    view = request.rel_url.query.get("view", "timeline")
    try:
        limit = int(request.rel_url.query.get("limit", "120"))
    except ValueError:
        limit = 120
    result = await photo_store.timeline_page(
        user_id,
        cursor=request.rel_url.query.get("cursor") or None,
        favorites=view == "favorites",
        trash=view == "trash",
        album_id=request.rel_url.query.get("album") or "",
        limit=limit,
    )
    return _json(result)


@routes.post("/api/photos/{photo_id}/favorite")
async def toggle_favorite(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    photo_id = request.match_info["photo_id"]
    try:
        body = await request.json()
        favorite = bool(body.get("favorite", True))
    except Exception:
        favorite = True
    ok = await photo_store.set_favorite(user_id, photo_id, favorite)
    if not ok:
        raise web.HTTPNotFound(text="Photo not found")
    return _json({"ok": True, "favorite": favorite})


@routes.post("/api/photos/trash")
async def trash_photos(request: web.Request) -> web.Response:
    await _require_user(request)
    return await _apply_trash(request, deleted=True)


@routes.post("/api/photos/restore")
async def restore_photos(request: web.Request) -> web.Response:
    await _require_user(request)
    return await _apply_trash(request, deleted=False)


async def _apply_trash(request: web.Request, *, deleted: bool) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    try:
        body = await request.json()
        ids = [str(x) for x in (body.get("ids") or []) if x]
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    if not ids:
        raise web.HTTPBadRequest(text="No photo ids supplied")
    if len(ids) > 500:
        raise web.HTTPBadRequest(text="Too many ids")
    count = await photo_store.soft_delete(user_id, ids, deleted)
    return _json({"ok": True, "modified": count})


# ── Albums ────────────────────────────────────────────────────────────────


@routes.get("/api/photos/albums")
async def albums_list(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    albums = await photo_store.list_albums(int(user["sub"]))
    return _json({"albums": albums})


@routes.post("/api/photos/albums")
async def albums_create(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 80:
        raise web.HTTPBadRequest(text="Album name must be 1-80 characters")
    album = await photo_store.create_album(int(user["sub"]), name)
    if not album:
        return _json({"error": "Could not create album"}, status=500)
    return _json({"album": album})


@routes.patch("/api/photos/albums/{album_id}")
async def albums_rename(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 80:
        raise web.HTTPBadRequest(text="Album name must be 1-80 characters")
    ok = await photo_store.rename_album(int(user["sub"]), request.match_info["album_id"], name)
    if not ok:
        raise web.HTTPNotFound(text="Album not found")
    return _json({"ok": True})


@routes.delete("/api/photos/albums/{album_id}")
async def albums_delete(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    ok = await photo_store.delete_album(int(user["sub"]), request.match_info["album_id"])
    if not ok:
        raise web.HTTPNotFound(text="Album not found")
    return _json({"ok": True})


@routes.post("/api/photos/albums/{album_id}/photos")
async def album_photos_add(request: web.Request) -> web.Response:
    return await _album_membership(request, member=True)


@routes.delete("/api/photos/albums/{album_id}/photos")
async def album_photos_remove(request: web.Request) -> web.Response:
    return await _album_membership(request, member=False)


async def _album_membership(request: web.Request, *, member: bool) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    album_id = request.match_info["album_id"]
    try:
        body = await request.json()
        ids = [str(x) for x in (body.get("ids") or []) if x]
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    if not ids:
        raise web.HTTPBadRequest(text="No photo ids supplied")
    if len(ids) > 500:
        raise web.HTTPBadRequest(text="Too many ids")
    count = await photo_store.set_album_photos(user_id, album_id, ids, member)
    return _json({"ok": True, "modified": count})


# ── Thumbnails ────────────────────────────────────────────────────────────


@routes.get(r"/api/photos/thumb/{message_id:\d+}/{size:grid|preview}")
async def photo_thumb(request: web.Request) -> web.StreamResponse:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    message_id = int(request.match_info["message_id"])
    size = request.match_info["size"]

    # Prefer the active binding's channel scope; fall back to a bare
    # lookup so disconnected-channel thumbs still render (with the
    # reconnect banner on the page).
    channel_doc = await _channel_doc(user_id)
    doc = await photo_store.get_photo(
        user_id, message_id,
        channel_id=channel_doc["channel_id"] if channel_doc else None,
    )
    if not doc:
        raise web.HTTPNotFound(text="Photo not found")
    channel_id = doc.get("channel_id")
    key = photo_store.thumb_key(channel_id, message_id, size)
    data = await photo_store.get_thumb(key)
    if not data:
        # Regenerate synchronously on first hit; pipeline flags miss when
        # generation failed permanently (e.g. unsupported format).
        from main.utils.photo_pipeline import generate_thumbs_for
        data = await generate_thumbs_for(user_id, channel_id, message_id, size)
    if not data:
        raise web.HTTPNotFound(text="Thumbnail unavailable")
    return web.Response(
        body=data,
        content_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ── Original bytes ────────────────────────────────────────────────────────


def _class_streamer() -> ByteStreamer:
    client = multi_clients.get(0) or StreamBot
    from main.server.stream_routes import class_cache
    streamer = class_cache.get(client)
    if streamer is None:
        streamer = ByteStreamer(client)
        class_cache[client] = streamer
    return streamer


@routes.get(r"/api/photos/file/{message_id:\d+}", allow_head=True)
async def photo_file(request: web.Request) -> web.StreamResponse:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    message_id = int(request.match_info["message_id"])

    channel_doc = await _channel_doc(user_id)
    if not channel_doc or channel_doc.get("status") != "active":
        raise web.HTTPServiceUnavailable(
            text="Channel disconnected — reconnect to stream originals",
            headers={"Retry-After": "0"},
        )
    # Scoped to the active binding's channel: a stale doc from a previously
    # disconnected channel (same per-channel message id) must not stream.
    doc = await photo_store.get_photo(
        user_id, message_id, channel_id=channel_doc["channel_id"]
    )
    if not doc:
        raise web.HTTPNotFound(text="Photo not found")
    if doc.get("deleted"):
        raise web.HTTPGone(text="Photo is in the trash")

    file_id_str = doc.get("file_id")
    if not file_id_str:
        raise web.HTTPNotFound(text="File reference missing")
    from pyrogram.file_id import FileId
    try:
        file_id = FileId.decode(file_id_str)
    except Exception:
        raise web.HTTPNotFound(text="File reference invalid")

    # FileId.decode does not carry the media size (see get_file_ids in
    # file_properties.py) — use the size persisted at ingest.
    file_size = int(doc.get("size") or 0) or int(getattr(file_id, "file_size", 0) or 0)
    range_header = "Range" in request.headers
    try:
        http_range = request.http_range
    except ValueError:
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{max(0, file_size)}"}
        )
    return await _stream_bytes(
        request, file_id, file_size, http_range, range_header,
        mime=doc.get("mime") or "application/octet-stream",
        file_name=doc.get("file_name") or "photo",
    )


async def _stream_bytes(
    request: web.Request,
    file_id,
    file_size: int,
    http_range: slice,
    range_header: bool,
    *,
    mime: str,
    file_name: str,
) -> web.Response:
    from main.utils.stream_range import chunk_size, offset_fix

    if file_size <= 0:
        if range_header:
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": "bytes */0"})
        return web.Response(
            status=200,
            body=b"",
            headers={
                "Content-Type": mime,
                "Content-Disposition": _inline_disposition(file_name),
                "Content-Length": "0",
            },
        )
    # Telegram file metadata always carries a size; without it we cannot
    # compute ranges, so refuse rather than serve a broken body.
    start = http_range.start
    stop = http_range.stop
    if start is not None and start < 0:
        suffix = min(-start, file_size)
        if suffix <= 0:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{file_size}"}
            )
        from_b, until_b = file_size - suffix, file_size - 1
    else:
        from_b = start or 0
        until_b = (stop if stop is not None else file_size) - 1
        if from_b >= file_size or until_b < from_b:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{file_size}"}
            )
        until_b = min(until_b, file_size - 1)

    streamer = _class_streamer()
    # Probing the session first mirrors stream_routes.media_streamer: a
    # dead media session must 503 cleanly instead of erroring mid-stream.
    try:
        await streamer.generate_media_session(streamer.client, file_id)
    except MediaSessionUnavailable as exc:
        logging.warning("photos file route: media session unavailable: %s", exc)
        raise web.HTTPServiceUnavailable(
            text="Media session unavailable; retry",
            headers={"Retry-After": "5"},
        )
    req_length = until_b - from_b + 1
    cs = chunk_size(req_length)
    offset = offset_fix(from_b, cs)
    first_part_cut = from_b - offset
    last_part_cut = (until_b % cs) + 1
    part_count = (until_b // cs) - (from_b // cs) + 1

    body = streamer.yield_file(
        file_id, 0, offset, first_part_cut, last_part_cut, part_count, cs
    )
    status = 206 if range_header else 200
    headers = {
        "Content-Type": mime,
        "Accept-Ranges": "bytes",
        "Content-Disposition": _inline_disposition(file_name),
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {from_b}-{until_b}/{file_size}"
        headers["Content-Length"] = str(max(0, until_b - from_b + 1))
    else:
        headers["Content-Length"] = str(file_size)
    return web.Response(status=status, body=body, headers=headers)


def _inline_disposition(file_name: str) -> str:
    from urllib.parse import quote
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";"} else "_"
        for ch in (file_name or "")
    ).strip(" .") or "photo"
    return f'inline; filename="{fallback}"; filename*=UTF-8\'\'{quote(file_name or fallback, safe="")}'


# ── Upload ────────────────────────────────────────────────────────────────


@routes.post("/api/photos/upload")
async def photos_upload(request: web.Request) -> web.Response:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    channel_doc = await _channel_doc(user_id)
    if not channel_doc or channel_doc.get("status") != "active":
        return _json({"error": "Connect a channel first"}, status=400)
    channel_id = channel_doc["channel_id"]
    count = await photo_store.count_photos(user_id)

    bot = multi_clients.get(0) or StreamBot
    reader = await request.multipart()
    results = []
    sent = 0
    files = 0
    album_id = ""
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "album_id":
            album_id = (await part.text()).strip()
            continue
        if part.name != "file":
            # Drain unknown parts so the reader stays in sync.
            while True:
                chunk = await part.read_chunk(65536)
                if not chunk:
                    break
                continue
            continue
        if files >= Var.PHOTOS_UPLOAD_MAX_FILES:
            results.append({"fileName": part.filename or "?", "error": "Too many files in one request"})
            while True:
                chunk = await part.read_chunk(262144)
                if not chunk:
                    break
                continue
            continue
        size = 0
        hasher = hashlib.sha256()
        # Buffer each file's bytes in memory for the sha + send call —
        # aiohttp gives us a stream; we need one pass to send as document.
        # Per-file cap bounds process memory: concurrent authenticated
        # requests each buffer one file at a time.
        payload = bytearray()
        per_file_overflow = False
        while True:
            chunk = await part.read_chunk(262144)
            if not chunk:
                break
            size += len(chunk)
            if size > Var.PHOTOS_UPLOAD_MAX_FILE:
                per_file_overflow = True
                hasher = hashlib.sha256()
                payload.clear()
                continue  # keep draining the stream to keep multipart in sync
            payload.extend(chunk)
            hasher.update(chunk)
            sent += len(chunk)
            if sent > Var.PHOTOS_UPLOAD_MAX_TOTAL:
                return _json(
                    {"error": "Total upload size exceeded", "results": results},
                    status=413,
                )
        if size == 0:
            continue
        if per_file_overflow:
            results.append({
                "fileName": part.filename or "?",
                "error": f"File exceeds the {Var.PHOTOS_UPLOAD_MAX_FILE // (1024 * 1024)} MB per-file limit",
            })
            continue
        files += 1
        sha = hasher.hexdigest()
        existing = await photo_store.find_by_sha(user_id, sha)
        if existing:
            results.append({
                "fileName": part.filename or "?",
                "duplicate": True,
                "id": str(existing["_id"]),
            })
            continue
        if count >= Var.PHOTOS_PER_USER_CAP:
            results.append({"fileName": part.filename or "?", "error": "Library cap reached"})
            continue
        if album_id:
            album = await photo_store.get_album(user_id, album_id)
            if not album:
                album_id = ""  # stale/foreign id — drop rather than fail the batch
        try:
            # send_document has no file_name kwarg — name the bytes via a
            # file-like object's .name, which Pyrogram uses as the caption
            # filename.
            source = io.BytesIO(bytes(payload))
            source.name = part.filename or "upload.bin"
            msg = await bot.send_document(chat_id=channel_id, document=source)
        except Exception as exc:
            logging.warning("photos upload: send_document failed uid=%d: %s", user_id, exc)
            results.append({"fileName": part.filename or "?", "error": "Telegram send failed"})
            continue
        count += 1
        # Record the album tag. The async channel_post ingest may not have
        # inserted the photo doc yet — retry briefly so the tag isn't lost
        # (ingest merges album_ids, never overwrites).
        if album_id:
            for _ in range(10):
                await photo_store.append_album_ids(user_id, msg.id, [album_id])
                tagged = await photo_store.get_photo(
                    user_id, msg.id, channel_id=channel_id
                )
                if tagged and album_id in (tagged.get("album_ids") or []):
                    break
                await asyncio.sleep(0.5)
        # The channel_post plugin ingests asynchronously; we still return
        # the message id so the UI can link the upload.
        results.append({
            "fileName": part.filename or "?",
            "sha256": sha,
            "messageId": msg.id,
            "albumId": album_id or None,
            "duplicate": False,
        })
    return _json({"results": results})


# ── Internal: called by the channel_post plugin on re-verify ─────────────


async def reverify_channel(owner_user_id: int) -> Optional[str]:
    """Periodic re-verify. Returns new status, None when the doc vanished."""
    doc = await photo_store.get_channel_by_owner(owner_user_id)
    if not doc:
        return None
    channel_id = doc["channel_id"]
    verified, _reason = await _verify_channel_access(channel_id, doc.get("creator_user_id") or owner_user_id)
    status = "active" if verified else "disconnected"
    await photo_store.set_channel_status(channel_id, status)
    _invalidate_channel_cache(owner_user_id)
    return status

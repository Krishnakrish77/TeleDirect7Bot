"""TeleDirect Photos HTTP API — session-guarded, owner-scoped.

Every handler resolves the user from the ``td_session`` JWT and filters all
reads/writes by that user id server-side. Photos never appear on the public
bearer-by-hash stream routes.

Serving:
  * thumbs  — generated webp from Mongo (photo_thumbs)
  * originals — /api/photos/file/{photo_id}: ownership check, fresh file
    reference from the live channel message, then byte-range stream from
    the user's own channel via ByteStreamer.

Uploads stream through the multipart reader directly into the bot's
send_document call — original bytes never touch disk.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import io
import json
import logging
import math
import re
import time
import weakref
from typing import Optional

from aiohttp import web

from main import Var
from main.bot import StreamBot, multi_clients
from main.utils import photo_store
from main.utils.custom_dl import ByteStreamer, MediaSessionUnavailable
from main.utils.user_auth import get_user

routes = web.RouteTableDef()

# Channel input: @username, t.me/<username>[/<msg>] link, t.me/c/<internal>[/<msg>]
# message link, or -100… numeric id.
_CHANNEL_INPUT_RE = re.compile(r"^(?:@|https?://t\.me/)?([A-Za-z0-9_]{4,64})(?:/\d+)?$")
_CHANNEL_LINK_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/c/(\d{5,14})(?:/\d+)?/?(?:\?.*)?$",
    re.IGNORECASE,
)
_CHANNEL_ID_RE = re.compile(r"^-100\d{6,}$")
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$")

_channels_cache_ttl = 30.0
_channels_cache: dict[int, tuple[float, dict]] = {}


def _json_default(value):
    """Fallback for Mongo values that are not JSON-native.

    Routes serialise explicitly, but one stray datetime or ObjectId must not
    take a whole endpoint down with a 500 (that is exactly what the raw scan
    subdocument did).
    """
    if isinstance(value, (_dt.datetime, _dt.date)):
        return photo_store.iso_utc(value)
    try:
        from bson import ObjectId
    except ImportError:  # pragma: no cover — bson ships with motor
        ObjectId = None
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _reject_nan(obj):
    """Convert NaN/Infinity floats to None — bare NaN is invalid JSON and the
    browser throws "Unexpected token 'N'" (documents from before the GPS
    sanitiser still carry some)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _reject_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_reject_nan(v) for v in obj]
    return obj


def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.json_response(
        data,
        status=status,
        dumps=lambda payload: json.dumps(
            _reject_nan(payload), default=_json_default, allow_nan=False
        ),
    )


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


def _bot_admin_link(bot_username: str) -> Optional[str]:
    """Telegram deep link that adds the bot to a channel with post rights."""
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?startchannel=true&admin=post_messages"


def _onboarding_payload() -> dict:
    """Bot identity the connect wizard needs to render its steps."""
    bot_username = Var.BOT_USERNAME or getattr(StreamBot, "username", "") or ""
    return {
        "botUsername": bot_username or None,
        "addToChannelUrl": _bot_admin_link(bot_username),
    }


_unavailable_logged = False


def _photos_disabled() -> Optional[web.Response]:
    global _unavailable_logged
    if not Var.PHOTOS_ENABLED:
        if not _unavailable_logged:
            _unavailable_logged = True
            logging.warning("photos: requests rejected because PHOTOS_ENABLED is off")
        return _json({"error": "Photos feature is disabled"}, status=503)
    if not photo_store.is_available():
        if not _unavailable_logged:
            _unavailable_logged = True
            logging.warning(
                "photos: requests rejected because MongoDB is unavailable — "
                "check the catalogue store connection (see photo_store logs)"
            )
        return _json({"error": "MongoDB is required for photos"}, status=503)
    return None


# ── Channel resolution + verification ─────────────────────────────────────


def _parse_channel_input(raw: str) -> Optional[int | str]:
    """Return an int channel id or a username string, None if malformed.

    Accepted forms, easiest first:
      * ``t.me/c/<internal_id>/<message>`` — the "Copy Link" URL of any post
        in a private channel. The internal id is the channel id without the
        ``-100`` prefix, so users never have to know the numeric id.
      * ``-100…`` numeric channel id.
      * ``@username`` / ``t.me/<username>[/<message>]`` — resolved by
        Telegram, then rejected by the privacy check if the channel is public.
    """
    text = (raw or "").strip()
    if not text:
        return None
    link = _CHANNEL_LINK_RE.match(text)
    if link:
        return int(f"-100{link.group(1)}")
    if text.startswith("-100"):
        return int(text) if _CHANNEL_ID_RE.match(text) else None
    if text.lstrip("-").isdigit():
        return None  # raw channel ids must be -100… form
    match = _CHANNEL_INPUT_RE.match(text)
    if match:
        return match.group(1)
    return None


def _status_name(status) -> str:
    """Normalize a ChatMemberStatus to its string value.

    Pyrogram 2.x statuses are plain enums — ``ChatMemberStatus.ADMINISTRATOR
    == "administrator"`` is False, so comparisons must go through ``.value``.
    Plain strings pass through unchanged (kurigram/other forks).
    """
    return str(getattr(status, "value", status) or "")


async def _verify_channel_access(channel_ref: int | str, requester_id: int) -> tuple[Optional[dict], str, bool]:
    """Run the three trust checks from the plan (§4).

    Returns (chat_dict, "", True) on success or (None, reason, definitive).
    ``definitive`` is False when we could not get an answer from Telegram
    (FloodWait, network, RPC error) — callers that mutate state on failure
    (reverify_channel) must not treat an unknown as a negative.
    """
    bot = multi_clients.get(0) or StreamBot
    try:
        chat = await bot.get_chat(channel_ref)
    except Exception as exc:
        logging.warning(
            "photos connect: could not resolve %r for uid=%d: %r",
            channel_ref, requester_id, exc, exc_info=True,
        )
        return None, f"Could not resolve that channel: {exc}", False

    chat_id = chat.id
    # Must be a channel (broadcast), not a user/group/supergroup. Public
    # channels also carry -100 ids, so the type check is what enforces the
    # private-vault invariant — and a resolvable @username means the
    # channel is public, which must never be bound as a private vault.
    type_name = _status_name(getattr(chat, "type", "")).upper()
    if type_name != "CHANNEL":
        return None, "Only channels (not groups or users) can be used", True
    if not str(chat_id).startswith("-100"):
        return None, "Only private channels can be used", True
    if getattr(chat, "username", None):
        return None, "That channel is public — use a private channel (no @username)", True

    try:
        bot_member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
    except Exception as exc:
        # Unknown, not disproved — the bot may still be admin (RPC error).
        logging.warning(
            "photos connect: bot membership lookup failed cid=%s uid=%d: %r",
            chat_id, requester_id, exc, exc_info=True,
        )
        return None, "Add the bot as an administrator of the channel first", False
    bot_status = _status_name(getattr(bot_member, "status", ""))
    # "creator" is Telegram's raw name; pyrogram 2.x calls it "owner".
    if bot_status not in ("administrator", "creator", "owner"):
        return None, "The bot must be a channel administrator with post rights", True
    privileges = getattr(bot_member, "privileges", None)
    if bot_status == "administrator" and privileges is not None:
        if not getattr(privileges, "can_post_messages", True):
            return None, "The bot needs post-messages rights to ingest uploads", True

    try:
        member = await bot.get_chat_member(chat_id, requester_id)
    except Exception as exc:
        # Non-definitive: a transient RPC error is not proof the requester
        # lost access; reverify must not flip the channel off the back of it.
        logging.warning(
            "photos connect: requester membership lookup failed cid=%s uid=%d: %r",
            chat_id, requester_id, exc, exc_info=True,
        )
        return None, "Could not verify your membership in that channel", False
    member_status = _status_name(getattr(member, "status", ""))
    if member_status not in ("administrator", "creator", "owner"):
        return None, "You must be the channel creator (or an admin) to connect it", True

    # The requester just proved admin/creator status via get_chat_member.
    # Anonymous-admin channels can hide who the real creator is from the
    # API, so record the verified requester — re-verify (reverify_channel)
    # re-runs the same membership check, which is the actual trust bound.
    creator_id = requester_id
    return {
        "chat_id": chat_id,
        "creator_id": creator_id or requester_id,
        "title": getattr(chat, "title", "") or "",
    }, "", True


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
            {"error": "Paste a message link from the channel (⋯ → Copy Link) or its -100… id"},
            status=400,
        )
    user_id = int(user["sub"])

    verified, reason, _definitive = await _verify_channel_access(channel_ref, user_id)
    if verified is None:
        return _json({"error": reason}, status=400)

    err = await photo_store.bind_channel(
        verified["chat_id"], user_id, verified["creator_id"]
    )
    if err:
        return _json({"error": err}, status=409 if "another user" in err else 400)
    _invalidate_channel_cache(user_id)
    # The detection handshake is done; stop the wizard polling it.
    try:
        from main.bot.plugins.photos import clear_pending_link
        clear_pending_link(user_id)
    except Exception:
        logging.debug("photos connect: could not clear the pending link", exc_info=True)
    # Catch-up: index anything posted while the channel was unbound (and
    # backfill posts a previous queue drop or downtime missed).
    from main.bot.plugins.photos import schedule_rescan
    schedule_rescan(user_id, verified["chat_id"])
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
        return _json({"connected": False, **_onboarding_payload()})
    return _json({
        "connected": True,
        "channelId": doc.get("channel_id"),
        "status": doc.get("status", "active"),
        "beta": Var.PHOTOS_BETA,
        "photoCount": await photo_store.count_photos(user_id),
        # Last background import outcome, so a failed scan is visible.
        "scan": photo_store.scan_payload(doc.get("scan")),
        **_onboarding_payload(),
    })


# ── Timeline / favorites / trash ─────────────────────────────────────────


@routes.get("/api/photos/pending-channel")
async def photos_pending_channel(request: web.Request) -> web.Response:
    """The channel the bot was just added to, if any.

    Backs the wizard's "Continue": the bot receives Telegram's own membership
    update (which carries the actor), so linking needs no id or link.
    """
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    from main.bot.plugins.photos import pending_link_for

    entry = pending_link_for(user_id)
    if not entry:
        return _json({"channelId": None})
    return _json({
        "channelId": entry["channel_id"],
        "title": entry.get("title") or None,
    })


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
    try:
        min_size = int(request.rel_url.query.get("minSize", "0"))
    except ValueError:
        min_size = 0
    result = await photo_store.timeline_page(
        user_id,
        cursor=request.rel_url.query.get("cursor") or None,
        favorites=view == "favorites",
        trash=view == "trash",
        album_id=request.rel_url.query.get("album") or "",
        limit=limit,
        q=request.rel_url.query.get("q") or "",
        kind=request.rel_url.query.get("kind") or "",
        mime=request.rel_url.query.get("mime") or "",
        camera=request.rel_url.query.get("camera") or "",
        taken_after=request.rel_url.query.get("takenAfter") or "",
        taken_before=request.rel_url.query.get("takenBefore") or "",
        min_size=max(0, min_size),
    )
    return _json(result)


@routes.get("/api/photos/facets")
async def photos_facets(request: web.Request) -> web.Response:
    """Filter-chip counts (kind / camera / month) for the current context."""
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    facets = await photo_store.photo_facets(
        int(user["sub"]),
        album_id=request.rel_url.query.get("album") or "",
        q=request.rel_url.query.get("q") or "",
    )
    return _json(facets)


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
    if any(not _OBJECT_ID_RE.match(photo_id) for photo_id in ids):
        # ObjectId() would raise inside the store and be reported as
        # "modified: 0" on a 200 — a malformed request must not look like a
        # successful no-op.
        raise web.HTTPBadRequest(text="Malformed photo id")
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
    # Album id is caller-supplied — it must exist and belong to the caller,
    # or photos would carry dangling/foreign album tags.
    if not await photo_store.get_album(user_id, album_id):
        raise web.HTTPNotFound(text="Album not found")
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

# On-demand regeneration pulls the FULL original from Telegram and decodes it
# on the request path, and video posters routinely fail (ffmpeg cannot seek a
# piped mp4). Without coalescing + a failure backoff, every grid tile that
# scrolls into view would re-download the original.
_thumb_regen_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()
_thumb_regen_after: dict[str, float] = {}
_THUMB_REGEN_BACKOFF_SECONDS = 300.0


def _prune_thumb_regen_after() -> None:
    """Keep the failure memo (keyed per photo/size) bounded."""
    now = time.monotonic()
    for key in [k for k, until in _thumb_regen_after.items() if until <= now]:
        _thumb_regen_after.pop(key, None)


async def _thumb_bytes(user_id: int, channel_id: int, message_id: int,
                       size: str) -> Optional[bytes]:
    """Serve one thumb, regenerating at most once per photo/size per window.

    Concurrent requests for the same thumb share one regeneration; a failed
    regeneration is remembered for ``_THUMB_REGEN_BACKOFF_SECONDS`` so an
    unrenderable video poster cannot be retried on every scroll.
    """
    key = photo_store.thumb_key(channel_id, message_id, size)
    data = await photo_store.get_thumb(key)
    if data:
        return data
    if time.monotonic() < _thumb_regen_after.get(key, 0.0):
        logging.debug("photos thumb: regeneration for %s is in back-off", key)
        return None
    lock = _thumb_regen_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _thumb_regen_locks[key] = lock
    async with lock:
        # Another waiter may have generated it while we queued.
        data = await photo_store.get_thumb(key)
        if not data:
            from main.utils.photo_pipeline import generate_thumbs_for
            data = await generate_thumbs_for(user_id, channel_id, message_id, size)
    if data:
        _thumb_regen_after.pop(key, None)
    else:
        # Persistent 404s are then answerable from the logs instead of
        # looking like a permanently missing thumbnail.
        logging.warning(
            "photos thumb: regeneration produced nothing for %s; backing off %.0fs",
            key, _THUMB_REGEN_BACKOFF_SECONDS,
        )
        _thumb_regen_after[key] = time.monotonic() + _THUMB_REGEN_BACKOFF_SECONDS
        _prune_thumb_regen_after()
    return data


@routes.get(r"/api/photos/thumb/{photo_id}/{size:grid|preview}")
async def photo_thumb(request: web.Request) -> web.StreamResponse:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    photo_id = request.match_info["photo_id"]
    size = request.match_info["size"]

    # Doc-id keyed (message ids are channel-scoped and can collide after a
    # reconnect). Ownership is enforced by the owner filter in the lookup.
    doc = await photo_store.get_photo_by_id(user_id, photo_id)
    if not doc:
        raise web.HTTPNotFound(text="Photo not found")
    # Same contract as the byte route: a trashed photo is gone, not merely
    # hidden from the timeline.
    if doc.get("deleted"):
        raise web.HTTPGone(text="Photo is in the trash")
    data = await _thumb_bytes(
        user_id, doc.get("channel_id"), doc.get("message_id"), size
    )
    if not data:
        raise web.HTTPNotFound(text="Thumbnail unavailable")
    return web.Response(
        body=data,
        content_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ── Original bytes ────────────────────────────────────────────────────────


async def _choose_streamer(file_id) -> tuple[int, ByteStreamer]:
    """Least-loaded client that holds a live media session for this file.

    Photo originals are Telegram GetFile calls just like catalogue streams,
    so they must go through the catalogue's selection (``_client_indexes``)
    and cooldown bookkeeping: pinning them to ``multi_clients[0]`` would
    bypass balancing and inflate that client's ``work_loads`` entry, which
    the catalogue sorts on.
    """
    from main.server import stream_routes as _sr
    last_exc: Optional[Exception] = None
    for index in _sr._client_indexes():
        _client, streamer = _sr._streamer_for_index(index)
        try:
            await streamer.generate_media_session(streamer.client, file_id)
            return index, streamer
        except MediaSessionUnavailable as exc:
            last_exc = exc
            _sr._mark_client_cooldown(index, f"photos file: {exc}")
    raise web.HTTPServiceUnavailable(
        text="Media session unavailable; retry",
        headers={"Retry-After": "5"},
    ) from last_exc


async def _fresh_file_id(doc: dict, channel_id: int) -> str:
    """Re-derive the file id from the live channel message.

    Falls back to the stored (possibly expired) id when the message can't
    be fetched right now — the decode/stream attempt then fails honestly
    rather than the route pretending the file is gone.
    """
    stored = str(doc.get("file_id") or "")
    message_id = doc.get("message_id")
    if not message_id:
        return stored
    bot = multi_clients.get(0) or StreamBot
    try:
        msg = await bot.get_messages(channel_id, int(message_id))
        media = msg and (msg.document or msg.video or msg.photo)
        fresh = str(getattr(media, "file_id", "") or "")
    except Exception as exc:
        logging.info("photos file: refresh failed mid=%s: %s", message_id, exc)
        return stored
    if fresh and fresh != stored:
        # Persist so future requests start from a valid reference and the
        # regen path (generate_thumbs_for) sees it too.
        await photo_store.update_file_id(
            doc["owner_user_id"], channel_id, int(message_id), fresh
        )
    return fresh or stored


@routes.get(r"/api/photos/file/{photo_id}", allow_head=True)
async def photo_file(request: web.Request) -> web.StreamResponse:
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    photo_id = request.match_info["photo_id"]

    # Keyed by the photo document id — Telegram message ids are
    # channel-scoped and can collide after a channel reconnect.
    doc = await photo_store.get_photo_by_id(user_id, photo_id)
    if not doc:
        raise web.HTTPNotFound(text="Photo not found")
    if doc.get("deleted"):
        raise web.HTTPGone(text="Photo is in the trash")

    channel_doc = await _channel_doc(user_id)
    if not channel_doc or channel_doc.get("status") != "active":
        raise web.HTTPServiceUnavailable(
            text="Channel disconnected — reconnect to stream originals",
            headers={"Retry-After": "0"},
        )
    if doc.get("channel_id") != channel_doc.get("channel_id"):
        raise web.HTTPGone(text="Photo belongs to a previously disconnected channel")

    # Stored file_ids embed a Telegram file_reference that EXPIRES — a doc
    # ingested days ago will fail GetFile with FILE_REFERENCE_EXPIRED.
    # Refresh from the live channel message (same trick the catalogue's
    # _resolve_file uses), falling back to the stored id when the message
    # is momentarily unreachable.
    file_id_str = await _fresh_file_id(doc, channel_doc["channel_id"])
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

    if request.method == "HEAD":
        # StreamResponse.write() ignores must_be_empty_body (aiohttp only
        # honors it in Response.write_eof), so streaming for a HEAD would
        # pull the whole range from Telegram and emit a body after the
        # headers. Answer from headers alone, like media_streamer does.
        headers = {
            "Content-Type": mime,
            "Accept-Ranges": "bytes",
            "Content-Disposition": _inline_disposition(file_name),
            "Content-Length": str(until_b - from_b + 1),
        }
        if range_header:
            headers["Content-Range"] = f"bytes {from_b}-{until_b}/{file_size}"
        return web.Response(status=206 if range_header else 200, headers=headers)

    # Probing the session first mirrors stream_routes.media_streamer: a
    # dead media session must 503 cleanly instead of erroring mid-stream.
    index, streamer = await _choose_streamer(file_id)

    # Share the catalogue's stream-slot budget — photos originals are
    # Telegram GetFile calls just like catalogue streams, so unbounded
    # concurrent photo streams could starve the media hub.
    from main.server import stream_routes as _sr
    client_ip = _sr._real_ip(request)
    is_loopback = client_ip in _sr._LOOPBACK
    if _sr._total_active >= _sr._MAX_STREAMS_TOTAL:
        raise web.HTTPServiceUnavailable(
            text="Server is at stream capacity. Try again shortly.",
            headers={"Retry-After": "10"},
        )
    if not is_loopback and _sr._ip_active.get(client_ip, 0) >= _sr._MAX_STREAMS_PER_IP:
        raise web.HTTPTooManyRequests(
            text="Too many concurrent streams from this IP.",
            headers={"Retry-After": "5"},
        )
    _sr._total_active += 1
    if not is_loopback:
        _sr._ip_active[client_ip] = _sr._ip_active.get(client_ip, 0) + 1

    req_length = until_b - from_b + 1
    cs = chunk_size(req_length)
    offset = offset_fix(from_b, cs)
    first_part_cut = from_b - offset
    last_part_cut = (until_b % cs) + 1
    part_count = (until_b // cs) - (from_b // cs) + 1

    body = streamer.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, cs
    )
    status = 206 if range_header else 200
    # yield_file is an async generator — stream it with StreamResponse
    # (web.Response(body=...) accepts only bytes/Payload, not async iterables).
    resp = web.StreamResponse(status=status, headers={
        "Content-Type": mime,
        "Accept-Ranges": "bytes",
        "Content-Disposition": _inline_disposition(file_name),
    })
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {from_b}-{until_b}/{file_size}"
    resp.content_length = max(0, until_b - from_b + 1)
    try:
        # prepare() is inside the try: a client that disconnects between the
        # admission check and the header write must still return its slot,
        # or the global budget leaks until restart.
        await resp.prepare(request)
        async for chunk in body:
            await resp.write(chunk)
        await resp.write_eof()
    finally:
        # Slot must be released on every exit path (success, error, client
        # disconnect) or the shared budget leaks.
        _sr._release_stream_slot(client_ip)
    return resp


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
        # Every drained byte counts toward the per-request total —
        # including bytes of files rejected by any cap below.
        payload = bytearray()
        per_file_overflow = False
        while True:
            chunk = await part.read_chunk(262144)
            if not chunk:
                break
            size += len(chunk)
            sent += len(chunk)
            if sent > Var.PHOTOS_UPLOAD_MAX_TOTAL:
                return _json(
                    {"error": "Total upload size exceeded", "results": results},
                    status=413,
                )
            if size > Var.PHOTOS_UPLOAD_MAX_FILE:
                per_file_overflow = True
                hasher = hashlib.sha256()
                payload.clear()
                continue  # keep draining the stream to keep multipart in sync
            payload.extend(chunk)
            hasher.update(chunk)
        if size == 0:
            continue
        if per_file_overflow:
            results.append({
                "fileName": part.filename or "?",
                "error": f"File exceeds the {Var.PHOTOS_UPLOAD_MAX_FILE // (1024 * 1024)} MB per-file limit",
            })
            continue
        # Vaults hold images and videos only — same allowlist the ingest
        # pipeline enforces. Reject early so the user isn't told an upload
        # succeeded when it would never produce a Photos record.
        mime = (part.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not (mime.startswith("image/") or mime.startswith("video/")):
            results.append({
                "fileName": part.filename or "?",
                "error": f"Unsupported file type: {mime or 'unknown'} (images and videos only)",
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
        data = bytes(payload)
        file_name = part.filename or "upload.bin"
        try:
            # send_document has no file_name kwarg — name the bytes via a
            # file-like object's .name, which Pyrogram uses as the caption
            # filename.
            source = io.BytesIO(data)
            source.name = file_name
            msg = await bot.send_document(chat_id=channel_id, document=source)
        except Exception as exc:
            logging.warning("photos upload: send_document failed uid=%d: %s", user_id, exc)
            results.append({"fileName": file_name, "error": "Telegram send failed"})
            continue
        count += 1
        # Index the bytes we already hold. Telegram does not send the bot a
        # channel_post for its own message, so waiting for that echo left
        # uploads sitting in the channel and never in the library.
        media = getattr(msg, "document", None) or getattr(msg, "video", None) or getattr(msg, "photo", None)
        file_id = str(getattr(media, "file_id", "") or "")
        if not file_id:
            # send_document to a channel sometimes returns a stub message
            # with media unpopulated. Re-fetch — the message IS there (the
            # bytes landed), only the returned object was thin.
            try:
                refetched = await bot.get_messages(channel_id, msg.id)
                media = (
                    getattr(refetched, "document", None)
                    or getattr(refetched, "video", None)
                    or getattr(refetched, "photo", None)
                )
                file_id = str(getattr(media, "file_id", "") or "")
            except Exception:
                logging.warning(
                    "photos upload: re-fetch of sent message failed uid=%d mid=%d",
                    user_id, msg.id, exc_info=True,
                )
        from main.utils import photo_pipeline
        ingest_error = None
        if not file_id:
            ingest_error = "Telegram did not return a file reference"
            logging.warning(
                "photos upload: no file id on the sent message uid=%d mid=%d", user_id, msg.id
            )
        else:
            try:
                ingest_error = await photo_pipeline.ingest_bytes(
                    user_id, channel_id, msg.id,
                    file_id=file_id, data=data, mime=mime, file_name=file_name,
                )
            except Exception as exc:
                logging.exception("photos upload: ingest failed uid=%d mid=%d", user_id, msg.id)
                ingest_error = f"indexing failed: {exc}"
        if ingest_error:
            logging.warning(
                "photos upload: %s uid=%d mid=%d", ingest_error, user_id, msg.id
            )
        # Keep the backfill cursor honest: uploads to a fresh channel land
        # at ids BELOW a cursor parked there by connect-time empty scans.
        # Rewind so the next rescan re-covers this id (self-heals a failed
        # self-index), and kick a scan now when the self-index failed.
        await photo_store.rewind_scan_cursor(channel_id, msg.id)
        if ingest_error:
            from main.bot.plugins.photos import schedule_rescan
            schedule_rescan(user_id, channel_id)
        if album_id:
            # The doc exists by now (or is a duplicate of an earlier upload),
            # so one merge is enough — no polling loop needed.
            await photo_store.append_album_ids(user_id, channel_id, msg.id, [album_id])
        results.append({
            "fileName": file_name,
            "sha256": sha,
            "messageId": msg.id,
            "albumId": album_id or None,
            "duplicate": False,
            "indexed": ingest_error is None,
            **({"error": f"Saved to Telegram, but not indexed: {ingest_error}"} if ingest_error else {}),
        })
    return _json({"results": results})


# ── Internal: called by the channel_post plugin on re-verify ─────────────


@routes.post("/api/photos/resync")
async def photos_resync(request: web.Request) -> web.Response:
    """Re-scan the bound channel's history and ingest anything missing.

    Covers posts made while the bot was down or dropped by a full ingest
    queue — the gallery must eventually reflect the whole vault.
    """
    disabled = _photos_disabled()
    if disabled:
        return disabled
    user = _require_user(request)
    user_id = int(user["sub"])
    channel_doc = await _channel_doc(user_id)
    if not channel_doc or channel_doc.get("status") != "active":
        return _json({"error": "Connect a channel first"}, status=400)
    from main.bot.plugins.photos import schedule_rescan
    schedule_rescan(user_id, channel_doc["channel_id"], explicit=True)
    return _json({"ok": True})


async def reverify_channel(owner_user_id: int) -> Optional[str]:
    """Periodic re-verify. Returns new status, None when the doc vanished.

    Only flips to ``disconnected`` on a DEFINITIVE negative (we fetched the
    member record and the bot/owner lost the required role). Transient
    Telegram errors leave the status untouched — an hourly FloodWait must
    not take a healthy library's originals offline.
    """
    doc = await photo_store.get_channel_by_owner(owner_user_id)
    if not doc:
        return None
    channel_id = doc["channel_id"]
    verified, _reason, definitive = await _verify_channel_access(channel_id, doc.get("creator_user_id") or owner_user_id)
    if verified:
        status = "active"
    elif definitive:
        status = "disconnected"
    else:
        return doc.get("status", "active")  # unknown — keep current status
    await photo_store.set_channel_status(channel_id, status)
    _invalidate_channel_cache(owner_user_id)
    return status

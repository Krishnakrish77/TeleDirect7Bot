"""IPTV channel catalogue API for the React Live TV experience."""

from __future__ import annotations

import json
import re

from aiohttp import web

from main.utils import iptv_store
from main.utils.user_auth import get_user


routes = web.RouteTableDef()


def _json(data, *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, separators=(",", ":")),
        content_type="application/json",
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def _require_admin(request: web.Request) -> dict:
    user = get_user(request)
    if not user or not user.get("is_admin"):
        raise web.HTTPForbidden(text="Admin access required")
    return user


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _channel_payload(data: dict, *, channel_id: str = "") -> dict:
    return {
        "channel_id": channel_id or data.get("id") or data.get("channel_id") or "",
        "name": data.get("name", ""),
        "stream_url": data.get("streamUrl") or data.get("stream_url") or "",
        "logo_url": data.get("logoUrl") or data.get("logo_url") or "",
        "category": data.get("category", ""),
        "enabled": data.get("enabled", True),
        "sort_order": data.get("sortOrder") if data.get("sortOrder") is not None else data.get("sort_order", 0),
    }


@routes.get("/api/live-tv/channels")
async def live_tv_channels(_: web.Request) -> web.Response:
    channels = await iptv_store.list_channels(include_disabled=False)
    return _json({"channels": channels})


@routes.get("/api/live-tv/channel/{channel_id}")
async def live_tv_channel(request: web.Request) -> web.Response:
    channel = await iptv_store.get_channel(request.match_info["channel_id"], include_disabled=False)
    if not channel:
        raise web.HTTPNotFound(text="Channel not found")
    return _json({"channel": channel})


@routes.get("/api/app/admin/iptv")
async def admin_iptv(request: web.Request) -> web.Response:
    _require_admin(request)
    channels = await iptv_store.list_channels(include_disabled=True)
    return _json({"channels": channels, "mongoAvailable": iptv_store.is_mongo_available()})


@routes.post("/api/app/admin/iptv/channel")
async def admin_iptv_create(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    ok, channel, message = await iptv_store.save_channel(_channel_payload(data))
    if not ok:
        return _json({"ok": False, "error": message}, status=400)
    channels = await iptv_store.list_channels(include_disabled=True)
    return _json({"ok": True, "channel": channel, "channels": channels})


@routes.patch("/api/app/admin/iptv/channel/{channel_id}")
async def admin_iptv_update(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    ok, channel, message = await iptv_store.save_channel(
        _channel_payload(data, channel_id=request.match_info["channel_id"])
    )
    if not ok:
        return _json({"ok": False, "error": message}, status=400)
    channels = await iptv_store.list_channels(include_disabled=True)
    return _json({"ok": True, "channel": channel, "channels": channels})


@routes.delete("/api/app/admin/iptv/channel/{channel_id}")
async def admin_iptv_delete(request: web.Request) -> web.Response:
    _require_admin(request)
    deleted = await iptv_store.delete_channel(request.match_info["channel_id"])
    channels = await iptv_store.list_channels(include_disabled=True)
    return _json({"ok": deleted, "channels": channels})


@routes.post("/api/app/admin/iptv/import-m3u")
async def admin_iptv_import_m3u(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    text = str(data.get("m3u") or data.get("text") or data.get("content") or "")
    if not text.strip():
        return _json({"ok": False, "error": "M3U content is required"}, status=400)
    result = await iptv_store.import_m3u(text)
    channels = await iptv_store.list_channels(include_disabled=True)
    return _json({"ok": True, **result, "channels": channels})


@routes.post("/api/app/admin/iptv/test")
async def admin_iptv_test(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    stream_url = str(data.get("streamUrl") or data.get("stream_url") or "").strip()
    ok = bool(re.match(r"^https?://", stream_url, re.IGNORECASE))
    return _json({"ok": ok, "message": "URL accepted" if ok else "A valid http(s) stream URL is required"}, status=200 if ok else 400)

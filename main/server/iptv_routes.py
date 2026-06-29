"""IPTV channel catalogue API for the React Live TV experience."""

from __future__ import annotations

import json
import os
import re
from ipaddress import ip_address
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp import web

from main.utils import iptv_store
from main.utils.user_auth import get_user


routes = web.RouteTableDef()
_IMPORT_MAX_BYTES = int(os.environ.get("IPTV_IMPORT_MAX_BYTES", str(25 * 1024 * 1024)))
_IMPORT_TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=10, sock_read=20)
_REDIRECT_LIMIT = 4


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


def _normalise_import_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        raise ValueError("Playlist URL is required")
    parsed = urlparse(url)
    if parsed.hostname == "github.com" and "/blob/" in parsed.path:
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _, branch, *path = parts
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{'/'.join(path)}"
            parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("A valid http(s) playlist URL is required")
    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local playlist URLs are not allowed")
    try:
        host_ip = ip_address(hostname)
    except ValueError:
        return url
    if (
        host_ip.is_private
        or host_ip.is_loopback
        or host_ip.is_link_local
        or host_ip.is_multicast
        or host_ip.is_unspecified
    ):
        raise ValueError("Private playlist URLs are not allowed")
    return url


def _looks_like_m3u(text: str) -> bool:
    preview = text.lstrip("\ufeff\r\n\t ")[:4096].upper()
    return preview.startswith("#EXTM3U") or "#EXTINF" in preview


async def _fetch_m3u_url(url: str) -> tuple[str, str]:
    current = _normalise_import_url(url)
    async with aiohttp.ClientSession(timeout=_IMPORT_TIMEOUT) as session:
        for _attempt in range(_REDIRECT_LIMIT + 1):
            async with session.get(current, allow_redirects=False) as response:
                if 300 <= response.status < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("Playlist URL redirected without a location")
                    current = _normalise_import_url(urljoin(current, location))
                    continue
                if response.status >= 400:
                    raise ValueError(f"Playlist URL returned HTTP {response.status}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > _IMPORT_MAX_BYTES:
                        raise ValueError("Playlist is too large to import")
                    chunks.append(chunk)
                text = b"".join(chunks).decode(response.charset or "utf-8", errors="replace")
                if not _looks_like_m3u(text):
                    if ".m3u" in text.lower():
                        raise ValueError("That URL looks like a playlist index. Import a specific .m3u URL from it.")
                    raise ValueError("URL did not return M3U playlist content")
                return text, str(response.url)
    raise ValueError("Playlist URL redirected too many times")


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


@routes.post("/api/app/admin/iptv/import-m3u-url")
async def admin_iptv_import_m3u_url(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    url = str(data.get("url") or data.get("playlistUrl") or data.get("playlist_url") or "")
    try:
        text, source_url = await _fetch_m3u_url(url)
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return _json({"ok": False, "error": f"Unable to fetch playlist URL: {type(exc).__name__}"}, status=400)
    result = await iptv_store.import_m3u(text)
    channels = await iptv_store.list_channels(include_disabled=True)
    return _json({"ok": True, **result, "sourceUrl": source_url, "channels": channels})


@routes.post("/api/app/admin/iptv/test")
async def admin_iptv_test(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    stream_url = str(data.get("streamUrl") or data.get("stream_url") or "").strip()
    ok = bool(re.match(r"^https?://", stream_url, re.IGNORECASE))
    return _json({"ok": ok, "message": "URL accepted" if ok else "A valid http(s) stream URL is required"}, status=200 if ok else 400)

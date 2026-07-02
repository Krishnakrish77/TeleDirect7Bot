"""IPTV channel catalogue API for the React Live TV experience."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from ipaddress import ip_address
from urllib.parse import quote, urljoin, urlparse

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver

from main.utils import iptv_store
from main.utils.user_auth import get_user


routes = web.RouteTableDef()
_IMPORT_MAX_BYTES = int(os.environ.get("IPTV_IMPORT_MAX_BYTES", str(25 * 1024 * 1024)))
_IMPORT_TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=10, sock_read=20)
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60)
_LOGO_TIMEOUT = aiohttp.ClientTimeout(total=10, sock_connect=5, sock_read=8)
_HLS_MANIFEST_MAX_BYTES = int(os.environ.get("IPTV_HLS_MANIFEST_MAX_BYTES", str(2 * 1024 * 1024)))
_LOGO_MAX_BYTES = int(os.environ.get("IPTV_LOGO_MAX_BYTES", str(512 * 1024)))
_LOGO_CACHE_TTL_SECONDS = int(os.environ.get("IPTV_LOGO_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
_LOGO_CACHE_MAX_ITEMS = int(os.environ.get("IPTV_LOGO_CACHE_MAX_ITEMS", "256"))
_REDIRECT_LIMIT = 4
# Well-known wildcard DNS services that map embedded IPs to hostnames
# (e.g. 10.0.0.1.nip.io → 10.0.0.1) — used as SSRF pivots.
_REBINDING_DOMAINS = frozenset({"nip.io", "sslip.io", "xip.io", "traefik.me"})
_FORBIDDEN_STREAM_HEADER_KEYS = {"host", "connection", "content-length", "transfer-encoding"}
_HLS_RE = re.compile(r"\.m3u8(?:[?#]|$)|[?&](?:type|format)=m3u8", re.IGNORECASE)
_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')
_LOGO_EXTENSION_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".svgz": "image/svg+xml",
    ".webp": "image/webp",
}
_LOGO_CACHE: dict[str, tuple[float, str, bytes]] = {}


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
    payload = {
        "channel_id": channel_id or data.get("id") or data.get("channel_id") or "",
        "name": data.get("name", ""),
        "stream_url": data.get("streamUrl") or data.get("stream_url") or "",
        "logo_url": data.get("logoUrl") or data.get("logo_url") or "",
        "category": data.get("category", ""),
        "enabled": data.get("enabled", True),
        "sort_order": data.get("sortOrder") if data.get("sortOrder") is not None else data.get("sort_order", 0),
    }
    passthrough = (
        ("tvg_id", "tvgId"),
        ("tvg_name", "tvgName"),
        ("duration",),
        ("attrs",),
        ("extras",),
        ("stream_headers", "streamHeaders"),
    )
    for keys in passthrough:
        for key in keys:
            if key in data:
                payload[keys[0]] = data[key]
                break
    return payload


def _logo_cache_key(channel_id: str, logo_url: str) -> str:
    digest = hashlib.sha256(logo_url.encode("utf-8")).hexdigest()[:16]
    return f"{channel_id}:{digest}"


def _logo_proxy_url(channel: dict) -> str:
    logo_url = str(channel.get("logoUrl") or "").strip()
    channel_id = str(channel.get("id") or "").strip()
    if not logo_url or not channel_id:
        return ""
    digest = hashlib.sha256(logo_url.encode("utf-8")).hexdigest()[:16]
    return f"/api/live-tv/logo/{quote(channel_id, safe='')}?v={digest}"


def _with_proxied_logo(channel: dict) -> dict:
    logo_url = _logo_proxy_url(channel)
    if not logo_url:
        return channel
    return {**channel, "logoUrl": logo_url}


def _normalise_logo_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        raise ValueError("Logo URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("A valid http(s) logo URL is required")
    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local logo URLs are not allowed")
    for _rd in _REBINDING_DOMAINS:
        if hostname == _rd or hostname.endswith("." + _rd):
            raise ValueError("Logo URL uses a DNS rebinding service — use a direct address")
    try:
        host_ip = ip_address(hostname)
    except ValueError:
        return url
    if not _is_public_import_ip(host_ip):
        raise ValueError("Private logo URLs are not allowed")
    return url


def _logo_content_type(raw_content_type: str, source_url: str) -> str:
    content_type = raw_content_type.split(";", 1)[0].strip().lower()
    path = urlparse(source_url).path.lower()
    extension = os.path.splitext(path)[1]
    guessed = _LOGO_EXTENSION_CONTENT_TYPES.get(extension, "")
    if content_type.startswith("image/"):
        return "image/svg+xml" if content_type == "image/svg" else content_type
    if guessed and content_type in {"", "application/octet-stream", "binary/octet-stream", "text/plain"}:
        return guessed
    raise ValueError("Logo URL did not return image content")


def _prune_logo_cache(now: float) -> None:
    for key, (expires_at, _, _) in list(_LOGO_CACHE.items()):
        if expires_at <= now:
            _LOGO_CACHE.pop(key, None)
    while _LOGO_CACHE_MAX_ITEMS > 0 and len(_LOGO_CACHE) >= _LOGO_CACHE_MAX_ITEMS:
        oldest_key = min(_LOGO_CACHE.items(), key=lambda item: item[1][0])[0]
        _LOGO_CACHE.pop(oldest_key, None)


async def _fetch_logo(channel_id: str, logo_url: str) -> tuple[str, bytes]:
    cache_key = _logo_cache_key(channel_id, logo_url)
    now = time.time()
    cached = _LOGO_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    current = _normalise_logo_url(logo_url)
    resolver = _SafePublicResolver(message="Private logo URLs are not allowed")
    connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=0)
    try:
        async with aiohttp.ClientSession(timeout=_LOGO_TIMEOUT, connector=connector) as session:
            for _attempt in range(_REDIRECT_LIMIT + 1):
                async with session.get(
                    current,
                    allow_redirects=False,
                    headers={"Accept": "image/avif,image/webp,image/svg+xml,image/*,*/*;q=0.8"},
                ) as response:
                    if 300 <= response.status < 400:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("Logo URL redirected without a location")
                        current = _normalise_logo_url(urljoin(current, location))
                        continue
                    if response.status >= 400:
                        raise ValueError(f"Logo URL returned HTTP {response.status}")
                    content_type = _logo_content_type(response.headers.get("Content-Type", ""), str(response.url))
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = 0
                        if declared_length > _LOGO_MAX_BYTES:
                            raise ValueError("Logo image is too large")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.content.iter_chunked(32 * 1024):
                        total += len(chunk)
                        if total > _LOGO_MAX_BYTES:
                            raise ValueError("Logo image is too large")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    _prune_logo_cache(now)
                    if _LOGO_CACHE_TTL_SECONDS > 0 and _LOGO_CACHE_MAX_ITEMS > 0:
                        _LOGO_CACHE[cache_key] = (time.time() + _LOGO_CACHE_TTL_SECONDS, content_type, body)
                    return content_type, body
    finally:
        await resolver.close()
    raise ValueError("Logo URL redirected too many times")


def _is_public_import_ip(value) -> bool:
    return bool(value.is_global and not value.is_multicast and not value.is_unspecified)


def _reject_non_public_ip(value: str, *, message: str = "Private playlist URLs are not allowed") -> None:
    try:
        host_ip = ip_address(value)
    except ValueError:
        raise ValueError("Unable to verify playlist host")
    if not _is_public_import_ip(host_ip):
        raise ValueError(message)


def _default_port(parsed) -> int:
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _origin_tuple(value: str) -> tuple[str, str, int]:
    parsed = urlparse(value)
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(), _default_port(parsed))


def _same_origin_url(base_url: str, candidate_url: str) -> bool:
    try:
        return _origin_tuple(base_url) == _origin_tuple(candidate_url)
    except ValueError:
        return False


class _SafePublicResolver(AbstractResolver):
    def __init__(self, *, message: str = "Private playlist URLs are not allowed"):
        self._resolver = aiohttp.resolver.DefaultResolver()
        self._message = message

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records:
            raise ValueError("Unable to resolve playlist host")
        for record in records:
            _reject_non_public_ip(str(record.get("host") or ""), message=self._message)
        return records

    async def close(self):
        await self._resolver.close()


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
    for _rd in _REBINDING_DOMAINS:
        if hostname == _rd or hostname.endswith("." + _rd):
            raise ValueError("Playlist URL uses a DNS rebinding service — use a direct address")
    try:
        host_ip = ip_address(hostname)
    except ValueError:
        return url
    if not _is_public_import_ip(host_ip):
        raise ValueError("Private playlist URLs are not allowed")
    return url


def _looks_like_m3u(text: str) -> bool:
    # Valid Extended M3U must begin with #EXTM3U (after stripping BOM/whitespace).
    # The #EXTINF fallback is intentionally removed \u2014 it matched any response
    # body that happened to contain that string in the first 4 KB (e.g. HTML
    # playlist-index pages), causing confusing false-positive import attempts.
    preview = text.lstrip("\ufeff\r\n\t ")[:256]
    return preview.upper().startswith("#EXTM3U")


async def _fetch_m3u_url(url: str) -> tuple[str, str]:
    current = _normalise_import_url(url)
    resolver = _SafePublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=0)
    try:
        async with aiohttp.ClientSession(timeout=_IMPORT_TIMEOUT, connector=connector) as session:
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
    finally:
        await resolver.close()
    raise ValueError("Playlist URL redirected too many times")


def _stream_request_headers(raw_headers: dict | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_key, raw_value in (raw_headers or {}).items():
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if not key or not value:
            continue
        lower = key.lower()
        if lower in _FORBIDDEN_STREAM_HEADER_KEYS:
            continue
        if lower == "useragent":
            headers["User-Agent"] = value
        elif lower == "referrer":
            headers["Referer"] = value
        else:
            headers[key] = value
    return headers


def _proxied_hls_uri(channel_id: str, playlist_url: str, source_url: str, uri: str) -> str:
    if uri.startswith(("data:", "blob:")):
        return uri
    absolute = urljoin(playlist_url, uri)
    if not _same_origin_url(source_url, absolute):
        return uri
    return f"/api/live-tv/stream/{quote(channel_id, safe='')}?url={quote(absolute, safe='')}"


def _rewrite_m3u_proxy_urls(text: str, *, channel_id: str, playlist_url: str, source_url: str) -> str:
    rows: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            rows.append(line)
            continue
        if stripped.startswith("#"):
            rows.append(
                _URI_ATTR_RE.sub(
                    lambda match: f'URI="{_proxied_hls_uri(channel_id, playlist_url, source_url, match.group(1))}"',
                    line,
                )
            )
            continue
        rows.append(_proxied_hls_uri(channel_id, playlist_url, source_url, stripped))
    return "\n".join(rows) + ("\n" if text.endswith("\n") else "")


async def _read_limited_text(response, max_bytes: int) -> str:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Playlist manifest is too large")
        chunks.append(chunk)
    return b"".join(chunks).decode(response.charset or "utf-8", errors="replace")


async def _proxy_channel_stream(request: web.Request, channel: dict, target_url: str) -> web.StreamResponse:
    resolver = _SafePublicResolver(message="Private stream URLs are not allowed")
    connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=0)
    headers = _stream_request_headers(channel.get("streamHeaders") or {})
    try:
        async with aiohttp.ClientSession(timeout=_STREAM_TIMEOUT, connector=connector) as session:
            async with session.get(target_url, headers=headers) as response:
                if response.status >= 400:
                    return web.Response(text=f"Stream returned HTTP {response.status}", status=response.status)

                content_type = response.headers.get("Content-Type", "")
                if "mpegurl" in content_type.lower() or _HLS_RE.search(target_url):
                    text = await _read_limited_text(response, _HLS_MANIFEST_MAX_BYTES)
                    if _looks_like_m3u(text):
                        text = _rewrite_m3u_proxy_urls(
                            text,
                            channel_id=channel["id"],
                            playlist_url=str(response.url),
                            source_url=channel["streamUrl"],
                        )
                    return web.Response(
                        text=text,
                        content_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"},
                    )

                stream = web.StreamResponse(
                    status=response.status,
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Type": content_type or "application/octet-stream",
                    },
                )
                await stream.prepare(request)
                async for chunk in response.content.iter_chunked(64 * 1024):
                    await stream.write(chunk)
                await stream.write_eof()
                return stream
    finally:
        await resolver.close()


@routes.get("/api/live-tv/channels")
async def live_tv_channels(_: web.Request) -> web.Response:
    channels = await iptv_store.list_channels(include_disabled=False)
    return _json({"channels": [_with_proxied_logo(channel) for channel in channels]})


@routes.get("/api/live-tv/channel/{channel_id}")
async def live_tv_channel(request: web.Request) -> web.Response:
    channel = await iptv_store.get_channel(request.match_info["channel_id"], include_disabled=False)
    if not channel:
        raise web.HTTPNotFound(text="Channel not found")
    return _json({"channel": _with_proxied_logo(channel)})


@routes.get("/api/live-tv/logo/{channel_id}")
async def live_tv_logo(request: web.Request) -> web.Response:
    channel = await iptv_store.get_channel(request.match_info["channel_id"], include_disabled=False)
    if not channel:
        raise web.HTTPNotFound(text="Channel not found")
    logo_url = str(channel.get("logoUrl") or "").strip()
    if not logo_url:
        raise web.HTTPNotFound(text="Logo not found")
    try:
        content_type, body = await _fetch_logo(channel["id"], logo_url)
    except ValueError as exc:
        return web.Response(text=str(exc), status=400)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return web.Response(text=f"Unable to fetch logo: {type(exc).__name__}", status=502)
    cache_control = f"public, max-age={_LOGO_CACHE_TTL_SECONDS}" if _LOGO_CACHE_TTL_SECONDS > 0 else "no-store"
    return web.Response(
        body=body,
        content_type=content_type,
        headers={
            "Cache-Control": cache_control,
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@routes.get("/api/live-tv/stream/{channel_id}")
async def live_tv_stream(request: web.Request) -> web.StreamResponse:
    channel = await iptv_store.get_channel(request.match_info["channel_id"], include_disabled=False)
    if not channel:
        raise web.HTTPNotFound(text="Channel not found")
    source_url = channel.get("streamUrl", "")
    requested_url = str(request.query.get("url") or source_url)
    try:
        target_url = _normalise_import_url(requested_url)
    except ValueError as exc:
        return web.Response(text=str(exc), status=400)
    if requested_url != source_url and not _same_origin_url(source_url, target_url):
        return web.Response(text="Stream subresources must stay on the configured channel origin", status=400)
    try:
        return await _proxy_channel_stream(request, channel, target_url)
    except ValueError as exc:
        return web.Response(text=str(exc), status=400)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return web.Response(text=f"Unable to fetch stream: {type(exc).__name__}", status=502)


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

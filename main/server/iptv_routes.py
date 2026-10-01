"""IPTV channel catalogue API for the React Live TV experience."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
_STREAM_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=12, sock_connect=5, sock_read=8)
_HLS_MANIFEST_MAX_BYTES = int(os.environ.get("IPTV_HLS_MANIFEST_MAX_BYTES", str(2 * 1024 * 1024)))
_STREAM_PROBE_MAX_BYTES = int(os.environ.get("IPTV_STREAM_PROBE_MAX_BYTES", str(8 * 1024)))
_LOGO_MAX_BYTES = int(os.environ.get("IPTV_LOGO_MAX_BYTES", str(512 * 1024)))
_LOGO_CACHE_TTL_SECONDS = int(os.environ.get("IPTV_LOGO_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
_LOGO_ERROR_CACHE_TTL_SECONDS = int(os.environ.get("IPTV_LOGO_ERROR_CACHE_TTL_SECONDS", str(6 * 60 * 60)))
_LOGO_CACHE_MAX_ITEMS = int(os.environ.get("IPTV_LOGO_CACHE_MAX_ITEMS", "256"))
_LOGO_CACHE_MAX_BYTES = int(os.environ.get("IPTV_LOGO_CACHE_MAX_BYTES", str(32 * 1024 * 1024)))
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
_LOGO_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96" '
    b'role="img" aria-label="Channel"><rect width="96" height="96" rx="18" fill="#0b0f14"/>'
    b'<path d="M38 58a14 14 0 1 1 20 0M30 66a26 26 0 1 1 36 0M47 51h2v20h-2zM38 72h20" '
    b'fill="none" stroke="#94a3b8" stroke-width="6" stroke-linecap="round"/>'
    b'<circle cx="48" cy="48" r="5" fill="#14b8a6"/></svg>'
)
_LOGO_CACHE: dict[str, tuple[float, str, bytes]] = {}
_LOGO_CACHE_BYTES = 0


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
    global _LOGO_CACHE_BYTES
    for key, (expires_at, _, _) in list(_LOGO_CACHE.items()):
        if expires_at <= now:
            entry = _LOGO_CACHE.pop(key, None)
            if entry:
                _LOGO_CACHE_BYTES = max(0, _LOGO_CACHE_BYTES - len(entry[2]))
    while _LOGO_CACHE_MAX_ITEMS > 0 and len(_LOGO_CACHE) > _LOGO_CACHE_MAX_ITEMS:
        oldest_key = min(_LOGO_CACHE.items(), key=lambda item: item[1][0])[0]
        entry = _LOGO_CACHE.pop(oldest_key, None)
        if entry:
            _LOGO_CACHE_BYTES = max(0, _LOGO_CACHE_BYTES - len(entry[2]))
    while _LOGO_CACHE_MAX_BYTES > 0 and _LOGO_CACHE_BYTES > _LOGO_CACHE_MAX_BYTES and _LOGO_CACHE:
        oldest_key = min(_LOGO_CACHE.items(), key=lambda item: item[1][0])[0]
        entry = _LOGO_CACHE.pop(oldest_key, None)
        if entry:
            _LOGO_CACHE_BYTES = max(0, _LOGO_CACHE_BYTES - len(entry[2]))


def _cache_logo_result(channel_id: str, logo_url: str, content_type: str, body: bytes, ttl_seconds: int) -> None:
    if ttl_seconds <= 0 or _LOGO_CACHE_MAX_ITEMS <= 0:
        return
    if _LOGO_CACHE_MAX_BYTES > 0 and len(body) > _LOGO_CACHE_MAX_BYTES:
        return
    global _LOGO_CACHE_BYTES
    now = time.time()
    _prune_logo_cache(now)
    cache_key = _logo_cache_key(channel_id, logo_url)
    existing = _LOGO_CACHE.pop(cache_key, None)
    if existing:
        _LOGO_CACHE_BYTES = max(0, _LOGO_CACHE_BYTES - len(existing[2]))
    _LOGO_CACHE[cache_key] = (now + ttl_seconds, content_type, body)
    _LOGO_CACHE_BYTES += len(body)
    _prune_logo_cache(now)


def _placeholder_logo_result(channel_id: str, logo_url: str) -> tuple[str, bytes]:
    content_type = "image/svg+xml"
    _cache_logo_result(channel_id, logo_url, content_type, _LOGO_PLACEHOLDER_SVG, _LOGO_ERROR_CACHE_TTL_SECONDS)
    return content_type, _LOGO_PLACEHOLDER_SVG


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
                    _cache_logo_result(channel_id, logo_url, content_type, body, _LOGO_CACHE_TTL_SECONDS)
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


# Hosts seen in each channel's own manifests — the ?url= subresource proxy
# only fetches from these (an open relay to arbitrary hosts would be an
# SSRF/abuse hazard; the manifest chain defines the channel's legit CDNs).
_CHANNEL_SUBRESOURCE_HOSTS: dict[str, set[str]] = {}
_CHANNEL_HOSTS_MAX = 64


def _remember_channel_host(channel_id: str, absolute_url: str) -> None:
    try:
        host = (urlparse(absolute_url).hostname or "").lower()
    except ValueError:
        return
    if not host:
        return
    hosts = _CHANNEL_SUBRESOURCE_HOSTS.setdefault(channel_id, set())
    if host not in hosts and len(hosts) < _CHANNEL_HOSTS_MAX:
        hosts.add(host)


def _channel_host_allowed(channel_id: str, absolute_url: str) -> bool:
    hosts = _CHANNEL_SUBRESOURCE_HOSTS.get(channel_id)
    if hosts is None:
        return False  # never rewrote this channel: no subresource proxying
    try:
        host = (urlparse(absolute_url).hostname or "").lower()
    except ValueError:
        return False
    return host in hosts


def _proxied_hls_uri(channel_id: str, playlist_url: str, source_url: str, uri: str) -> str:
    if uri.startswith(("data:", "blob:")):
        return uri
    absolute = urljoin(playlist_url, uri)
    _remember_channel_host(channel_id, absolute)
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


async def _read_probe_bytes(response, max_bytes: int = _STREAM_PROBE_MAX_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    limit = max(1, max_bytes)
    async for chunk in response.content.iter_chunked(min(16 * 1024, limit)):
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            chunks.append(chunk[:max(0, len(chunk) - (total - limit))])
            break
        chunks.append(chunk)
        if total >= limit:
            break
    return b"".join(chunks)


async def _probe_stream_url(stream_url: str, raw_headers: dict | None = None) -> str:
    current = _normalise_import_url(stream_url)
    resolver = _SafePublicResolver(message="Private stream URLs are not allowed")
    connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=0)
    headers = _stream_request_headers(raw_headers)
    headers.setdefault("Accept", "application/vnd.apple.mpegurl,video/*,*/*;q=0.8")
    try:
        async with aiohttp.ClientSession(timeout=_STREAM_PROBE_TIMEOUT, connector=connector) as session:
            for _attempt in range(_REDIRECT_LIMIT + 1):
                async with session.get(
                    current,
                    allow_redirects=False,
                    headers={**headers, "Range": "bytes=0-8191"},
                ) as response:
                    if 300 <= response.status < 400:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("Stream URL redirected without a location")
                        current = _normalise_import_url(urljoin(current, location))
                        continue
                    if response.status >= 400:
                        raise ValueError(f"Stream returned HTTP {response.status}")
                    body = await _read_probe_bytes(response)
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "mpegurl" in content_type or _HLS_RE.search(current):
                        text = body.decode(response.charset or "utf-8", errors="replace")
                        if not _looks_like_m3u(text):
                            raise ValueError("Stream URL did not return an HLS playlist")
                        return "HLS playlist reachable"
                    if not body and response.status != 204:
                        raise ValueError("Stream returned no data")
                    return "Stream reachable"
    finally:
        await resolver.close()
    raise ValueError("Stream URL redirected too many times")


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


_HEALTH_OK_TTL_SECONDS = int(os.environ.get("IPTV_HEALTH_OK_TTL_SECONDS", str(5 * 60)))
_HEALTH_FAIL_TTL_SECONDS = int(os.environ.get("IPTV_HEALTH_FAIL_TTL_SECONDS", str(2 * 60)))
_HEALTH_MAX_IDS = int(os.environ.get("IPTV_HEALTH_MAX_IDS", "100"))
# Probe concurrency shares the event loop with video relaying and ffmpeg
# transcoding on a small Koyeb instance — keep it modest so a batch never
# starves stream sockets/TLS handshakes.
_HEALTH_PROBE_CONCURRENCY = int(os.environ.get("IPTV_HEALTH_PROBE_CONCURRENCY", "6"))
_HEALTH_CACHE: dict[str, tuple[float, bool]] = {}
_HEALTH_CACHE_LOCK = asyncio.Lock()

def _first_segment_url(manifest_text: str, manifest_url: str) -> str:
    """Return the first playable media segment (or sub-playlist) URL."""
    for line in manifest_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _URI_ATTR_RE.search(line):  # EXT-X-MEDIA / EXT-X-STREAM-INF URI="..."
            match = _URI_ATTR_RE.search(line)
            if match:
                return urljoin(manifest_url, match.group(1))
        return urljoin(manifest_url, line)
    return ""


async def _probe_stream_segments(stream_url: str, raw_headers: dict | None) -> bool:
    """Strict probe for the health sweep: manifest AND first segment.

    A manifest alone can resolve fine while every segment inside is dead
    (geo-fenced CDNs, expired tokens) — which is why sweeps that only check
    manifests delete nothing. Fetch the manifest, then Range-GET its first
    segment URL.
    """
    try:
        async with aiohttp.ClientSession(
            timeout=_STREAM_PROBE_TIMEOUT,
            connector=aiohttp.TCPConnector(resolver=_SafePublicResolver(), ttl_dns_cache=0),
        ) as session:
            current = _normalise_import_url(stream_url)
            manifest_text = ""
            manifest_content_type = ""
            for _ in range(_REDIRECT_LIMIT + 1):
                async with session.get(current, allow_redirects=False, headers=_stream_request_headers(raw_headers)) as response:
                    if 300 <= response.status < 400:
                        location = response.headers.get("Location")
                        if not location:
                            return False
                        current = _normalise_import_url(urljoin(current, location))
                        continue
                    if response.status >= 400:
                        return False
                    manifest_content_type = response.headers.get("Content-Type", "").lower()
                    manifest_text = (await _read_probe_bytes(response, _HLS_MANIFEST_MAX_BYTES)).decode(
                        response.charset or "utf-8", errors="replace"
                    )
                    break
            if not _looks_like_m3u(manifest_text):
                # Direct non-HLS streams (raw .ts, .mpd, PHP playlist gates —
                # ~3% of real catalogues) have no manifest to parse. The URL
                # answered with content, which is all manifest-only probing
                # would have established. BUT: CDNs also serve soft-error
                # HTML pages with HTTP 200 — those are NOT streams.
                head = manifest_text.lstrip()[:512].lower()
                if "text/html" in manifest_content_type or head.startswith(("<!doctype html", "<html")):
                    return False
                return True
            segment_url = _first_segment_url(manifest_text, current)
            if not segment_url:
                return False
            # Sub-playlists (master → variant) need one more hop; media
            # segments answer a Range GET. Either proving reachable is enough.
            async with session.get(
                segment_url,
                allow_redirects=False,
                headers={**_stream_request_headers(raw_headers), "Range": "bytes=0-1023"},
            ) as segment_response:
                if segment_response.status < 400:
                    return True
                if 300 <= segment_response.status < 400 and segment_response.headers.get("Location"):
                    return True  # redirect counts as reachable; the proxy follows it too
                return False
    except (ValueError, aiohttp.ClientError, TimeoutError, OSError):
        # OSError: raw socket failures from uvloop (network unreachable,
        # fd exhaustion, connection refused before aiohttp wraps them).
        return False


async def _health_probe_channel(channel: dict) -> bool:
    # Single deep probe: manifest AND first segment. The old two-step
    # (manifest probe, then deep probe) fetched every manifest twice per
    # attempt; _probe_stream_segments subsumes it, with a non-HLS fallback.
    return await _probe_stream_segments(channel["streamUrl"], channel.get("streamHeaders") or {})

def _health_status_name(healthy: bool) -> str:
    return "ok" if healthy else "down"

def _health_cached(channel_id: str) -> bool | None:
    entry = _HEALTH_CACHE.get(channel_id)
    if not entry:
        return None
    checked_at, healthy = entry
    ttl = _HEALTH_OK_TTL_SECONDS if healthy else _HEALTH_FAIL_TTL_SECONDS
    if time.time() - checked_at > ttl:
        return None
    return healthy

@routes.get("/api/live-tv/health")
async def live_tv_health(request: web.Request) -> web.Response:
    """Batch playability status for the Live TV rail.

    Probe results are cached server-side (shared across visitors) with short
    TTLs so recurring polls mostly hit the cache instead of upstream origins.
    """
    raw_ids = str(request.query.get("ids") or "")
    ids = [part for part in (piece.strip() for piece in raw_ids.split(",")) if part][: _HEALTH_MAX_IDS]
    if not ids:
        return _json({"statuses": {}})

    all_channels = {channel["id"]: channel for channel in await iptv_store.list_channels(include_disabled=False)}
    statuses: dict[str, str] = {}
    stale: list[tuple[str, dict]] = []
    for channel_id in ids:
        channel = all_channels.get(channel_id)
        if channel is None:
            statuses[channel_id] = "unknown"
            continue
        cached = _health_cached(channel_id)
        if cached is None:
            stale.append((channel_id, channel))
        else:
            statuses[channel_id] = _health_status_name(cached)

    if stale:
        async with _HEALTH_CACHE_LOCK:
            # A concurrent request may have probed the same channels while we
            # awaited the lock — re-check so fresh results are not overwritten.
            still_stale = [(cid, channel) for cid, channel in stale if _health_cached(cid) is None]
            semaphore = asyncio.Semaphore(_HEALTH_PROBE_CONCURRENCY)

            async def _guarded(channel_id: str, channel: dict) -> None:
                # Probe errors must degrade to "down", never fail the whole
                # status endpoint: a raw socket error (uvloop OSError,
                # ENETUNREACH, fd exhaustion) escaping here 500s the batch.
                try:
                    async with semaphore:
                        healthy = await _health_probe_channel(channel)
                except Exception:
                    logging.exception("iptv health probe crashed for channel %s", channel_id)
                    healthy = False
                _HEALTH_CACHE[channel_id] = (time.time(), healthy)

            await asyncio.gather(*(_guarded(cid, channel) for cid, channel in still_stale), return_exceptions=True)
        for channel_id, _channel in stale:
            statuses[channel_id] = _health_status_name(_health_cached(channel_id) or False)

    return _json({"statuses": statuses})


@routes.get("/api/live-tv/channels")
async def live_tv_channels(_: web.Request) -> web.Response:
    channels = await iptv_store.list_channels(include_disabled=False)
    return _json({"channels": [_with_proxied_logo(channel) for channel in channels]})


_SWEEP_MAX_ATTEMPTS = int(os.environ.get("IPTV_SWEEP_MAX_ATTEMPTS", "3"))
_HEALTH_SWEEP: dict | None = None
_HEALTH_SWEEP_TASK: asyncio.Task | None = None


def _sweep_snapshot() -> dict:
    if not _HEALTH_SWEEP:
        return {"running": False}
    return {**_HEALTH_SWEEP, "running": _HEALTH_SWEEP.get("running", False)}


async def _run_health_sweep(mode: str, attempts: int) -> None:
    """Probe channels and disable/delete the ones that keep failing.

    disable mode: enabled channels only (hide dead ones from Live TV).
    delete mode:  the whole catalogue including already-disabled channels —
                  a purge of everything dead, past and present.
    """
    global _HEALTH_SWEEP
    if _HEALTH_SWEEP is None:
        _HEALTH_SWEEP = {"total": 0, "processed": 0, "affected": []}
    sweep = _HEALTH_SWEEP
    include_disabled = mode == "delete"
    channels = await iptv_store.list_channels(include_disabled=include_disabled)
    sweep["total"] = len(channels)
    if not channels:
        sweep["running"] = False
        sweep["finishedAt"] = time.time()
        return

    semaphore = asyncio.Semaphore(_HEALTH_PROBE_CONCURRENCY)

    async def _probe_with_retries(channel: dict) -> bool:
        # A channel is only "down" when every attempt fails. A crash
        # (raw socket OSError escaping the probe, etc.) counts the
        # channel as dead — one broken channel must not kill the sweep.
        try:
            for _ in range(attempts):
                async with semaphore:
                    if await _health_probe_channel(channel):
                        return True
            return False
        except Exception:
            logging.exception("iptv health sweep: probe crashed for channel id=%s", channel["id"])
            return False
        finally:
            sweep["processed"] += 1

    results = await asyncio.gather(*(_probe_with_retries(channel) for channel in channels))
    dead = [channel for channel, healthy in zip(channels, results) if not healthy]

    affected: list[dict] = []
    for channel in dead:
        if mode == "delete":
            ok = await iptv_store.delete_channel(channel["id"])
            action = "deleted"
        else:
            saved, _updated, _message = await iptv_store.save_channel({**channel, "enabled": False})
            ok = bool(saved)
            action = "disabled"
        if ok:
            _HEALTH_CACHE.pop(channel["id"], None)
            affected.append({"id": channel["id"], "name": channel["name"], "action": action})
            logging.info("iptv health sweep: %s channel id=%s name=%r", action, channel["id"], channel["name"])
        else:
            logging.warning("iptv health sweep: failed to %s channel id=%s name=%r", action, channel["id"], channel["name"])

    sweep["running"] = False
    sweep["finishedAt"] = time.time()
    sweep["affected"] = affected
    logging.info(
        "iptv health sweep: finished mode=%s probed=%d dead=%d %s=%d attempts=%d",
        mode, len(channels), len(dead), "deleted" if mode == "delete" else "disabled", len(affected), attempts,
    )


@routes.post("/api/app/admin/iptv/health-sweep")
async def admin_iptv_health_sweep_start(request: web.Request) -> web.Response:
    global _HEALTH_SWEEP, _HEALTH_SWEEP_TASK
    _require_admin(request)
    if _HEALTH_SWEEP and _HEALTH_SWEEP.get("running"):
        return _json({"ok": False, "error": "A health sweep is already running"}, status=409)

    data = await _body(request)
    mode = str(data.get("mode") or "disable")
    if mode not in ("disable", "delete"):
        return _json({"ok": False, "error": "mode must be 'disable' or 'delete'"}, status=400)
    try:
        attempts = int(data.get("attempts") or 3)
    except (TypeError, ValueError):
        attempts = 3
    attempts = max(1, min(attempts, _SWEEP_MAX_ATTEMPTS))

    # No awaits between the running-check and publishing the new sweep state:
    # two concurrent POSTs must not both start a sweep.
    if _HEALTH_SWEEP and _HEALTH_SWEEP.get("running"):
        return _json({"ok": False, "error": "A health sweep is already running"}, status=409)
    _HEALTH_SWEEP = {
        "running": True,
        "mode": mode,
        "attempts": attempts,
        "total": 0,
        "processed": 0,
        "affected": [],
        "startedAt": time.time(),
        "finishedAt": None,
    }
    _HEALTH_SWEEP_TASK = asyncio.create_task(_run_health_sweep(mode, attempts))

    def _on_sweep_done(task: asyncio.Task) -> None:
        global _HEALTH_SWEEP
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and _HEALTH_SWEEP:
            _HEALTH_SWEEP["running"] = False
            _HEALTH_SWEEP["finishedAt"] = time.time()
            _HEALTH_SWEEP["error"] = f"{type(exc).__name__}: {exc}"

    _HEALTH_SWEEP_TASK.add_done_callback(_on_sweep_done)
    return _json({"ok": True, "sweep": _sweep_snapshot()})


@routes.get("/api/app/admin/iptv/health-sweep")
async def admin_iptv_health_sweep_status(request: web.Request) -> web.Response:
    _require_admin(request)
    return _json({"ok": True, "sweep": _sweep_snapshot()})


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
        ttl_seconds = _LOGO_CACHE_TTL_SECONDS
    except (ValueError, aiohttp.ClientError, TimeoutError):
        content_type, body = _placeholder_logo_result(channel["id"], logo_url)
        ttl_seconds = _LOGO_ERROR_CACHE_TTL_SECONDS
    cache_control = f"public, max-age={ttl_seconds}" if ttl_seconds > 0 else "no-store"
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
    if requested_url != source_url and not _same_origin_url(source_url, target_url) and not _channel_host_allowed(channel["id"], target_url):
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
    # No channels list: a 10k-channel import would be a multi-MB response.
    # Clients refresh via GET /api/app/admin/iptv after importing.
    return _json({"ok": True, **result, "channels": []})


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
    return _json({"ok": True, **result, "sourceUrl": source_url, "channels": []})


@routes.post("/api/app/admin/iptv/test")
async def admin_iptv_test(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await _body(request)
    stream_url = str(data.get("streamUrl") or data.get("stream_url") or "").strip()
    stream_headers = data.get("streamHeaders") or data.get("stream_headers") or {}
    try:
        message = await _probe_stream_url(stream_url, stream_headers if isinstance(stream_headers, dict) else {})
    except ValueError as exc:
        return _json({"ok": False, "message": str(exc)}, status=400)
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
        return _json({"ok": False, "message": f"Unable to reach stream: {type(exc).__name__}"}, status=400)
    return _json({"ok": True, "message": message})

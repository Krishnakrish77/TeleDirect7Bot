"""Same-origin, bounded Open Library cover proxy."""

from __future__ import annotations

import re
import time

import aiohttp
from aiohttp import web


_COVER_ID_RE = re.compile(r"^[1-9]\d{0,9}$")
_TIMEOUT = aiohttp.ClientTimeout(total=12, sock_connect=5, sock_read=8)
_TTL_SECONDS = 24 * 60 * 60
_MAX_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 128
_cache: dict[int, tuple[float, str, bytes]] = {}


def cover_proxy_url(cover_id: int | str) -> str:
    """Return a safe local URL for a numeric Open Library cover id."""
    clean = str(cover_id or "").strip()
    return f"/api/openlibrary-cover/{clean}" if _COVER_ID_RE.fullmatch(clean) else ""


def _normalise_cover_id(value: str) -> int:
    clean = str(value or "").strip()
    if not _COVER_ID_RE.fullmatch(clean):
        raise ValueError("Invalid Open Library cover id")
    return int(clean)


def _content_type(value: str) -> str:
    clean = value.split(";", 1)[0].strip().lower()
    if not clean.startswith("image/"):
        raise ValueError("Open Library cover did not return an image")
    return clean


async def openlibrary_cover_proxy(request: web.Request) -> web.Response:
    try:
        cover_id = _normalise_cover_id(request.match_info["cover_id"])
    except ValueError:
        raise web.HTTPNotFound()
    now = time.monotonic()
    cached = _cache.get(cover_id)
    if cached and cached[0] > now:
        content_type, body = cached[1], cached[2]
    else:
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg", headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}) as response:
                    if response.status >= 400:
                        raise ValueError("Open Library cover unavailable")
                    content_type = _content_type(response.headers.get("Content-Type", ""))
                    declared = int(response.headers.get("Content-Length", "0") or 0)
                    if declared > _MAX_BYTES:
                        raise ValueError("Open Library cover is too large")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            raise ValueError("Open Library cover is too large")
                        chunks.append(chunk)
                    body = b"".join(chunks)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise web.HTTPNotFound()
        if len(_cache) >= _MAX_ITEMS:
            oldest = min(_cache, key=lambda key: _cache[key][0])
            _cache.pop(oldest, None)
        _cache[cover_id] = (now + _TTL_SECONDS, content_type, body)
    return web.Response(body=body, content_type=content_type, headers={"Cache-Control": f"public, max-age={_TTL_SECONDS}", "X-Content-Type-Options": "nosniff"})

"""Same-origin, bounded Open Library cover proxy."""

from __future__ import annotations

import re
import time

import aiohttp
from aiohttp import web


_COVER_ID_RE = re.compile(r"^[1-9]\d{0,9}$")
_TIMEOUT = aiohttp.ClientTimeout(total=12, sock_connect=5, sock_read=8)
_TTL_SECONDS = 24 * 60 * 60
_FALLBACK_TTL_SECONDS = 10 * 60
_MAX_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 128
_cache: dict[int, tuple[float, str, bytes]] = {}
_FALLBACK_COVER = b'''<svg xmlns="http://www.w3.org/2000/svg" width="480" height="720" viewBox="0 0 480 720" role="img" aria-label="Book cover unavailable"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#303949"/><stop offset="1" stop-color="#151a24"/></linearGradient></defs><rect width="480" height="720" rx="24" fill="url(#g)"/><path fill="#ff8a32" d="M136 210c42-18 83-8 104 15v240c-21-23-62-33-104-15V210Zm208 0c-42-18-83-8-104 15v240c21-23 62-33 104-15V210Z"/><path fill="#fff" fill-opacity=".72" d="M160 264h56v12h-56zm104 0h56v12h-56zm-104 36h56v12h-56zm104 0h56v12h-56z"/><text x="240" y="570" text-anchor="middle" fill="#d9e0ec" font-family="Arial, sans-serif" font-size="24" letter-spacing="3">TELEDIRECT</text><text x="240" y="608" text-anchor="middle" fill="#9ba9bc" font-family="Arial, sans-serif" font-size="18">BOOK</text></svg>'''


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
        expires_at, content_type, body = cached
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
            expires_at = now + _TTL_SECONDS
        except (aiohttp.ClientError, TimeoutError, ValueError):
            # An Open Library cover ID is metadata, not a guarantee that the
            # corresponding image is still available. A local image fallback
            # keeps broken upstream artwork out of the browser console and
            # prevents a failed cover from making an otherwise usable book
            # card look broken.
            expires_at = now + _FALLBACK_TTL_SECONDS
            content_type, body = "image/svg+xml", _FALLBACK_COVER
        if len(_cache) >= _MAX_ITEMS:
            oldest = min(_cache, key=lambda key: _cache[key][0])
            _cache.pop(oldest, None)
        _cache[cover_id] = (expires_at, content_type, body)
    return web.Response(
        body=body,
        content_type=content_type,
        headers={
            "Cache-Control": f"public, max-age={max(0, int(expires_at - now))}",
            "X-Content-Type-Options": "nosniff",
        },
    )

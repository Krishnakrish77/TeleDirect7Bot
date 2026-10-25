"""Same-origin, bounded Open Library cover proxy."""
from __future__ import annotations

import re

import aiohttp
from aiohttp import web

_COVER_ID_RE = re.compile(r"^\d{1,12}$")
_TIMEOUT = aiohttp.ClientTimeout(total=10, sock_connect=3, sock_read=6)
_MAX_BYTES = 2 * 1024 * 1024
_FALLBACK = b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="720"><rect width="100%" height="100%" fill="#151a24"/><text x="50%" y="50%" text-anchor="middle" fill="#ff8a32" font-family="Arial" font-size="32">BOOK</text></svg>'


async def openlibrary_cover_proxy(request: web.Request) -> web.Response:
    cover_id = request.match_info["cover_id"]
    if not _COVER_ID_RE.fullmatch(cover_id):
        raise web.HTTPNotFound()
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false"
            ) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if response.status >= 400 or not content_type.startswith("image/"):
                    raise ValueError("Open Library cover unavailable")
                body = await response.content.read(_MAX_BYTES + 1)
                if len(body) > _MAX_BYTES:
                    raise ValueError("Open Library cover too large")
    except (aiohttp.ClientError, TimeoutError, ValueError):
        content_type, body = "image/svg+xml", _FALLBACK
    return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})

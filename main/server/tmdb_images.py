"""Same-origin TMDB artwork proxy helpers."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web

_TMDB_IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=12, sock_connect=5, sock_read=8)
_TMDB_IMAGE_CACHE_TTL = int(os.environ.get("TMDB_IMAGE_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
_TMDB_IMAGE_ERROR_CACHE_TTL = int(os.environ.get("TMDB_IMAGE_ERROR_CACHE_TTL_SECONDS", str(6 * 60 * 60)))
_TMDB_IMAGE_CACHE_MAX_ITEMS = int(os.environ.get("TMDB_IMAGE_CACHE_MAX_ITEMS", "256"))
_TMDB_IMAGE_MAX_BYTES = int(os.environ.get("TMDB_IMAGE_MAX_BYTES", str(2 * 1024 * 1024)))
_tmdb_image_cache: dict[tuple[str, str], tuple[float, str, bytes]] = {}
_TMDB_IMAGE_SIZES = frozenset({"w92", "w154", "w185", "w300", "w342", "w500", "w780", "w1280", "original"})
_TMDB_IMAGE_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+\.(?:avif|gif|jpe?g|png|webp)$", re.IGNORECASE)
_TMDB_IMAGE_EXTENSION_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_TMDB_IMAGE_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513" viewBox="0 0 342 513" '
    b'role="img" aria-label="Artwork"><rect width="342" height="513" rx="26" fill="#0b0f14"/>'
    b'<path d="M126 184h90a20 20 0 0 1 20 20v105a20 20 0 0 1-20 20h-90a20 20 0 0 1-20-20V204a20 20 0 0 1 20-20Z" '
    b'fill="none" stroke="#475569" stroke-width="14"/>'
    b'<path d="m124 302 42-47 28 32 18-20 25 35" fill="none" stroke="#64748b" stroke-width="12" '
    b'stroke-linecap="round" stroke-linejoin="round"/><circle cx="202" cy="232" r="13" fill="#14b8a6"/></svg>'
)


def _normalise_tmdb_image(size: str, tail: str) -> tuple[str, str]:
    clean_size = str(size or "").strip()
    clean_tail = str(tail or "").strip().lstrip("/")
    if clean_size not in _TMDB_IMAGE_SIZES:
        raise ValueError("Unsupported TMDB image size")
    if not clean_tail or len(clean_tail) > 300:
        raise ValueError("Invalid TMDB image path")
    if ".." in clean_tail or "\\" in clean_tail or not _TMDB_IMAGE_PATH_RE.fullmatch(clean_tail):
        raise ValueError("Invalid TMDB image path")
    return clean_size, clean_tail


def _tmdb_image_cache_key(size: str, tail: str) -> tuple[str, str]:
    return (size, tail)


def _prune_tmdb_image_cache(now: float) -> None:
    for key, (expires_at, _, _) in list(_tmdb_image_cache.items()):
        if expires_at <= now:
            _tmdb_image_cache.pop(key, None)
    while _TMDB_IMAGE_CACHE_MAX_ITEMS > 0 and len(_tmdb_image_cache) >= _TMDB_IMAGE_CACHE_MAX_ITEMS:
        oldest_key = min(_tmdb_image_cache.items(), key=lambda item: item[1][0])[0]
        _tmdb_image_cache.pop(oldest_key, None)


def _cache_tmdb_image(size: str, tail: str, content_type: str, body: bytes, ttl_seconds: int) -> None:
    if ttl_seconds <= 0 or _TMDB_IMAGE_CACHE_MAX_ITEMS <= 0:
        return
    now = time.monotonic()
    _prune_tmdb_image_cache(now)
    _tmdb_image_cache[_tmdb_image_cache_key(size, tail)] = (
        now + ttl_seconds,
        content_type,
        body,
    )


def _tmdb_image_content_type(raw_content_type: str, tail: str) -> str:
    content_type = raw_content_type.split(";", 1)[0].strip().lower()
    extension = Path(tail).suffix.lower()
    guessed = _TMDB_IMAGE_EXTENSION_CONTENT_TYPES.get(extension, "")
    if content_type.startswith("image/"):
        return content_type
    if guessed and content_type in {"", "application/octet-stream", "binary/octet-stream", "text/plain"}:
        return guessed
    raise ValueError("TMDB URL did not return image content")


def _tmdb_placeholder_result(size: str, tail: str) -> tuple[str, bytes]:
    content_type = "image/svg+xml"
    _cache_tmdb_image(size, tail, content_type, _TMDB_IMAGE_PLACEHOLDER_SVG, _TMDB_IMAGE_ERROR_CACHE_TTL)
    return content_type, _TMDB_IMAGE_PLACEHOLDER_SVG


async def _fetch_tmdb_image(size: str, tail: str) -> tuple[str, bytes]:
    cache_key = _tmdb_image_cache_key(size, tail)
    now = time.monotonic()
    cached = _tmdb_image_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    url = f"https://image.tmdb.org/t/p/{size}/{tail}"
    async with aiohttp.ClientSession(timeout=_TMDB_IMAGE_TIMEOUT) as session:
        async with session.get(
            url,
            headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
        ) as response:
            if response.status >= 400:
                raise ValueError(f"TMDB image returned HTTP {response.status}")
            content_type = _tmdb_image_content_type(response.headers.get("Content-Type", ""), tail)
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = 0
                if declared_length > _TMDB_IMAGE_MAX_BYTES:
                    raise ValueError("TMDB image is too large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > _TMDB_IMAGE_MAX_BYTES:
                    raise ValueError("TMDB image is too large")
                chunks.append(chunk)
            body = b"".join(chunks)
            _cache_tmdb_image(size, tail, content_type, body, _TMDB_IMAGE_CACHE_TTL)
            return content_type, body


def tmdb_image_url(path: str, size: str = "w342") -> str:
    if not path:
        return ""
    try:
        clean_size, clean_tail = _normalise_tmdb_image(size, path)
    except ValueError:
        return ""
    return f"/api/tmdb-image/{quote(clean_size, safe='')}/{quote(clean_tail, safe='/._-')}"


async def tmdb_image_proxy(request: web.Request) -> web.Response:
    try:
        size, tail = _normalise_tmdb_image(request.match_info["size"], request.match_info["tail"])
        content_type, body = await _fetch_tmdb_image(size, tail)
        cache_ttl = _TMDB_IMAGE_CACHE_TTL
    except ValueError:
        size = request.match_info.get("size", "w342")
        tail = request.match_info.get("tail", "placeholder.jpg")
        try:
            size, tail = _normalise_tmdb_image(size, tail)
        except ValueError:
            size, tail = "w342", "placeholder.jpg"
        content_type, body = _tmdb_placeholder_result(size, tail)
        cache_ttl = _TMDB_IMAGE_ERROR_CACHE_TTL
    except (aiohttp.ClientError, TimeoutError):
        size, tail = _normalise_tmdb_image(request.match_info["size"], request.match_info["tail"])
        content_type, body = _tmdb_placeholder_result(size, tail)
        cache_ttl = _TMDB_IMAGE_ERROR_CACHE_TTL
    return web.Response(
        body=body,
        content_type=content_type,
        headers={
            "Cache-Control": f"public, max-age={cache_ttl}" if cache_ttl > 0 else "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )

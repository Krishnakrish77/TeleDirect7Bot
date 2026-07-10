from __future__ import annotations

import re
from urllib.parse import urljoin

from main.vars import Var


_WHITESPACE_RE = re.compile(r"\s+")


def absolute_url(path_or_url: str | None) -> str:
    value = (path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return urljoin(Var.URL, value.lstrip("/"))


def tmdb_image_url(path: str | None, size: str = "w780") -> str:
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    normalized = path if path.startswith("/") else f"/{path}"
    return f"https://image.tmdb.org/t/p/{size}{normalized}"


def item_image_url(item, *, size: str = "w780") -> str:
    if item is None:
        return ""
    poster = getattr(item, "poster_path", "") or ""
    if poster:
        return tmdb_image_url(poster, size)
    still = getattr(item, "episode_still_path", "") or ""
    if still:
        return tmdb_image_url(still, size)
    backdrop = getattr(item, "backdrop_path", "") or ""
    if backdrop:
        return tmdb_image_url(backdrop, size)
    secure_hash = getattr(item, "secure_hash", "") or ""
    message_id = getattr(item, "message_id", "") or ""
    if secure_hash and message_id:
        suffix = "?v=audio3" if getattr(item, "media_kind", "") == "audio" else ""
        return absolute_url(f"thumb/{secure_hash}{message_id}.jpg{suffix}")
    return ""


def fallback_thumb_url(secure_hash: str, message_id: int | str, *, is_audio: bool = False) -> str:
    suffix = "?v=audio3" if is_audio else ""
    return absolute_url(f"thumb/{secure_hash}{message_id}.jpg{suffix}")


def compact_description(*values: str | None, fallback: str = "Watch on TeleDirect", limit: int = 220) -> str:
    text = next((value for value in values if value and value.strip()), "") or fallback
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

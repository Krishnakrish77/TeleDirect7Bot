"""Server-side Wyzie subtitle search, download and quota helpers.

The provider key never crosses an API response.  Search candidates are cached
briefly on the server and clients refer to an opaque candidate id when asking
to attach a subtitle.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from main.vars import Var

_BASE_URL = "https://sub.wyzie.io"
_SEARCH_TTL = 60 * 60 * 6
_MAX_RESULTS = 40
_MAX_SUBTITLE_BYTES = 10 * 1024 * 1024
_USER_SEARCH_LIMIT = 50
_USER_ATTACH_LIMIT = 10
_USER_ITEM_ATTACH_LIMIT = 3
_GLOBAL_REQUEST_LIMIT = 800
_cache: dict[tuple[int, str], tuple[float, list[dict[str, Any]]]] = {}
_lock = asyncio.Lock()


class WyzieError(Exception):
    pass


def configured() -> bool:
    return bool(Var.WYZIE_API_KEY)


def _db():
    try:
        from main.utils import media_index
        store = media_index._store
        if store is not None and hasattr(store, "_client"):
            return store._client[store._db_name]
    except Exception:
        pass
    return None


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _reserve(user_id: int, action: str, item_id: int | None = None,
                   *, provider_call: bool) -> None:
    """Persist conservative daily counters in Mongo before doing work."""
    db = _db()
    if db is None:
        raise WyzieError("Subtitle requests are temporarily unavailable")
    day = _day()
    limit = _USER_SEARCH_LIMIT if action == "search" else _USER_ATTACH_LIMIT
    item_limit = _USER_ITEM_ATTACH_LIMIT if action == "attach" and item_id else None
    async with _lock:
        usage = db["subtitle_usage"]
        user_key = f"{day}:user:{user_id}:{action}"
        user = await usage.find_one({"_id": user_key}, projection={"count": 1})
        if int((user or {}).get("count", 0)) >= limit:
            raise WyzieError(f"Daily {action} limit reached. Try again tomorrow.")
        if item_limit:
            item_key = f"{day}:item:{user_id}:{item_id}:attach"
            item = await usage.find_one({"_id": item_key}, projection={"count": 1})
            if int((item or {}).get("count", 0)) >= item_limit:
                raise WyzieError("You have reached the subtitle limit for this title today.")
        if provider_call:
            global_key = f"{day}:provider"
            global_doc = await usage.find_one({"_id": global_key}, projection={"count": 1})
            if int((global_doc or {}).get("count", 0)) >= _GLOBAL_REQUEST_LIMIT:
                raise WyzieError("Subtitle service has reached today's request budget.")
            await usage.update_one({"_id": global_key}, {"$inc": {"count": 1}, "$setOnInsert": {"day": day}}, upsert=True)
        await usage.update_one({"_id": user_key}, {"$inc": {"count": 1}, "$setOnInsert": {"day": day, "user_id": user_id, "action": action}}, upsert=True)
        if item_limit:
            await usage.update_one({"_id": item_key}, {"$inc": {"count": 1}, "$setOnInsert": {"day": day, "user_id": user_id, "item_id": item_id}}, upsert=True)


def _candidate(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    ident = str(raw.get("id") or "")
    fmt = str(raw.get("format") or "").lower()
    if not ident or not isinstance(url, str) or not url.startswith(f"{_BASE_URL}/") or fmt not in {"srt", "vtt"}:
        return None
    return {"id": ident, "url": url, "format": fmt, "language": str(raw.get("language") or ""),
            "label": str(raw.get("display") or raw.get("language") or "Subtitles"),
            "release": str(raw.get("release") or ""), "fileName": str(raw.get("fileName") or f"subtitle.{fmt}"),
            "hearingImpaired": bool(raw.get("isHearingImpaired")), "source": str(raw.get("source") or "")}


async def search(user_id: int, item, language: str = "") -> list[dict[str, Any]]:
    if not configured():
        raise WyzieError("Subtitle search is not configured")
    provider_id = item.imdb_id or (str(item.tmdb_id) if item.tmdb_id else "")
    if not provider_id:
        raise WyzieError("This title needs IMDb or TMDB metadata before subtitles can be searched")
    language = language.strip().lower()
    if language and (len(language) > 16 or not all(ch.isalpha() or ch in {",", "-"} for ch in language)):
        raise WyzieError("Invalid subtitle language filter")
    cache_key = (item.message_id, language)
    now = time.monotonic()
    cached = _cache.get(cache_key)
    await _reserve(user_id, "search", provider_call=not (cached and now - cached[0] < _SEARCH_TTL))
    if cached and now - cached[0] < _SEARCH_TTL:
        return [{k: v for k, v in result.items() if k != "url"} for result in cached[1]]
    params = {"id": provider_id, "format": "srt,vtt", "key": Var.WYZIE_API_KEY}
    if item.season is not None and item.episode is not None:
        params.update({"season": str(item.season), "episode": str(item.episode)})
    if language:
        params["language"] = language
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            async with session.get(f"{_BASE_URL}/search", params=params) as response:
                if response.status == 429:
                    raise WyzieError("Subtitle provider rate limit reached. Try again later.")
                if response.status >= 400:
                    raise WyzieError("Subtitle provider is unavailable")
                payload = await response.json(content_type=None)
    except WyzieError:
        raise
    except Exception as exc:
        logging.warning("wyzie: search failed for item %s: %s", item.message_id, exc)
        raise WyzieError("Subtitle provider is unavailable") from exc
    results = payload if isinstance(payload, list) else payload.get("subtitles", []) if isinstance(payload, dict) else []
    clean = [value for value in (_candidate(raw) for raw in results) if value][: _MAX_RESULTS]
    _cache[cache_key] = (now, clean)
    return [{k: v for k, v in result.items() if k != "url"} for result in clean]


async def download(user_id: int, item, candidate_id: str) -> tuple[bytes, dict[str, Any]]:
    if not candidate_id or len(candidate_id) > 64:
        raise WyzieError("Invalid subtitle selection")
    found = None
    now = time.monotonic()
    for (message_id, _language), (created, candidates) in list(_cache.items()):
        if message_id == item.message_id and now - created < _SEARCH_TTL:
            found = next((candidate for candidate in candidates if candidate["id"] == candidate_id), None)
            if found:
                break
    if found is None:
        raise WyzieError("Search results expired. Search again before attaching a subtitle.")
    await _reserve(user_id, "attach", item.message_id, provider_call=True)
    try:
        async with ClientSession(timeout=ClientTimeout(total=20)) as session:
            async with session.get(found["url"], allow_redirects=False) as response:
                if response.status != 200:
                    raise WyzieError("Selected subtitle is no longer available")
                length = response.content_length
                if length is not None and length > _MAX_SUBTITLE_BYTES:
                    raise WyzieError("Selected subtitle is too large")
                data = await response.content.read(_MAX_SUBTITLE_BYTES + 1)
    except WyzieError:
        raise
    except Exception as exc:
        logging.warning("wyzie: download failed for item %s: %s", item.message_id, exc)
        raise WyzieError("Could not download selected subtitle") from exc
    if not data or len(data) > _MAX_SUBTITLE_BYTES:
        raise WyzieError("Selected subtitle is invalid or too large")
    return data, found

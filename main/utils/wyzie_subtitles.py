"""Server-side Wyzie subtitle search, download and quota helpers.

The provider key never crosses an API response.  Search candidates are cached
briefly on the server and clients refer to an opaque candidate id when asking
to attach a subtitle.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout

from main.vars import Var

_BASE_URL = "https://sub.wyzie.io"
_SEARCH_TTL = 60 * 60 * 6
_MAX_CACHE_ENTRIES = 256  # in-memory search-result cache cap
_MAX_RESULTS = 40
_MAX_SUBTITLE_BYTES = 10 * 1024 * 1024
# Daily caps now come from Var (env-tunable, generous defaults). Kept as
# module names for the tests that patch behavior around them.
_USER_SEARCH_LIMIT = Var.WYZIE_USER_SEARCH_LIMIT
_USER_ATTACH_LIMIT = Var.WYZIE_USER_ATTACH_LIMIT
_USER_ITEM_ATTACH_LIMIT = Var.WYZIE_ITEM_ATTACH_LIMIT
_GLOBAL_REQUEST_LIMIT = Var.WYZIE_GLOBAL_REQUEST_LIMIT
# Start with Wyzie's default source (OpenSubtitles). It is consistently
# available to valid keys; the wider source router is a fallback for titles it
# misses, because some keys cannot query every source in ``all`` reliably.
_FALLBACK_SEARCH_SOURCES = "all"
_cache: dict[int, dict[str, tuple[float, list[dict[str, Any]]]]] = {}
_lock = asyncio.Lock()
_TRUSTED_DOWNLOAD_HOSTS = {"sub.wyzie.io"}
_RELEASE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_RELEASE_NOISE = {
    "aac", "ac3", "atmos", "av1", "bluray", "brrip", "ddp", "dv", "dts", "h264", "h265",
    "hevc", "hdr", "proper", "remux", "repack", "subs", "web", "webrip", "webdl", "x264", "x265",
}


class WyzieError(Exception):
    pass


def _prune_cache(now: float) -> None:
    """Drop expired entries and cap the in-memory search-result cache."""
    expired: list[int] = []
    for message_id, langs in list(_cache.items()):
        for lang, (created, _candidates) in list(langs.items()):
            if now - created >= _SEARCH_TTL:
                langs.pop(lang, None)
        if not langs:
            expired.append(message_id)
    for message_id in expired:
        _cache.pop(message_id, None)
    while len(_cache) > _MAX_CACHE_ENTRIES:
        oldest = min(_cache, key=lambda mid: min(c[0] for c in _cache[mid].values()))
        _cache.pop(oldest, None)


def _trusted_download_url(value: object) -> bool:
    """Accept the provider proxy and direct HTTPS OpenSubtitles downloads.

    Wyzie used to return only ``sub.wyzie.io`` proxy URLs. It now also
    returns direct ``d1.opensubtitles.org`` links for some sources, so keeping
    the old proxy-only guard silently discarded valid search results.
    """
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and bool(parsed.path) and (
        host in _TRUSTED_DOWNLOAD_HOSTS or host.endswith(".opensubtitles.org")
    )


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


class QuotaUnavailable(WyzieError):
    """A daily cap was hit; the route maps this to a 429 with Retry-After."""

    def __init__(self, message: str):
        super().__init__(message)
        self.retry_after = 3600  # daily caps reset at UTC midnight


def _check_quota(user_id: int, action: str, item_id: int | None = None) -> None:
    """Refuse the request up front when a daily cap is already spent.

    Read-only: nothing is counted here. Counters are only committed after the
    work succeeds, so a provider hiccup or flaky download never eats quota.
    """
    db = _db()
    if db is None:
        raise WyzieError("Subtitle requests are temporarily unavailable")
    day = _day()
    limit = _USER_SEARCH_LIMIT if action == "search" else _USER_ATTACH_LIMIT
    item_limit = _USER_ITEM_ATTACH_LIMIT if action == "attach" and item_id else None
    usage = db["subtitle_usage"]

    async def _run() -> None:
        async with _lock:
            user = await usage.find_one({"_id": f"{day}:user:{user_id}:{action}"}, projection={"count": 1})
            if int((user or {}).get("count", 0)) >= limit:
                raise QuotaUnavailable(f"Daily {action} limit reached. Try again tomorrow.")
            if item_limit:
                item = await usage.find_one({"_id": f"{day}:item:{user_id}:{item_id}:attach"}, projection={"count": 1})
                if int((item or {}).get("count", 0)) >= item_limit:
                    raise QuotaUnavailable("You have reached the subtitle limit for this title today.")
            if item_id or action == "search":
                # Provider requests (every search, every attach download) draw
                # from the shared key budget; check it before doing the work.
                global_doc = await usage.find_one({"_id": f"{day}:provider"}, projection={"count": 1})
                if int((global_doc or {}).get("count", 0)) >= _GLOBAL_REQUEST_LIMIT:
                    raise QuotaUnavailable("Subtitle service has reached today's request budget.")

    return _run()


def _commit_quota(user_id: int, action: str, item_id: int | None = None):
    """Count one successful request against the daily caps."""
    db = _db()
    if db is None:
        async def _noop() -> None:
            return None
        return _noop()
    day = _day()
    item_limit = _USER_ITEM_ATTACH_LIMIT if action == "attach" and item_id else None
    usage = db["subtitle_usage"]

    async def _run() -> None:
        async with _lock:
            if item_id or action == "search":
                await usage.update_one(
                    {"_id": f"{day}:provider"}, {"$inc": {"count": 1}, "$setOnInsert": {"day": day}}, upsert=True,
                )
            await usage.update_one(
                {"_id": f"{day}:user:{user_id}:{action}"},
                {"$inc": {"count": 1}, "$setOnInsert": {"day": day, "user_id": user_id, "action": action}},
                upsert=True,
            )
            if item_limit:
                await usage.update_one(
                    {"_id": f"{day}:item:{user_id}:{item_id}:attach"},
                    {"$inc": {"count": 1}, "$setOnInsert": {"day": day, "user_id": user_id, "item_id": item_id}},
                    upsert=True,
                )

    return _run()


def _candidate(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    ident = str(raw.get("id") or "")
    fmt = str(raw.get("format") or "").lower()
    if not ident or not _trusted_download_url(url) or fmt not in {"srt", "vtt"}:
        return None
    return {"id": ident, "url": url, "format": fmt, "language": str(raw.get("language") or ""),
            "label": str(raw.get("display") or raw.get("language") or "Subtitles"),
            "release": str(raw.get("release") or ""), "fileName": str(raw.get("fileName") or f"subtitle.{fmt}"),
            "hearingImpaired": bool(raw.get("isHearingImpaired")), "source": str(raw.get("source") or "")}


def _release_tokens(value: object) -> set[str]:
    """Keep useful release/name tokens without turning them into a filter."""
    tokens = _RELEASE_TOKEN_RE.findall(str(value or "").lower())
    return {token for token in tokens if len(token) > 1 and token not in _RELEASE_NOISE}


def _rank_release_matches(item, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer subtitle releases resembling the video, retaining every result.

    Wyzie resolves title identity with IMDB/TMDB IDs.  The local filename and
    parsed title are only a tie-breaker: release strings are often incomplete
    or unconventional, so using them as an upstream search filter would turn
    good subtitle matches into empty results.
    """
    wanted = _release_tokens(getattr(item, "file_name", ""))
    wanted.update(_release_tokens(getattr(item, "series_title", "")))
    wanted.update(_release_tokens(getattr(item, "title", "")))
    if not wanted:
        return candidates

    def score(candidate: dict[str, Any]) -> int:
        available = _release_tokens(candidate.get("release"))
        available.update(_release_tokens(candidate.get("fileName")))
        overlap = wanted & available
        # Episode and resolution/release-group tokens tend to be the most
        # discriminating. A plain title overlap is still useful but lighter.
        return sum(3 if token.startswith("s") and "e" in token else 2 if token.isdigit() else 1 for token in overlap)

    return [candidate for _position, candidate in sorted(
        enumerate(candidates), key=lambda row: (-score(row[1]), row[0]),
    )]


async def search(user_id: int, item, language: str = "") -> list[dict[str, Any]]:
    if not configured():
        raise WyzieError("Subtitle search is not configured")
    provider_id = item.imdb_id or (str(item.tmdb_id) if item.tmdb_id else "")
    if not provider_id:
        raise WyzieError("This title needs IMDb or TMDB metadata before subtitles can be searched")
    language = language.strip().lower()
    if language and (len(language) > 16 or not all(ch.isalpha() or ch in {",", "-"} for ch in language)):
        raise WyzieError("Invalid subtitle language filter")
    now = time.monotonic()
    cached = (_cache.get(item.message_id) or {}).get(language)
    if cached and now - cached[0] < _SEARCH_TTL:
        # Cache hits cost nothing: no quota check, no provider call.
        return [{k: v for k, v in result.items() if k != "url"} for result in cached[1]]
    await _check_quota(user_id, "search")
    params = {
        "id": provider_id,
        "format": "srt,vtt",
        "key": Var.WYZIE_API_KEY,
    }
    if item.season is not None and item.episode is not None:
        params.update({"season": str(item.season), "episode": str(item.episode)})
    if language:
        params["language"] = language

    async def _search(params: dict[str, str]) -> list[Any]:
        try:
            async with ClientSession(timeout=ClientTimeout(total=15)) as session:
                async with session.get(f"{_BASE_URL}/search", params=params) as response:
                    if response.status == 429:
                        raise WyzieError("Subtitle provider rate limit reached. Try again later.")
                    if response.status in (401, 403):
                        raise WyzieError("Subtitle service key is invalid or not authorized.")
                    if response.status == 402:
                        raise WyzieError("Subtitle service request budget is exhausted. Try again later.")
                    if response.status >= 400:
                        raise WyzieError("Subtitle provider is unavailable")
                    payload = await response.json(content_type=None)
        except WyzieError:
            raise
        except Exception as exc:
            logging.warning("wyzie: search failed for item %s: %s", item.message_id, exc)
            raise WyzieError("Subtitle provider is unavailable") from exc
        return payload if isinstance(payload, list) else payload.get("subtitles", []) if isinstance(payload, dict) else []

    try:
        results = await _search(params)
        # Search every enabled source only after the dependable default source
        # has no result. This expands discovery without letting a restricted
        # source set make a common title look unavailable.
        if not results:
            fallback_params = {**params, "source": _FALLBACK_SEARCH_SOURCES}
            results = await _search(fallback_params)
    except WyzieError:
        raise
    clean = [value for value in (_candidate(raw) for raw in results) if value][: _MAX_RESULTS]
    clean = _rank_release_matches(item, clean)
    # Never cache an empty provider response. An intermittent provider/source
    # failure must not make a title appear to have no subtitles for six hours.
    if clean:
        _cache.setdefault(item.message_id, {})[language] = (now, clean)
        _prune_cache(now)
        await _commit_quota(user_id, "search")
    return [{k: v for k, v in result.items() if k != "url"} for result in clean]


class _TransientDownloadError(WyzieError):
    """Timeout/network/truncated-body class — worth one retry."""


class _LinkGoneError(WyzieError):
    """The cached download URL died (OpenSubtitles links expire in hours,
    not days). Involves dropping the stale search cache."""


def _proxy_url(candidate: dict[str, Any]) -> str:
    """Wyzie's own proxy download path — routes around OpenSubtitles
    hotlink/datacenter blocks that 403 direct links."""
    source = str(candidate.get("source") or "opensubtitles").strip() or "opensubtitles"
    ident = str(candidate.get("id") or "").strip()
    fmt = str(candidate.get("format") or "srt").strip() or "srt"
    return f"{_BASE_URL}/c/{source}/id/{ident}?format={fmt}"


def _drop_cached(item) -> None:
    """Forget cached search results for an item so the next search refetches."""
    _cache.pop(item.message_id, None)


def _status_class(status: int) -> str:
    """Classify a provider download response.

    Returns one of:
    - "ok"        — body is usable.
    - "transient" — throttling/server fault; worth one retry.
    - "gone"      — dead/expired download link; drop the stale cache and
                    re-search for fresh URLs.
    - "error"     — anything else; a user-facing failure.
    """
    if status == 200:
        return "ok"
    if status == 429 or status == 503 or status >= 500:
        return "transient"
    if status in (403, 404, 410):
        # OpenSubtitles mirrors answer expired signed URLs with 403 as
        # often as 404/410, hours before the cached result would refresh.
        return "gone"
    return "error"


async def _download_bytes(url: str) -> bytes:
    """One trusted-redirect-following subtitle download attempt."""
    try:
        async with ClientSession(timeout=ClientTimeout(total=20)) as session:
            # Direct OpenSubtitles links commonly redirect to a regional
            # download host. Follow that redirect only while every hop stays
            # on a trusted subtitle host.
            async with session.get(url, allow_redirects=True) as response:
                redirect_urls = [str(entry.url) for entry in response.history]
                if not _trusted_download_url(str(response.url)) or not all(_trusted_download_url(url) for url in redirect_urls):
                    raise WyzieError("Selected subtitle download is not trusted")
                if _status_class(response.status) == "transient":
                    raise _TransientDownloadError(f"provider returned {response.status}")
                if _status_class(response.status) == "gone":
                    # OpenSubtitles download links expire hours after search,
                    # far sooner than our 6h result cache. Treat as a stale
                    # cache entry, not a user-facing failure.
                    raise _LinkGoneError("download link expired")
                if response.status != 200:
                    raise WyzieError("Selected subtitle is no longer available")
                length = response.content_length
                if length is not None and length > _MAX_SUBTITLE_BYTES:
                    raise WyzieError("Selected subtitle is too large")
                data = await response.content.read(_MAX_SUBTITLE_BYTES + 1)
    except WyzieError:
        raise
    except Exception as exc:
        logging.warning("wyzie: download attempt failed: %s", exc)
        raise _TransientDownloadError(str(exc)) from exc
    if not data or len(data) > _MAX_SUBTITLE_BYTES:
        raise WyzieError("Selected subtitle is invalid or too large")
    return data


def _find_candidate(item, candidate_id: str) -> dict[str, Any] | None:
    """Locate a cached search result, ignoring expired entries."""
    now = time.monotonic()
    for _language, (created, candidates) in (_cache.get(item.message_id) or {}).items():
        if now - created < _SEARCH_TTL:
            for candidate in candidates:
                if candidate["id"] == candidate_id:
                    return candidate
    return None


async def _resolve_candidate(user_id: int, item, candidate_id: str) -> dict[str, Any]:
    """Find the cached candidate, transparently re-searching when stale.

    OpenSubtitles download links die hours before our search cache does, so
    the recovery path drops the cache and refetches fresh URLs once.
    """
    found = _find_candidate(item, candidate_id)
    if found is not None:
        return found
    await search(user_id, item, getattr(item, "language", "") or "")
    found = _find_candidate(item, candidate_id)
    if found is None:
        raise WyzieError("That subtitle is no longer offered. Pick another or search again.")
    return found


async def download(user_id: int, item, candidate_id: str) -> tuple[bytes, dict[str, Any]]:
    if not candidate_id or len(candidate_id) > 64:
        raise WyzieError("Invalid subtitle selection")
    found = await _resolve_candidate(user_id, item, candidate_id)
    # Quota is a pre-check only; it is committed after a successful download
    # so flaky provider hops never consume a user's daily attach budget.
    await _check_quota(user_id, "attach", item.message_id)

    async def _with_retry(url: str) -> bytes:
        try:
            return await _download_bytes(url)
        except _TransientDownloadError as exc:
            # Download hosts (OpenSubtitles mirrors in particular) drop or
            # time out sporadically. One bounded retry with a short backoff
            # converts most of those into success without hammering the
            # provider.
            logging.info("wyzie: transient download failure for item %s, retrying once: %s", item.message_id, exc)
            await asyncio.sleep(1.5)
            try:
                return await _download_bytes(url)
            except _TransientDownloadError:
                raise WyzieError("Subtitle download is having trouble — try again in a moment.") from exc

    async def _attempt(candidate: dict[str, Any]) -> bytes:
        # Direct download first. A 403/404/410 covers both a dead link AND
        # OpenSubtitles blocking datacenter IPs (Koyeb egress) — the next
        # step handles both.
        try:
            return await _with_retry(candidate["url"])
        except _LinkGoneError:
            pass
        # Wyzie's own proxy serves the same subtitle by id and is not subject
        # to OpenSubtitles hotlink blocks. Try it before giving up.
        proxy = _proxy_url(candidate)
        logging.info("wyzie: direct download failed for item %s, falling back to proxy", item.message_id)
        try:
            return await _with_retry(proxy)
        except WyzieError as exc:
            raise WyzieError("That subtitle is no longer offered. Pick another or search again.") from exc

    data = await _attempt(found)
    await _commit_quota(user_id, "attach", item.message_id)
    return data, found

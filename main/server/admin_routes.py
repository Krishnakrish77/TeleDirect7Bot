"""Admin UI for catalogue cleanup.

The owner DMs ``/admin`` to the bot and receives a one-time URL. Visiting
that URL exchanges the token for a session cookie, then renders a paged
list of indexed BIN_CHANNEL entries with checkboxes. Bulk actions:

  • Delete: removes the BIN message AND the in-memory hub entry.
  • Re-tag: replaces the tag set on every selected entry.
  • Set quality: stamps a quality bucket on every selected entry.

Both re-tag and set-quality re-render the BIN caption via the same
IndexEntry pipeline used at index time so the on-channel representation
stays in sync.
"""

from __future__ import annotations

import asyncio
import base64
import html as _html_lib
import io
import json
import logging
import re
import secrets
import socket
import time
from ipaddress import ip_address
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import media_index, thumb_cache, trending as _trending
from main.utils.human_readable import humanbytes
from main.utils.hub_query import ExternalSubtitle, HubItem
from main.utils.file_properties import get_hash
from main.utils.index_entry import IndexEntry, render
from main.utils import series as series_parse
from main.utils.media_index import compute_movie_key
from main.utils.user_auth import decode_token
from main.utils.subtitles import derive_label, language_from_filename
from main.vars import Var


routes = web.RouteTableDef()
_MAX_SIDECAR_SUBTITLE_BYTES = 10 * 1024 * 1024
_THUMB_MAX_BYTES = 5 * 1024 * 1024
_THUMB_REDIRECT_LIMIT = 3

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)
_env.filters["humansize"] = lambda b: humanbytes(b) if b else ""


def _validate_public_thumbnail_url(value: str) -> str:
    """Validate a thumbnail URL before connecting to it.

    The resolver below also validates DNS results, which prevents a hostname
    from bypassing this literal-IP check or rebinding to an internal address.
    """
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only valid http(s) URLs are allowed")
    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("private/loopback addresses are not allowed")
    try:
        address = ip_address(hostname)
    except ValueError:
        return url
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
        raise ValueError("private/loopback addresses are not allowed")
    return url


def _reject_private_thumbnail_ip(host: str) -> None:
    try:
        address = ip_address(host)
    except ValueError as exc:
        raise ValueError("Unable to resolve thumbnail host") from exc
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
        raise ValueError("private/loopback addresses are not allowed")


class _SafeThumbnailResolver(AbstractResolver):
    """Resolve only public addresses, including after every redirect."""
    def __init__(self):
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records:
            raise ValueError("Unable to resolve thumbnail host")
        for record in records:
            _reject_private_thumbnail_ip(str(record.get("host") or ""))
        return records

    async def close(self):
        await self._resolver.close()


async def _fetch_public_thumbnail(url: str) -> bytes:
    """Download one small image without following unsafe redirects."""
    current = _validate_public_thumbnail_url(url)
    connector = aiohttp.TCPConnector(
        resolver=_SafeThumbnailResolver(), ttl_dns_cache=0,
    )
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for _attempt in range(_THUMB_REDIRECT_LIMIT + 1):
            async with session.get(
                current,
                allow_redirects=False,
                headers={"Accept": "image/avif,image/webp,image/svg+xml,image/*,*/*;q=0.8"},
            ) as response:
                if 300 <= response.status < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("thumbnail URL redirected without a location")
                    current = _validate_public_thumbnail_url(urljoin(current, location))
                    continue
                if response.status >= 400:
                    raise ValueError(f"thumbnail fetch failed ({response.status})")
                if not (response.content_type or "").lower().startswith("image/"):
                    raise ValueError("thumbnail URL did not return an image")
                declared = response.headers.get("Content-Length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError:
                        declared_size = 0
                    if declared_size > _THUMB_MAX_BYTES:
                        raise ValueError("thumbnail too large (> 5 MB)")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > _THUMB_MAX_BYTES:
                        raise ValueError("thumbnail too large (> 5 MB)")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("thumbnail URL redirected too many times")


async def _apply_custom_thumbnail(message_id: int, thumb_url: str) -> str:
    """Clear or persist a safe custom thumbnail and return its operator message."""
    if thumb_url == "__clear__":
        await thumb_cache.clear(message_id)
        return "thumbnail cleared"
    try:
        image = await _fetch_public_thumbnail(thumb_url)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        return f"thumbnail rejected: {exc}"
    except Exception:
        logging.exception("admin: thumbnail download failed for %s", thumb_url)
        return "thumbnail error"

    keys = [message_id]
    item = media_index.get_item(message_id)
    if getattr(item, "media_kind", "") == "audio":
        keys.append(thumb_cache.cache_id(message_id, audio=True))
    for key in keys:
        thumb_cache.set_(key, image)
    store = thumb_cache._store()
    if store is not None:
        for key in keys:
            try:
                await store.set_thumb(key, image)
            except Exception:
                logging.exception("admin: durable thumbnail save failed for %d", key)
    return "thumbnail updated"


def _get_admin_user(request: web.Request) -> Optional[dict]:
    """Extract and verify JWT from Authorization header OR td_session cookie."""
    # Header first (for htmx/fetch calls from the admin SPA)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = decode_token(auth[7:])
        if user and user.get("is_admin"):
            return user
    # Cookie fallback (for initial server-rendered page loads)
    cookie = request.cookies.get("td_session", "")
    if cookie:
        user = decode_token(cookie)
        if user and user.get("is_admin"):
            return user
    return None


def _require_session(request: web.Request) -> int:
    """Backward-compat alias — returns the Telegram user_id or redirects."""
    user = _get_admin_user(request)
    if user is None:
        raise web.HTTPFound("/admin")
    return user["sub"]


def _html(body: str, *, status: int = 200) -> web.Response:
    return web.Response(
        text=body,
        status=status,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


_ADMIN_PAGE_SIZE = 100
_ADMIN_FILTERS = [
    ("all", "All"),
    ("unenriched", "Unenriched"),
    ("enriched", "Enriched"),
    ("series", "Series"),
    ("movies", "Movies"),
    ("music", "Music"),
    ("no-poster", "No poster"),
    ("no-thumb", "No thumb"),
    ("no-overview", "No overview"),
    ("no-subtitles", "No subtitles (verified)"),
    ("no-year", "No year"),
    ("no-cast", "No cast/crew"),
    ("no-markers", "No playback markers"),
    ("duplicates", "Duplicates"),
    ("hidden", "Hidden"),
]
_ADMIN_SORT_COLUMNS = {"date", "title", "size", "quality"}
_ADMIN_QUALITY_ORDER = {"4K": 4, "2160p": 4, "1080p": 3, "720p": 2, "480p": 1, "": 0}


def _admin_duplicate_candidates(items: list) -> tuple[dict[int, dict], int, int]:
    """Return admin duplicate diagnostics keyed by BIN message id.

    Only exact secure_hash+size matches are duplicate candidates. Same
    title/TMDB/movie rows can be valid release or quality variants and
    should stay out of the destructive duplicate cleanup flow.
    """
    buckets: list[tuple[str, str, list]] = []

    def add_buckets(reason: str, grouped: dict) -> None:
        for key, members in grouped.items():
            if key and len(members) > 1:
                buckets.append((reason, str(key), members))

    exact: dict = {}
    for item in items:
        if item.secure_hash and item.file_size:
            exact.setdefault((item.secure_hash, item.file_size), []).append(item)

    add_buckets("Exact file", exact)

    details: dict[int, dict] = {}
    extra_ids: set[int] = set()
    duplicate_groups = 0
    for reason, key, members in buckets:
        sorted_members = sorted(members, key=lambda item: item.message_id)
        group_id = f"{reason}:{key}"
        bucket_extra_ids = {item.message_id for item in sorted_members[1:]}
        if bucket_extra_ids - extra_ids:
            duplicate_groups += 1
        extra_ids.update(bucket_extra_ids)
        for item in sorted_members:
            details.setdefault(item.message_id, {
                "reason": reason,
                "group": group_id,
                "size": len(sorted_members),
                "extra": item.message_id in bucket_extra_ids,
            })

    duplicate_extras = len(extra_ids)
    return details, duplicate_groups, duplicate_extras


def _admin_catalogue_context(request: web.Request) -> dict:
    try:
        page = max(1, int(request.query.get("page", "1") or "1"))
    except ValueError:
        page = 1
    filter_name = (request.query.get("filter") or "all").strip()
    if filter_name not in {key for key, _ in _ADMIN_FILTERS}:
        filter_name = "all"
    q = (request.query.get("q") or "").strip().lower()
    sort_col = (request.query.get("sort") or "date").strip()
    sort_dir = (request.query.get("dir") or "desc").strip()
    if sort_col not in _ADMIN_SORT_COLUMNS:
        sort_col = "date"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    def _sort_key(it):
        if sort_col == "title":
            return (it.title or "").lower()
        if sort_col == "size":
            return it.file_size or 0
        if sort_col == "quality":
            return _ADMIN_QUALITY_ORDER.get(it.quality or "", 0)
        return it.message_id

    items_all = sorted(
        media_index._items.values(),
        key=_sort_key,
        reverse=(sort_dir == "desc"),
    )
    catalogue_size = sum(1 for it in items_all if not it.hidden)

    duplicate_details, duplicate_groups, duplicate_extras = _admin_duplicate_candidates(
        [it for it in items_all if not it.hidden],
    )
    duplicate_extra_ids = {
        message_id for message_id, info in duplicate_details.items()
        if info.get("extra")
    }
    duplicate_message_ids = set(duplicate_details)

    def _is_video_item(it) -> bool:
        return getattr(it, "media_kind", "") != "audio"

    def _has_time_range(start, end) -> bool:
        try:
            return float(end or 0) > float(start or 0)
        except (TypeError, ValueError):
            return False

    def _passes_filter(it) -> bool:
        if filter_name == "unenriched" and it.tmdb_id:
            return False
        if filter_name == "enriched" and not it.tmdb_id:
            return False
        if filter_name == "series" and not it.series_key:
            return False
        if filter_name == "movies" and it.series_key:
            return False
        if filter_name == "no-poster" and (it.poster_path or not it.tmdb_id):
            return False
        if filter_name == "duplicates" and it.message_id not in duplicate_extra_ids:
            return False
        if filter_name == "no-thumb" and (it.has_thumb or it.duration):
            return False
        if filter_name == "no-overview" and (
            not _is_video_item(it) or it.overview or it.description
        ):
            return False
        # Only flag items whose embedded streams have been probed. This avoids
        # falsely marking legacy/unprobed MKVs that may contain subtitles.
        if filter_name == "no-subtitles" and (
            not _is_video_item(it)
            or not getattr(it, "subtitles_probed_at", 0)
            or bool(it.subtitles)
            or int(getattr(it, "embedded_subtitle_count", 0) or 0) > 0
        ):
            return False
        if filter_name == "no-year" and (not _is_video_item(it) or it.year):
            return False
        if filter_name == "no-cast" and not media_index.needs_credits_backfill(it):
            return False
        if filter_name == "no-markers" and (
            not _is_video_item(it)
            or (it.duration or 0) < 20 * 60
            or it.chapters
            or _has_time_range(it.intro_start, it.intro_end)
            or _has_time_range(it.recap_start, it.recap_end)
        ):
            return False
        if filter_name == "music" and getattr(it, "media_kind", "") != "audio":
            return False
        if filter_name == "hidden" and not it.hidden:
            return False
        if filter_name != "hidden" and it.hidden:
            return False
        return True

    filtered = [it for it in items_all if _passes_filter(it)]

    if q:
        def _matches(it) -> bool:
            blob = " ".join((
                it.title or "",
                it.series_title or "",
                it.file_name or "",
                " ".join(it.tags or []),
                it.imdb_id or "",
                getattr(it, "artist", "") or "",
                getattr(it, "album_title", "") or "",
                f"bin:{it.message_id}",
            )).lower()
            return q in blob
        filtered = [it for it in filtered if _matches(it)]

    if filter_name == "duplicates":
        filtered.sort(
            key=lambda it: (_sort_key(it), it.secure_hash or "", it.message_id),
            reverse=(sort_dir == "desc"),
        )

    filtered_count = len(filtered)
    total_pages = max(1, (filtered_count + _ADMIN_PAGE_SIZE - 1) // _ADMIN_PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * _ADMIN_PAGE_SIZE
    items_page = filtered[start : start + _ADMIN_PAGE_SIZE]

    raw = request.cookies.get(_FLASH_COOKIE) or ""
    flash = ""
    if raw:
        from urllib.parse import unquote as _u
        try:
            flash = _u(raw)
        except Exception:
            flash = ""

    _series_counts: dict = {}
    for _it in media_index._items.values():
        if _it.series_title:
            _series_counts[_it.series_title] = _series_counts.get(_it.series_title, 0) + 1
    known_series = [s for s, _ in sorted(
        _series_counts.items(), key=lambda kv: (-kv[1], kv[0]),
    )]

    return {
        "items": items_page,
        "catalogue_size": catalogue_size,
        "filtered_count": filtered_count,
        "page": page,
        "total_pages": total_pages,
        "page_size": _ADMIN_PAGE_SIZE,
        "filter_name": filter_name,
        "search_q": request.query.get("q") or "",
        "sort_col": sort_col,
        "sort_dir": sort_dir,
        "stats": {
            **media_index.stats(),
            "duplicate_groups": duplicate_groups,
            "duplicate_extras": duplicate_extras,
        },
        "duplicate_message_ids": duplicate_message_ids,
        "duplicate_details": duplicate_details,
        "known_series": known_series,
        "flash": flash,
        "raw_flash": raw,
    }


def _admin_thumb_url(item) -> str:
    if not item.secure_hash:
        return ""
    suffix = "?v=audio3" if getattr(item, "media_kind", "") == "audio" else ""
    return f"/thumb/{item.secure_hash}{item.message_id}.jpg{suffix}"


def _admin_item_payload(item, duplicate_details) -> dict:
    watch_key = f"{item.secure_hash}{item.message_id}" if item.secure_hash else str(item.message_id)
    duplicate_info = (
        {"reason": "Duplicate", "size": 0}
        if isinstance(duplicate_details, set) and item.message_id in duplicate_details
        else (duplicate_details or {}).get(item.message_id)
    )
    return {
        "messageId": item.message_id,
        "secureHash": item.secure_hash or "",
        "watchKey": watch_key,
        "title": item.title or "",
        "year": item.year,
        "quality": item.quality or "",
        "tags": list(item.tags or []),
        "fileName": item.file_name or "",
        "fileSize": item.file_size or 0,
        "fileSizeLabel": humanbytes(item.file_size) if item.file_size else "",
        "duration": item.duration or 0,
        "description": item.description or "",
        "hidden": bool(item.hidden),
        "duplicate": bool(duplicate_info),
        "duplicateReason": (duplicate_info or {}).get("reason", ""),
        "duplicateGroupSize": (duplicate_info or {}).get("size", 0),
        "duplicateExtra": bool((duplicate_info or {}).get("extra")),
        "hasThumb": bool(item.has_thumb),
        "missingThumb": not item.has_thumb and not item.duration,
        "missingPoster": bool(item.tmdb_id and not item.poster_path),
        "subtitleCount": len(item.subtitles or []) + int(item.embedded_subtitle_count or 0),
        "sidecars": [
            {"binMessageId": sub.bin_message_id, "language": sub.language or "und", "label": sub.label or "Subtitles"}
            for sub in (item.subtitles or [])
        ],
        "subtitleProbePending": not bool(item.subtitles_probed_at),
        "mediaKind": getattr(item, "media_kind", "") or "",
        "seriesTitle": item.series_title or "",
        "seriesKey": item.series_key or "",
        "season": item.season,
        "episode": item.episode,
        "episodeEnd": item.episode_end,
        "recapStart": item.recap_start,
        "recapEnd": item.recap_end,
        "tmdbId": item.tmdb_id,
        "tmdbKind": item.tmdb_kind or ("tv" if item.series_key else "movie"),
        "imdbId": item.imdb_id or "",
        "artist": getattr(item, "artist", "") or "",
        "albumTitle": getattr(item, "album_title", "") or "",
        "trackNumber": getattr(item, "track_number", None),
        "adminLocked": list(item.admin_locked or []),
        "posterUrl": _admin_thumb_url(item),
        "watchHref": f"/app/watch/{watch_key}",
        "classicHref": f"/watch/{watch_key}",
    }


def _admin_context_payload(ctx: dict) -> dict:
    return {
        "items": [
            _admin_item_payload(item, ctx["duplicate_details"])
            for item in ctx["items"]
        ],
        "catalogueSize": ctx["catalogue_size"],
        "filteredCount": ctx["filtered_count"],
        "page": ctx["page"],
        "totalPages": ctx["total_pages"],
        "pageSize": ctx["page_size"],
        "filterName": ctx["filter_name"],
        "searchQ": ctx["search_q"],
        "sortCol": ctx["sort_col"],
        "sortDir": ctx["sort_dir"],
        "stats": ctx["stats"],
        "knownSeries": ctx["known_series"],
        "filters": [{"value": key, "label": label} for key, label in _ADMIN_FILTERS],
        "sortOptions": [
            {"value": "date", "label": "Newest"},
            {"value": "title", "label": "Title"},
            {"value": "size", "label": "Size"},
            {"value": "quality", "label": "Quality"},
        ],
        "capabilities": {
            "gemini": bool(Var.GEMINI_API_KEY),
        },
    }


def _admin_status_payload() -> dict:
    from main.utils import codec_probe
    return {
        "seed": media_index.seed_state(),
        "enrich": media_index.enrichment_state(),
        "credits": media_index.credits_backfill_state(),
        "reindex": media_index.reindex_state(),
        "probe": codec_probe.state(),
        "episode_fill": media_index.episode_fill_state(),
        "migrate": media_index.migrate_state(),
        "catalogue_size": media_index.size(),
    }


def _require_api_admin(request: web.Request) -> dict:
    user = _get_admin_user(request)
    if user is None:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Admin access required"}),
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    return user


def _parse_chapter_time(raw: str) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        if ":" not in value:
            seconds = float(value)
            return seconds if seconds >= 0 else None
        parts = [float(part) for part in value.split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            total = minutes * 60 + seconds
        elif len(parts) == 3:
            hours, minutes, seconds = parts
            total = hours * 3600 + minutes * 60 + seconds
        else:
            return None
        return total if total >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_chapters_text(raw: str) -> list[dict]:
    chapters: list[dict] = []
    seen: set[float] = set()
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue
        match = re.match(r"^(\d+(?::\d{1,2}){0,2}(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:[-|]\s*)?(.*)$", text)
        if not match:
            continue
        start = _parse_chapter_time(match.group(1))
        title = (match.group(2) or "").strip() or "Chapter"
        if start is None:
            continue
        start = round(start, 2)
        if start in seen:
            continue
        seen.add(start)
        chapters.append({"start": start, "title": title[:80]})
        if len(chapters) >= 50:
            break
    chapters.sort(key=lambda chapter: chapter["start"])
    return chapters


def _format_chapters_text(chapters: list[dict] | None) -> str:
    lines: list[str] = []
    for chapter in chapters or []:
        try:
            start = float(chapter.get("start") or 0)
        except (TypeError, ValueError):
            continue
        title = str(chapter.get("title") or "Chapter").strip() or "Chapter"
        lines.append(f"{start:g} {title}")
    return "\n".join(lines)


def _prefers_react_admin(request: web.Request) -> bool:
    return (
        request.cookies.get("td_ui") == "react"
        and request.headers.get("HX-Request", "").lower() != "true"
    )


@routes.get("/admin/login")
async def admin_login_get(request: web.Request) -> web.Response:
    """Redirect legacy /admin/login URLs to /admin."""
    raise web.HTTPFound("/admin")


_FLASH_COOKIE = "admin_flash"


def _redirect_with_flash(message: str, target: str = "/admin") -> web.Response:
    """Set a short-lived flash cookie and redirect to a clean URL.

    Flash messages used to live in ``?flash=<encoded>`` query strings,
    which made the address bar ugly, leaked the text into browser
    history, re-showed the toast on refresh, and could expose state
    when an admin shared a URL. Cookies are a better fit: write once,
    read once, auto-cleared after the next /admin render.
    """
    from urllib.parse import quote as _q
    resp = web.HTTPFound(target)
    if message:
        # Cookie value is URL-encoded so commas / spaces / semicolons
        # don't mangle the Set-Cookie syntax. Decoded server-side
        # before rendering.
        resp.set_cookie(
            _FLASH_COOKIE, _q(message, safe=""),
            max_age=60, path="/admin", httponly=True, samesite="Lax",
        )
    return resp


def _pop_flash(request: web.Request, resp: web.Response) -> str:
    """Read the flash cookie if present and immediately delete it.

    Called from ``admin_home`` so the message renders exactly once
    and clears whether or not the user refreshes.
    """
    raw = request.cookies.get(_FLASH_COOKIE)
    if not raw:
        return ""
    from urllib.parse import unquote as _u
    try:
        msg = _u(raw)
    except Exception:
        msg = ""
    resp.del_cookie(_FLASH_COOKIE, path="/admin")
    return msg


@routes.get("/admin")
async def admin_home(request: web.Request) -> web.Response:
    if _prefers_react_admin(request):
        target = "/app/admin"
        if request.query_string:
            target = f"{target}?{request.query_string}"
        raise web.HTTPFound(target)

    user = _get_admin_user(request)
    if user is None:
        # Not authenticated or not admin — show Telegram login page
        bot_username = Var.BOT_USERNAME or (StreamBot.username or "")
        return web.Response(
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
            text=f"""<!doctype html>
<html lang="en" class="h-full dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Admin — Sign in</title>
  <link rel="stylesheet" href="/static/tailwind.css?v=1">
</head>
<body class="min-h-full bg-ink-900 flex items-center justify-center px-4">
  <div class="w-full max-w-sm bg-ink-800/60 border border-white/10
              rounded-2xl shadow-2xl p-8 text-center">
    <h1 class="text-2xl font-bold text-white mb-1">Admin</h1>
    <p class="text-sm text-slate-400 mb-8">Sign in with your Telegram account to continue.</p>
    <div id="_tg-root" class="flex justify-center"></div>
    <p class="mt-6 text-xs text-slate-600">Only the bot owner can access this panel.</p>
  </div>
  <script>
    function onTelegramAuth(user) {{
      fetch('/auth/telegram', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(user)
      }}).then(r => r.json()).then(d => {{
        if (d.token) {{
          // Store in sessionStorage for client-side JS use
          try {{ sessionStorage.setItem('td:auth', d.token); }} catch(_) {{}}
          // Cookie is set by the server — reload to get the authenticated page
          window.location.reload();
        }}
      }});
    }}
    (function() {{
      var s = document.createElement('script');
      s.async = true;
      s.src = 'https://telegram.org/js/telegram-widget.js?22';
      s.setAttribute('data-telegram-login', '{bot_username}');
      s.setAttribute('data-size', 'large');
      s.setAttribute('data-radius', '8');
      s.setAttribute('data-onauth', 'onTelegramAuth(user)');
      s.setAttribute('data-request-access', 'write');
      document.getElementById('_tg-root').appendChild(s);
    }})();
  </script>
</body>
</html>""",
        )
    _require_session(request)

    ctx = _admin_catalogue_context(request)
    tpl = _env.get_template("admin.html")
    body = await tpl.render_async(
        items=ctx["items"],
        catalogue_size=ctx["catalogue_size"],
        filtered_count=ctx["filtered_count"],
        page=ctx["page"],
        total_pages=ctx["total_pages"],
        page_size=ctx["page_size"],
        filter_name=ctx["filter_name"],
        search_q=ctx["search_q"],
        sort_col=ctx["sort_col"],
        sort_dir=ctx["sort_dir"],
        stats=ctx["stats"],
        duplicate_message_ids=ctx["duplicate_message_ids"],
        known_series=ctx["known_series"],
        flash=ctx["flash"],
        var=Var,
    )
    resp = _html(body)
    if ctx["raw_flash"]:
        resp.del_cookie(_FLASH_COOKIE, path="/admin")
    return resp


@routes.get("/api/app/admin")
async def api_app_admin(request: web.Request) -> web.Response:
    _require_api_admin(request)
    ctx = _admin_catalogue_context(request)
    payload = _admin_context_payload(ctx)
    payload["status"] = _admin_status_payload()
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


@routes.get("/api/app/admin/status")
async def api_app_admin_status(request: web.Request) -> web.Response:
    _require_api_admin(request)
    return web.json_response(_admin_status_payload(), headers={"Cache-Control": "no-store"})


@routes.get("/admin/dashboard")
async def admin_dashboard(request: web.Request) -> web.Response:
    """Catalogue insights dashboard — health metrics, storage breakdown,
    recent additions, top series, largest files, year distribution.
    """
    _require_session(request)
    tpl = _env.get_template("dashboard.html")
    body = await tpl.render_async(
        stats=media_index.dashboard_stats(),
        var=Var,
    )
    return _html(body)


def _is_htmx(request: web.Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


async def _run_metadata_cleanup() -> None:
    await media_index.enrich_all(bot=StreamBot, force=True)
    if not media_index.episode_fill_state().get("running"):
        await media_index.fill_episode_details(bot=StreamBot)


@routes.post("/admin/clear-audio-tmdb")
async def admin_clear_audio_tmdb(request: web.Request) -> web.Response:
    """Strip TMDB fields from audio items that were mis-enriched as movies/series."""
    _require_session(request)
    fixed = await media_index.clear_audio_tmdb_mismatches()
    msg = f"Cleared TMDB data from {fixed} audio item(s)" if fixed else "No mis-enriched audio items found"
    if _is_htmx(request):
        return web.Response(text=msg, status=200)
    raise _redirect_with_flash(msg)


@routes.post("/admin/clear-audio-thumbs")
async def admin_clear_audio_thumbs(request: web.Request) -> web.Response:
    """Bust the L1+L2 thumbnail cache for every audio item."""
    _require_session(request)
    msg = await _admin_clear_thumb_cache(audio_only=True)
    if _is_htmx(request):
        return web.Response(text=msg, status=200)
    raise _redirect_with_flash(msg)


@routes.post("/admin/clear-all-thumbs")
async def admin_clear_all_thumbs(request: web.Request) -> web.Response:
    """Bust the L1+L2 thumbnail cache for every item in the catalogue."""
    _require_session(request)
    msg = await _admin_clear_thumb_cache(audio_only=False)
    if _is_htmx(request):
        return web.Response(text=msg, status=200)
    raise _redirect_with_flash(msg)


@routes.post("/admin/enrich")
async def admin_enrich(request: web.Request) -> web.Response:
    """Fire-and-forget bulk TMDB enrichment.

    HTMX requests get a 204 so the admin page stays put and the live
    progress widget picks the new state up on its next /admin/status
    poll. Non-HTMX callers (curl etc.) still get the legacy redirect.
    """
    _require_session(request)
    form = await request.post()
    force = bool(form.get("force"))
    from urllib.parse import quote

    state = media_index.enrichment_state()
    if not state.get("running"):
        import asyncio as _aio
        _aio.create_task(media_index.enrich_all(bot=StreamBot, force=force))
    flash_msg = (
        "Enrichment already running — see progress below"
        if state.get("running")
        else "Enrichment started — leave the page open to watch progress"
    )
    if _is_htmx(request):
        return web.Response(status=204)
    raise _redirect_with_flash(flash_msg)


@routes.get("/admin/trending-gaps")
async def admin_trending_gaps(request: web.Request) -> web.Response:
    """TMDB trending/popular titles not yet in the catalogue.

    Shows posters, ratings, and TMDB links so the admin can decide what
    to source next. Refreshes from the same 24h cache as the user shelf.
    """
    _require_session(request)
    try:
        tr = await asyncio.wait_for(_trending.get_trending(), timeout=15.0)
        gaps = tr.get("missing", [])
    except Exception:
        logging.exception("admin: trending_gaps fetch failed")
        gaps = []
    tpl = _env.get_template("admin/trending_gaps.html")
    body = await tpl.render_async(gaps=gaps)
    return web.Response(text=body, content_type="text/html")


@routes.post("/admin/trending-gaps/refresh")
async def admin_trending_gaps_refresh(request: web.Request) -> web.Response:
    """Force-invalidate the trending cache so the next load fetches fresh data."""
    _require_session(request)
    _trending.invalidate()
    raise _redirect_with_flash("Trending cache cleared — next load will re-fetch from TMDB.")


async def _tmdb_preview_response(request: web.Request) -> web.Response:
    try:
        tmdb_id = int(request.query.get("id", ""))
    except ValueError:
        return web.json_response({"error": "id must be numeric"}, status=400)
    kind = (request.query.get("kind") or "movie").lower()
    if kind not in ("movie", "tv"):
        return web.json_response({"error": "kind must be movie or tv"}, status=400)

    from main.utils import tmdb
    if not tmdb.is_configured():
        return web.json_response({"error": "TMDB_API_KEY not set"}, status=503)
    hit = await tmdb.fetch_by_id(tmdb_id, kind)
    if hit is None:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({
        "tmdb_id": hit.tmdb_id,
        "kind": hit.kind,
        "title": hit.title,
        "year": hit.year,
        "overview": hit.overview,
        "poster_path": hit.poster_path,
        "genres": hit.genres,
        "imdb_id": hit.imdb_id,
    })


@routes.get("/admin/tmdb-preview")
async def admin_tmdb_preview(request: web.Request) -> web.Response:
    """Preview a TMDB record by id so admin can confirm before applying.

    Hit by the Edit modal whenever the operator types a TMDB id. Returns
    poster path, title, year, overview, genres so the UI can render a
    small preview card next to the input.
    """
    _require_session(request)
    return await _tmdb_preview_response(request)


@routes.get("/api/app/admin/tmdb-preview")
async def api_app_admin_tmdb_preview(request: web.Request) -> web.Response:
    _require_api_admin(request)
    return await _tmdb_preview_response(request)


async def _tmdb_resolve_imdb_response(request: web.Request) -> web.Response:
    imdb_id = (request.query.get("imdb_id") or "").strip()
    # Accept either ``tt1234567`` or the full IMDb URL — pull the tt-id
    # out so the admin can paste either form.
    import re as _re
    m = _re.search(r"tt\d{6,10}", imdb_id)
    if not m:
        return web.json_response(
            {"error": "Provide an IMDb tt-id like tt1234567"}, status=400,
        )
    imdb_id = m.group(0)

    from main.utils import tmdb
    if not tmdb.is_configured():
        return web.json_response({"error": "TMDB_API_KEY not set"}, status=503)
    resolved = await tmdb.resolve_imdb_id(imdb_id)
    if resolved is None:
        return web.json_response(
            {"error": "No TMDB record for that IMDb id"}, status=404,
        )
    tmdb_id, kind = resolved
    return web.json_response({"tmdb_id": tmdb_id, "kind": kind, "imdb_id": imdb_id})


@routes.get("/admin/tmdb-resolve-imdb")
async def admin_tmdb_resolve_imdb(request: web.Request) -> web.Response:
    """Resolve an IMDb tt-id to a TMDB (id, kind) pair via /find.

    Lets the Edit modal accept an IMDb URL/id and auto-fill the TMDB id
    + kind fields, sparing the operator a manual TMDB lookup.
    """
    _require_session(request)
    return await _tmdb_resolve_imdb_response(request)


@routes.get("/api/app/admin/tmdb-resolve-imdb")
async def api_app_admin_tmdb_resolve_imdb(request: web.Request) -> web.Response:
    _require_api_admin(request)
    return await _tmdb_resolve_imdb_response(request)


@routes.get("/admin/status")
async def admin_status(request: web.Request) -> web.Response:
    """JSON snapshot of the seed + enrichment progress. The admin page
    polls this every couple of seconds while either pipeline is active.
    """
    _require_session(request)
    return web.json_response(_admin_status_payload(), headers={"Cache-Control": "no-store"})


@routes.post("/admin/migrate-to-mongo")
async def admin_migrate_to_mongo(request: web.Request) -> web.Response:
    """Kick off a Mongo migration in the background. Progress flows
    through ``/admin/status`` (the same widget as the other long-
    running pipelines), so the admin page can keep updating the bar
    without holding an HTTP connection open.

    The endpoint itself only validates configuration + spawns the
    task. Real work happens in ``media_index.migrate_to_mongo``,
    which:
      * Builds a MongoStore + pings the cluster (connectivity check
        — bad URI / network / auth surfaces upfront, not mid-write).
      * Snapshots the in-memory dict under the lock so a parallel
        upload doesn't double-write.
      * Bulk-upserts in batches of 500 with done-counter bumps so
        the progress bar advances smoothly.
    """
    _require_session(request)
    import os
    if not os.environ.get("MONGO_URI"):
        raise _redirect_with_flash(
            "MONGO_URI env var is not set — configure Atlas first.",
        )

    if media_index.migrate_state().get("running"):
        if _is_htmx(request):
            return web.Response(status=204)
        raise _redirect_with_flash("Migration already running")

    db_name = os.environ.get("MONGO_DB") or "teledirect"
    items_coll = os.environ.get("MONGO_COLLECTION") or "items"
    meta_coll = os.environ.get("MONGO_META_COLLECTION") or "meta"

    import asyncio as _aio
    _aio.create_task(media_index.migrate_to_mongo(
        os.environ["MONGO_URI"], db_name, items_coll, meta_coll,
    ))
    if _is_htmx(request):
        return web.Response(status=204)
    raise _redirect_with_flash(
        f"Migration started against {db_name}.{items_coll} — watch the "
        "progress widget below.",
    )


@routes.post("/admin/dedupe")
async def admin_dedupe(request: web.Request) -> web.Response:
    """Find items that share a ``secure_hash`` (= same file uploaded
    multiple times) and delete the extras, keeping the lowest
    message_id (= the original upload).

    Quality variants of the same episode have DIFFERENT secure_hashes
    (different files, different file_unique_id) — they're untouched.
    This only collapses true duplicates: the same byte stream
    forwarded into BIN_CHANNEL more than once.
    """
    _require_session(request)
    raise _redirect_with_flash(await _admin_dedupe_uploads())


@routes.get("/admin/series-list")
async def admin_series_list(request: web.Request) -> web.Response:
    """Return all series (key, title, episode_count) for the merge-series picker."""
    _require_session(request)
    series: dict = {}
    for it in media_index._items.values():
        if it.series_key:
            if it.series_key not in series:
                series[it.series_key] = {
                    "key": it.series_key,
                    "title": it.series_title or it.series_key,
                    "count": 0,
                }
            series[it.series_key]["count"] += 1
    return web.json_response(
        sorted(series.values(), key=lambda s: s["title"].lower())
    )


@routes.post("/admin/merge-series")
async def admin_merge_series(request: web.Request) -> web.Response:
    """Move every episode from source_key into target_key.

    Updates series_key and series_title on all source items in-memory
    and persists to MongoDB.  Removes any 'series_title' admin_locked flag
    so the reassignment is not blocked by a previously-set lock.
    """
    _require_session(request)
    try:
        body = await request.json()
        source_key = (body.get("source_key") or "").strip()
        target_key = (body.get("target_key") or "").strip()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    if not source_key or not target_key:
        return web.json_response({"error": "source_key and target_key required"}, status=400)
    if source_key == target_key:
        return web.json_response({"error": "source and target must differ"}, status=400)

    source_items = [it for it in media_index._items.values() if it.series_key == source_key]
    target_items = [it for it in media_index._items.values() if it.series_key == target_key]

    if not target_items:
        return web.json_response({"error": f"Target series '{target_key}' not found"}, status=404)
    if not source_items:
        return web.json_response({"error": f"Source series '{source_key}' not found"}, status=404)

    target_title = target_items[0].series_title

    for it in source_items:
        it.series_key = target_key
        it.series_title = target_title
        # Remove series_title lock so the reassignment is not silently
        # blocked by a previously-set admin_locked entry.
        if it.admin_locked and "series_title" in it.admin_locked:
            it.admin_locked = [f for f in it.admin_locked if f != "series_title"]
        await media_index._store_upsert(it)

    await media_index.persist_now()
    logging.info(
        "admin: merged %d episodes from '%s' into '%s'",
        len(source_items), source_key, target_key,
    )
    return web.json_response({
        "ok": True,
        "merged": len(source_items),
        "target_title": target_title,
        "target_key": target_key,
    })


@routes.post("/admin/prune-stale")
async def admin_prune_stale(request: web.Request) -> web.Response:
    """Remove index entries whose BIN_CHANNEL messages no longer exist.

    Stale entries accumulate when the bot misses deletion events (OOM
    crash, restart). Checks every indexed message_id against BIN_CHANNEL
    in batches of 100 and removes any that come back empty.
    """
    _require_session(request)
    raise _redirect_with_flash(await _admin_prune_stale_entries())


@routes.post("/admin/fetch-episodes")
async def admin_fetch_episodes(request: web.Request) -> web.Response:
    """Backfill TMDB per-episode metadata (episode name + overview + still
    image) for TV rows where it's missing. One TMDB call per
    (tv_id, season) thanks to season-level caching, so even a 500-ep
    anime show only costs one call per season.
    """
    _require_session(request)
    import asyncio as _aio
    if not media_index.episode_fill_state().get("running"):
        _aio.create_task(media_index.fill_episode_details(bot=StreamBot))
    if _is_htmx(request):
        return web.Response(status=204)
    from urllib.parse import quote
    raise _redirect_with_flash('Episode details fetch queued')


@routes.post("/admin/probe-codecs")
async def admin_probe_codecs(request: web.Request) -> web.Response:
    """ffprobe every catalogue entry that hasn't been probed yet.

    Lets the watch page render the VLC-fallback overlay upfront for
    HEVC / 10-bit / AV1-in-MKV files instead of waiting for the
    browser to fail mid-playback. Bounded concurrency so we don't
    saturate Telegram's range endpoint.
    """
    _require_session(request)
    from main.utils import codec_probe
    import asyncio as _aio
    if not codec_probe.state().get("running"):
        _aio.create_task(codec_probe.probe_all_missing())
    if _is_htmx(request):
        return web.Response(status=204)
    from urllib.parse import quote
    raise _redirect_with_flash('Codec probe queued')



@routes.post("/admin/reindex")
async def admin_reindex(request: web.Request) -> web.Response:
    """Recompute series/movie/quality fields on every existing HubItem.

    Cheap — runs entirely against the cached metadata, no Telegram round
    trips. Used after the series or dedup detectors improve and older
    entries need to pick up the new logic.
    """
    _require_session(request)
    import asyncio as _aio
    state = media_index.reindex_state()
    if not state.get("running"):
        # Pass StreamBot so the completed re-index also uploads a fresh
        # Telegram-pinned state snapshot — cold restarts then restore
        # full enrichment data without re-hitting TMDB.
        _aio.create_task(media_index.reindex_all(bot=StreamBot))
    if _is_htmx(request):
        return web.Response(status=204)
    from urllib.parse import quote
    flash = "Re-index started — leave the page open to watch progress"
    raise _redirect_with_flash(flash)


@routes.post("/admin/action")
async def admin_action(request: web.Request) -> web.Response:
    _require_session(request)

    form = await request.post()
    action = form.get("action", "")
    # MultiDict.getall() raises KeyError when the key is missing; pass
    # a default so an empty form (mis-submission, no rows selected) just
    # falls into the 'nothing selected' branch instead of 500ing.
    ids = [int(x) for x in form.getall("ids", []) if str(x).isdigit()]

    # Preserve the current view (filter + page + search) across the redirect
    # so a bulk action from page 4 doesn't dump the operator back on page 1.
    from urllib.parse import urlencode as _urlencode
    _view_qs = {}
    if form.get("_filter"):
        _view_qs["filter"] = form.get("_filter")
    if form.get("_page"):
        _view_qs["page"] = form.get("_page")
    if form.get("_q"):
        _view_qs["q"] = form.get("_q")
    if form.get("_sort"):
        _view_qs["sort"] = form.get("_sort")
    if form.get("_dir"):
        _view_qs["dir"] = form.get("_dir")
    _target = "/admin" + (("?" + _urlencode(_view_qs)) if _view_qs else "")

    if not ids:
        raise _redirect_with_flash("Nothing selected", target=_target)

    if action == "delete":
        result = await _bulk_delete(ids)
        raise _redirect_with_flash(_bulk_delete_message(result), target=_target)

    if action in ("hide", "unhide"):
        hidden = action == "hide"
        n = 0
        for mid in ids:
            if await media_index.set_hidden(mid, hidden):
                n += 1
        verb = "Hidden" if hidden else "Unhidden"
        raise _redirect_with_flash(f"{verb} {n} entries", target=_target)

    if action == "retag":
        tags = _normalise_tags(form.get("tags", ""))
        n = await _bulk_retag(ids, tags)
        raise _redirect_with_flash(f"Re-tagged {n} entries", target=_target)

    if action == "quality":
        quality = (form.get("quality") or "").strip()
        if quality not in {"480p", "720p", "1080p", "4K"}:
            raise _redirect_with_flash("Invalid quality", target=_target)
        n = await _bulk_quality(ids, quality)
        raise _redirect_with_flash(f"Updated quality on {n} entries", target=_target)

    if action == "series":
        series_title = (form.get("series_title_bulk") or "").strip()
        season_raw = (form.get("season_bulk") or "").strip()
        if not series_title:
            raise _redirect_with_flash(
                "Series title can't be empty", target=_target,
            )
        try:
            season_num = int(season_raw) if season_raw else 1
        except ValueError:
            season_num = 1
        affected = await _bulk_assign_series(ids, series_title, season_num)
        raise _redirect_with_flash(
            f"Assigned series '{series_title}' (S{season_num:02d}) to "
            f"{affected} item(s)",
            target=_target,
        )

    if action == "enrich":
        _queue_selected_enrich(ids)
        raise _redirect_with_flash(
            f"Enrichment queued for {len(ids)} items — watch the progress bar",
            target=_target,
        )

    if action == "probe":
        _queue_selected_probe(ids)
        raise _redirect_with_flash(
            f"Probe queued for {len(ids)} item(s) — watch the progress bar",
            target=_target,
        )

    if action == "tmdb-id":
        tmdb_id_raw = (form.get("tmdb_id_bulk") or "").strip()
        tmdb_kind = (form.get("tmdb_kind_bulk") or "tv").strip().lower()
        if tmdb_kind not in ("tv", "movie"):
            tmdb_kind = "tv"
        try:
            tmdb_id_int = int(tmdb_id_raw)
        except ValueError:
            raise _redirect_with_flash("Enter a numeric TMDB id", target=_target)

        _queue_selected_tmdb(ids, tmdb_id_int, tmdb_kind)
        raise _redirect_with_flash(
            f"TMDB id {tmdb_id_int} ({tmdb_kind}) queued for {len(ids)} item(s) — "
            "watch the progress bar",
            target=_target,
        )

    raise _redirect_with_flash("Unknown action", target=_target)


async def _ai_models_response() -> web.Response:
    if not Var.GEMINI_API_KEY:
        return web.json_response([])

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        f"?key={Var.GEMINI_API_KEY}&pageSize=200"
    )
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return web.json_response([])
                data = await r.json()

        models = []
        for m in data.get("models", []):
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            raw_name = m.get("name", "")          # "models/gemini-2.5-flash"
            model_id = raw_name.split("/")[-1] if "/" in raw_name else raw_name
            display  = m.get("displayName", model_id)
            models.append({"id": model_id, "name": display})

        return web.json_response(
            models,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except Exception:
        logging.exception("admin: failed to list Gemini models")
        return web.json_response([])


@routes.get("/admin/ai-models")
async def admin_ai_models(request: web.Request) -> web.Response:
    """Return models available for the configured GEMINI_API_KEY.

    Calls Google's model-list endpoint and filters to those that support
    generateContent (i.e. can be used with our suggest endpoint).
    Returns an empty list when the key is not configured.
    """
    _require_session(request)
    return await _ai_models_response()


@routes.get("/api/app/admin/ai-models")
async def api_app_admin_ai_models(request: web.Request) -> web.Response:
    _require_api_admin(request)
    return await _ai_models_response()


async def _fetch_thumb_bytes(item) -> Optional[bytes]:
    """Fetch the video thumbnail via our own /thumb/ endpoint."""
    if not item.has_thumb:
        return None
    url = f"http://127.0.0.1:{Var.PORT}/thumb/{item.secure_hash}{item.message_id}.jpg"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception:
        pass
    return None


# Temporary store for pending AI filename proposals.
# { token: {"expires": float, "proposals": [{"message_id", "current_file_name",
#            "current_title", "proposed_file_name", "proposed_title",
#            "proposed_year", "proposed_quality", "reasoning"}, ...]} }
_pending_proposals: dict = {}
_PROPOSAL_TTL = 600  # 10 minutes


def _prune_proposals() -> None:
    now = time.time()
    stale = [k for k, v in _pending_proposals.items() if v["expires"] < now]
    for k in stale:
        _pending_proposals.pop(k, None)


@routes.post("/admin/ai-review")
async def admin_ai_review(request: web.Request) -> web.Response:
    """Run Gemini on selected items and return an HTML review panel.

    Called via HTMX from the bulk action toolbar. Proposals are stored
    server-side keyed by a short-lived token; the review panel embeds
    the token so /admin/ai-apply knows which batch to commit.
    """
    _require_session(request)
    if not Var.GEMINI_API_KEY:
        return web.Response(
            text='<p class="text-red-400 text-sm p-4">GEMINI_API_KEY not configured.</p>',
            content_type="text/html",
        )

    form = await request.post()
    ids = [int(x) for x in form.getall("ids") if str(x).isdigit()]
    if not ids:
        return web.Response(
            text='<p class="text-slate-400 text-sm p-4">No items selected.</p>',
            content_type="text/html",
        )

    from main.utils import filename_ai as _fnai

    # Send the AI a snapshot of existing series titles so it picks the
    # right spelling instead of inventing near-duplicates.
    _series_counts: dict = {}
    for it in media_index._items.values():
        if it.series_title:
            _series_counts[it.series_title] = _series_counts.get(it.series_title, 0) + 1
    _known_series = [s for s, _ in sorted(
        _series_counts.items(), key=lambda kv: (-kv[1], kv[0]),
    )]

    proposals = []
    for mid in ids:
        item = media_index.get_item(mid)
        if item is None or not item.file_name:
            continue
        result = await _fnai.parse_filename(
            item.file_name, known_series=_known_series,
        )
        if result is None:
            continue
        prop: dict = {
            "message_id": mid,
            "current_file_name": item.file_name,
            "current_title": item.title or "",
        }
        if result.get("is_device_generated"):
            prop["proposed_file_name"] = ""
            prop["proposed_title"] = item.title or ""
            prop["proposed_year"] = item.year or 0
            prop["proposed_quality"] = item.quality or ""
            prop["reasoning"] = result.get("reasoning", "Device-generated filename")
        else:
            prop["proposed_file_name"] = (result.get("clean_filename") or "").strip()
            prop["proposed_title"] = (result.get("title") or "").strip() or item.title or ""
            prop["proposed_year"] = result.get("year") or item.year or 0
            prop["proposed_quality"] = (result.get("quality") or "").strip() or item.quality or ""
            prop["reasoning"] = result.get("reasoning", "")
        # Skip if nothing would actually change
        if (prop["proposed_file_name"] == item.file_name
                and prop["proposed_title"] == (item.title or "")
                and prop["proposed_year"] == (item.year or 0)
                and prop["proposed_quality"] == (item.quality or "")):
            continue
        proposals.append(prop)

    if not proposals:
        return web.Response(
            text='<p class="text-slate-400 text-sm p-4">No changes suggested — filenames already look clean.</p>',
            content_type="text/html",
        )

    _prune_proposals()
    token = secrets.token_hex(12)
    _pending_proposals[token] = {
        "expires": time.time() + _PROPOSAL_TTL,
        "proposals": proposals,
    }

    rows_html = ""
    for p in proposals:
        fn_change = ""
        if p["proposed_file_name"] != p["current_file_name"]:
            fn_change = (
                f'<div class="flex items-baseline gap-1.5 flex-wrap">'
                f'<span class="text-slate-500 line-through text-[11px]">{p["current_file_name"] or "(blank)"}</span>'
                f'<span class="text-slate-400 text-[11px]">→</span>'
                f'<span class="text-violet-300 text-[11px]">{p["proposed_file_name"] or "(clear)"}</span>'
                f'</div>'
            )
        title_change = ""
        if p["proposed_title"] != p["current_title"]:
            title_change = (
                f'<div class="flex items-baseline gap-1.5 flex-wrap">'
                f'<span class="text-[10px] text-slate-600">Title:</span>'
                f'<span class="text-slate-500 line-through text-[11px]">{p["current_title"] or "(blank)"}</span>'
                f'<span class="text-slate-400 text-[11px]">→</span>'
                f'<span class="text-violet-200 text-[11px]">{p["proposed_title"]}</span>'
                f'</div>'
            )
        rows_html += (
            f'<label class="flex items-start gap-3 p-3 rounded-lg bg-ink-800/60 cursor-pointer'
            f' border border-white/5 hover:border-violet-400/30 transition-colors">'
            f'  <input type="checkbox" name="approve" value="{p["message_id"]}" checked'
            f'         class="mt-0.5 accent-violet-400 flex-shrink-0" />'
            f'  <div class="min-w-0 flex-1 space-y-0.5">'
            f'    {fn_change}{title_change}'
            f'    <p class="text-[10px] text-slate-600 italic">{p["reasoning"]}</p>'
            f'  </div>'
            f'</label>'
        )

    html = f"""
<div class="mt-6 rounded-xl border border-violet-400/20 bg-violet-500/5 p-5">
  <div class="flex items-center justify-between mb-4">
    <h3 class="text-sm font-semibold text-white">
      AI Filename Suggestions
      <span class="text-slate-400 font-normal ml-1">({len(proposals)} proposals)</span>
    </h3>
    <button type="button"
            onclick="document.getElementById('ai-review-panel').innerHTML=''"
            class="text-slate-500 hover:text-white text-sm transition-colors">✕</button>
  </div>
  <form method="post" action="/admin/ai-apply">
    <input type="hidden" name="token" value="{token}" />
    <div class="space-y-2 mb-5 max-h-96 overflow-y-auto pr-1">
      {rows_html}
    </div>
    <div class="flex items-center gap-2">
      <button type="submit"
              class="px-4 py-2 rounded-lg text-sm font-medium
                     bg-violet-500 hover:bg-violet-600 text-white transition-colors">
        Apply approved
      </button>
      <button type="button"
              onclick="document.getElementById('ai-review-panel').innerHTML=''"
              class="px-4 py-2 rounded-lg text-sm
                     bg-ink-800 text-slate-300 border border-white/5
                     hover:bg-ink-700 transition-colors">
        Dismiss
      </button>
    </div>
  </form>
</div>
"""
    return web.Response(text=html, content_type="text/html")


@routes.post("/admin/ai-apply")
async def admin_ai_apply(request: web.Request) -> web.Response:
    """Commit admin-approved AI filename proposals."""
    _require_session(request)
    form = await request.post()
    token = (form.get("token") or "").strip()
    approved = {int(x) for x in form.getall("approve") if str(x).isdigit()}

    batch = _pending_proposals.pop(token, None)
    if batch is None:
        raise _redirect_with_flash("Proposals expired or not found — run AI clean again")

    proposals = batch["proposals"]
    changed = 0
    for p in proposals:
        if p["message_id"] not in approved:
            continue
        item = media_index.get_item(p["message_id"])
        if item is None:
            continue
        if p["proposed_file_name"] != item.file_name:
            item.file_name = p["proposed_file_name"]
        if p["proposed_title"] and p["proposed_title"] != item.title:
            item.title = p["proposed_title"]
        if p["proposed_year"] and p["proposed_year"] != item.year:
            item.year = p["proposed_year"]
        if p["proposed_quality"] and p["proposed_quality"] != item.quality:
            item.quality = p["proposed_quality"]
        await media_index._store_upsert(item)
        changed += 1

    if changed:
        await media_index.persist_now()

    raise _redirect_with_flash(f"Applied {changed} of {len(approved)} approved proposals")


async def _ai_suggest_response(request: web.Request, message_id: int) -> web.Response:
    """Gemini Vision thumbnail analysis → structured metadata suggestions.

    Sends the video thumbnail + basic metadata to Gemini 2.0 Flash (free).
    Gemini reads any visible text in the thumbnail (course UI, show title,
    episode markers, watermarks, URLs) to identify the content and return
    pre-filled suggestions for title, series, season, episode, etc.
    """
    if not Var.GEMINI_API_KEY:
        return web.json_response(
            {"error": "GEMINI_API_KEY not configured — get a free key at aistudio.google.com"},
            status=503,
        )
    item = media_index.get_item(message_id)
    if item is None:
        return web.json_response({"error": "Item not found"}, status=404)

    is_audio = getattr(item, "media_kind", "") == "audio"

    # Field sets differ by media kind.
    # Audio tracks have artist/album/track_number instead of series/season/episode.
    if is_audio:
        _ALL_FIELDS = {"title", "artist", "album_title", "track_number",
                       "tags", "file_name"}
    else:
        _ALL_FIELDS = {"title", "year", "file_name", "series_title",
                       "season", "episode", "tags", "description"}

    raw_fields = (request.rel_url.query.get("fields") or "").strip()
    if raw_fields:
        wanted = {f.strip() for f in raw_fields.split(",") if f.strip() in _ALL_FIELDS}
        if not wanted:
            wanted = _ALL_FIELDS
    else:
        wanted = _ALL_FIELDS
    targeted = wanted != _ALL_FIELDS

    # Targeted requests (tags, file_name only) don't need the thumbnail —
    # the existing title/series text is enough context and skipping the
    # image makes the round-trip 10× faster and cheaper.
    thumb_bytes = None if targeted else await _fetch_thumb_bytes(item)

    if is_audio:
        meta_text = "\n".join([
            f"Filename: {item.file_name or '(none)'}",
            f"Current title: {item.title or '(none)'}",
            f"Current artist: {item.artist or '(none)'}",
            f"Current album: {item.album_title or '(none)'}",
            f"Track number: {item.track_number or '(none)'}",
            f"Duration: {item.duration}s" if item.duration else "Duration: unknown",
        ])
    else:
        meta_text = "\n".join([
            f"Filename: {item.file_name or '(none)'}",
            f"Current title: {item.title or '(none)'}",
            f"Current series: {item.series_title or '(none)'}",
            f"Duration: {item.duration}s" if item.duration else "Duration: unknown",
            f"File size: {humanbytes(item.file_size)}" if item.file_size else "",
        ])

    # Catalogue context so the AI matches existing series/tag vocabulary
    # instead of inventing near-duplicates.
    # Tags are split by media_kind: audio tracks see music genre/mood tags;
    # video items see video/genre tags. Without the split the top-N list is
    # dominated by whichever kind has more items (usually video) and the AI
    # suggests "action" / "drama" for a music track.
    series_counts: dict = {}
    tag_counts: dict = {}
    audio_tag_counts: dict = {}
    for it in media_index._items.values():
        if it.series_title:
            series_counts[it.series_title] = series_counts.get(it.series_title, 0) + 1
        _it_audio = getattr(it, "media_kind", "") == "audio"
        for t in (it.tags or []):
            if _it_audio:
                audio_tag_counts[t] = audio_tag_counts.get(t, 0) + 1
            else:
                tag_counts[t] = tag_counts.get(t, 0) + 1
    top_series = sorted(series_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
    top_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    top_audio_tags = sorted(audio_tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    series_list = ", ".join(f'"{s}" ({n})' for s, n in top_series) or "(none yet)"
    tag_list = ", ".join(t for t, _ in top_tags) or "(none yet)"
    audio_tag_list = ", ".join(t for t, _ in top_audio_tags) or "(none yet)"

    if is_audio:
        # ── Music-specific prompts ────────────────────────────────────────
        if targeted:
            focus_lines = []
            if "tags" in wanted:
                focus_lines.append(
                    "• tags: at most 3 music genre/mood tags, space-separated, lowercase "
                    "(e.g. 'folk classical devotional'). Prefer tags from the common-tags "
                    "list; only invent new ones when no existing tag fits."
                )
            if "file_name" in wanted:
                focus_lines.append(
                    "• file_name: 'Artist Name - Track Title.mp3' format. "
                    "Use the actual artist and track name, not the album."
                )
            if "title" in wanted:
                focus_lines.append("• title: the track name (song title), not the album name.")
            if "artist" in wanted:
                focus_lines.append("• artist: the performing artist(s), comma-separated if multiple.")
            if "album_title" in wanted:
                focus_lines.append("• album_title: the album or soundtrack name this track belongs to.")
            if "track_number" in wanted:
                focus_lines.append("• track_number: integer track position on the album (1-based).")
            prompt = (
                "You are a music catalogue assistant. Generate ONLY the requested "
                "fields based on the existing metadata below.\n\n"
                f"Audio file metadata:\n{meta_text}\n\n"
                f"Common tags already in catalogue: {audio_tag_list}\n\n"
                "Rules:\n"
                + "\n".join(focus_lines) + "\n"
                "• In 'reasoning' briefly explain your choices."
            )
        else:
            # Collect existing album vocabulary for context
            album_counts: dict = {}
            for it in media_index._items.values():
                if it.album_title:
                    album_counts[it.album_title] = album_counts.get(it.album_title, 0) + 1
            top_albums = sorted(album_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
            album_list = ", ".join(f'"{a}"' for a, _ in top_albums) or "(none yet)"

            prompt = (
                "You are a music catalogue assistant. Analyse this audio file and "
                "suggest accurate music metadata.\n\n"
                "The filename often encodes useful information — parse it carefully:\n"
                "• Artist name (often before ' - ' or in parentheses)\n"
                "• Track title (usually after ' - ')\n"
                "• Album name (sometimes in brackets or as folder name)\n"
                "• Track number (leading digits like '05' or 'Track 05')\n"
                "• Download sites (MassTamilan, StarMusiQ etc.) add watermarks — strip those.\n\n"
                f"Audio file metadata:\n{meta_text}\n\n"
                "Existing catalogue context (use exact spellings to avoid duplicates):\n"
                f"  Known albums/soundtracks: {album_list}\n"
                f"  Common tags (music only): {audio_tag_list}\n\n"
                "Rules:\n"
                "• title: the TRACK NAME (song title), not the album or movie name.\n"
                "• artist: the performing artist(s). Comma-separate if multiple.\n"
                "• album_title: the album or film soundtrack this belongs to. "
                "If the filename contains a film/show name that matches a known album above, "
                "use that EXACT spelling.\n"
                "• track_number: integer position on the album if determinable from filename.\n"
                "• tags: at most 3 genre/mood tags, lowercase, space-separated "
                "(e.g. 'folk classical devotional'). Prefer existing tags from the list above.\n"
                "• file_name: 'Artist Name - Track Title.mp3' (clean, no watermarks/site names).\n"
                "• Use empty string only for fields you truly cannot determine.\n"
                "• In 'reasoning' briefly explain what you found in the filename/metadata."
            )
    elif targeted:
        # ── Video targeted prompt ─────────────────────────────────────────
        focus_lines = []
        if "tags" in wanted:
            focus_lines.append(
                "• tags: at most 3 lowercase tags, space-separated. Prefer tags "
                "from the common-tags list; only invent new ones when no existing "
                "tag fits the content."
            )
        if "file_name" in wanted:
            focus_lines.append(
                "• file_name: build a clean descriptive filename. "
                "'Series Name - Episode Title.mp4' for series/courses, "
                "'Movie Title (Year).mkv' for movies."
            )
        if "description" in wanted:
            focus_lines.append(
                "• description: a one- to two-sentence summary of the content."
            )
        if "title" in wanted:
            focus_lines.append(
                "• title: the canonical title. For series items prefer the "
                "show name; for movies use the official title."
            )
        prompt = (
            "You are a media catalogue assistant. Generate ONLY the requested "
            "fields based on the existing metadata below.\n\n"
            f"File metadata:\n{meta_text}\n\n"
            "Existing catalogue context (match these spellings/vocabulary):\n"
            f"  Known series titles: {series_list}\n"
            f"  Common tags: {tag_list}\n\n"
            "Rules:\n"
            + "\n".join(focus_lines) + "\n"
            "• In 'reasoning' briefly explain your choices."
        )
    else:
        # ── Video full prompt ─────────────────────────────────────────────
        prompt = (
            "You are a media catalogue assistant. Analyse this video file and suggest "
            "accurate catalogue metadata.\n\n"
            "If a thumbnail image is provided, carefully read ALL visible text including:\n"
            "• Course / platform names (e.g. Three.js Journey, Udemy, YouTube)\n"
            "• Show or movie titles displayed on screen\n"
            "• Lesson / episode numbers or titles\n"
            "• Watermarks, UI elements, URLs, browser tabs\n"
            "• Any branding that identifies the content\n\n"
            f"File metadata:\n{meta_text}\n\n"
            "Existing catalogue context (use these spellings if a match exists "
            "— a near-duplicate would split a series into two):\n"
            f"  Known series titles: {series_list}\n"
            f"  Common tags: {tag_list}\n\n"
            "Rules:\n"
            "• Populate every field you can confidently identify.\n"
            "• For courses/series: set series_title, season (default 1), episode. "
            "If this item belongs to a known series above, use that EXACT spelling.\n"
            "• For movies: set title and year.\n"
            "• file_name: ALWAYS generate a descriptive filename based on what you found. "
            "Format: 'Series Name - Episode Title.mp4' for courses/episodes, "
            "'Movie Title (Year).mkv' for movies. Never leave file_name empty if you "
            "identified the content — it is the primary display label.\n"
            "• tags: at most 3 tags, space-separated, lowercase. Prefer tags already "
            "in the common-tags list above; only invent new tags when no existing "
            "tag fits.\n"
            "• Use 0 or empty string only for fields you truly cannot determine.\n"
            "• In 'reasoning' briefly explain what you found in the thumbnail."
        )

    parts = []
    if thumb_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(thumb_bytes).decode(),
            }
        })
    parts.append({"text": prompt})

    _ALL_PROPS = {
        # Shared
        "title":        {"type": "STRING"},
        "file_name":    {"type": "STRING"},
        "tags":         {"type": "STRING", "description": "Up to 3 space-separated tags, lowercase"},
        # Video-only
        "year":         {"type": "INTEGER"},
        "series_title": {"type": "STRING"},
        "season":       {"type": "INTEGER"},
        "episode":      {"type": "INTEGER"},
        "description":  {"type": "STRING"},
        # Audio/music-only
        "artist":       {"type": "STRING", "description": "Performing artist(s), comma-separated"},
        "album_title":  {"type": "STRING", "description": "Album or soundtrack name"},
        "track_number": {"type": "INTEGER", "description": "Track position on album"},
    }
    schema = {
        "type": "OBJECT",
        "properties": {
            **{k: v for k, v in _ALL_PROPS.items() if k in wanted},
            "reasoning":    {"type": "STRING"},
        },
        "required": sorted(wanted) + ["reasoning"],
    }

    import re as _re
    model = request.rel_url.query.get("model", "gemini-2.5-flash-lite")
    # Sanitise: only allow alphanumeric + hyphens + dots (no path traversal)
    if not _re.fullmatch(r"[a-zA-Z0-9][-a-zA-Z0-9._]*", model):
        model = "gemini-2.5-flash-lite"
    gemini_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model}:generateContent?key={Var.GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    }

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                gemini_url, json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as r:
                try:
                    resp_data = await r.json(content_type=None)
                except Exception:
                    body = await r.text()
                    return web.json_response(
                        {"error": f"Gemini returned non-JSON (HTTP {r.status}): {body[:200]}"},
                        status=502,
                    )
                if r.status != 200:
                    err = resp_data.get("error", {}).get("message", str(resp_data))
                    return web.json_response({"error": f"Gemini: {err}"}, status=502)

        candidates = resp_data.get("candidates") or []
        if not candidates:
            # Gemini blocked the response (safety filter, token limit, etc.)
            block_reason = (
                resp_data.get("promptFeedback", {}).get("blockReason")
                or resp_data.get("candidates", [{}])[0].get("finishReason")
                or "no candidates returned"
            )
            return web.json_response(
                {"error": f"Gemini blocked the response: {block_reason}"},
                status=502,
            )
        finish_reason = candidates[0].get("finishReason", "")
        if finish_reason not in ("STOP", "MAX_TOKENS", ""):
            return web.json_response(
                {"error": f"Gemini stopped early: {finish_reason}"},
                status=502,
            )
        try:
            text = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            return web.json_response(
                {"error": f"Unexpected Gemini response structure: {e} — {str(resp_data)[:200]}"},
                status=502,
            )
        data = json.loads(text)

        # Drop zero / blank fields so the modal only fills what Gemini knows.
        clean = {
            k: v for k, v in data.items()
            if not (isinstance(v, int) and v == 0)
            and not (isinstance(v, str) and not v.strip())
        }
        # Cap tags at 3 regardless of what Gemini returned.
        if "tags" in clean:
            clean["tags"] = " ".join(clean["tags"].split()[:3])
        return web.json_response(clean)

    except asyncio.TimeoutError:
        return web.json_response(
            {"error": "Gemini timed out (90 s) — thumbnail may be too large; try a model without vision or re-try"},
            status=504,
        )
    except Exception:
        logging.exception("admin: Gemini suggest failed for bin:%d", message_id)
        return web.json_response(
            {"error": "Gemini request failed — check server logs"}, status=500
        )


@routes.post(r"/admin/ai-suggest/{id:\d+}")
async def admin_ai_suggest(request: web.Request) -> web.Response:
    _require_session(request)
    return await _ai_suggest_response(request, int(request.match_info["id"]))


@routes.post(r"/api/app/admin/item/{id:\d+}/ai-suggest")
async def api_app_admin_item_ai_suggest(request: web.Request) -> web.Response:
    _require_api_admin(request)
    return await _ai_suggest_response(request, int(request.match_info["id"]))


def _clear_tmdb_fields(item: HubItem) -> None:
    item.tmdb_id = None
    item.tmdb_kind = ""
    item.imdb_id = ""
    item.tmdb_vote_average = 0.0
    item.tmdb_vote_count = 0
    item.tmdb_vote_checked_at = 0.0
    item.poster_path = ""
    item.backdrop_path = ""
    item.overview = ""
    item.tmdb_genres = []
    item.cast = []
    item.director = ""
    item.enriched_at = 0.0
    media_index.clear_episode_tmdb_fields(item)
    item.trailer_key = ""


@routes.post(r"/admin/clear-tmdb/{id:\d+}")
async def admin_clear_tmdb(request: web.Request) -> web.Response:
    """Wipe all TMDB-derived fields for a catalogue entry.

    Useful when auto-enrichment matched the wrong movie/show. Clears
    tmdb_id, poster, backdrop, genres, overview, imdb_id, and resets
    enriched_at so the next enrich pass will search fresh.
    """
    _require_session(request)
    message_id = int(request.match_info["id"])
    item = media_index.get_item(message_id)
    if item is None:
        from urllib.parse import quote
        raise _redirect_with_flash(f"bin:{message_id} not found")

    async with media_index._lock:
        _clear_tmdb_fields(item)
        media_index._persist_unlocked()

    await media_index._store_upsert(item)

    from urllib.parse import quote
    raise _redirect_with_flash(f"TMDB enrichment cleared for bin:{message_id}")


@routes.post(r"/admin/edit/{id:\d+}")
async def admin_edit(request: web.Request) -> web.Response:
    """Per-row edit: title, year, tags, description in one go.

    After saving, fire a background re-enrich for this single entry so a
    title fix immediately retries the TMDB lookup with the new query —
    the operator doesn't have to also click "Enrich" to refresh
    misclassified items.
    """
    _require_session(request)
    message_id = int(request.match_info["id"])
    form = await request.post()

    new_title = (form.get("title") or "").strip()
    year_raw = (form.get("year") or "").strip()
    new_year = None
    if year_raw:
        try:
            new_year = int(year_raw)
        except ValueError:
            from urllib.parse import quote
            raise _redirect_with_flash('Year must be a number')
    new_tags = _normalise_tags(form.get("tags") or "")
    new_description = (form.get("description") or "").strip()
    new_file_name = (form.get("file_name") or "").strip()
    new_series_title = (form.get("series_title") or "").strip()
    season_raw = (form.get("season") or "").strip()
    episode_raw = (form.get("episode") or "").strip()
    episode_end_raw = (form.get("episode_end") or "").strip()
    new_season: Optional[int] = int(season_raw) if season_raw.isdigit() else None
    new_episode: Optional[int] = int(episode_raw) if episode_raw.isdigit() else None
    new_episode_end: Optional[int] = int(episode_end_raw) if episode_end_raw.isdigit() else None
    # Discard end if not strictly after start
    if new_episode_end is not None and new_episode is not None and new_episode_end <= new_episode:
        new_episode_end = None

    # Skip Intro timestamps (seconds, float or None)
    def _parse_sec(val: str) -> Optional[float]:
        try:
            v = float(val.strip())
            return v if v >= 0 else None
        except (ValueError, AttributeError):
            return None
    new_intro_start = _parse_sec(form.get("intro_start") or "")
    new_intro_end   = _parse_sec(form.get("intro_end") or "")
    if new_intro_start is not None and new_intro_end is not None and new_intro_end <= new_intro_start:
        new_intro_end = None  # discard nonsensical range

    # Custom thumbnail override
    thumb_url = (form.get("thumb_url") or "").strip()

    # Music metadata fields
    new_artist = (form.get("artist") or "").strip()
    new_album_title = (form.get("album_title") or "").strip()
    track_number_raw = (form.get("track_number") or "").strip()
    new_track_number: Optional[int] = int(track_number_raw) if track_number_raw.isdigit() else None

    # Optional manual TMDB-id override. When present, it bypasses the
    # title-search path entirely — admin tells us which record to use
    # by its provider id (handy when titles are too generic for search
    # to disambiguate).
    tmdb_id_raw = (form.get("tmdb_id") or "").strip()
    tmdb_kind = (form.get("tmdb_kind") or "movie").strip().lower()
    if tmdb_kind not in ("movie", "tv"):
        tmdb_kind = "movie"
    manual_tmdb_id: Optional[int] = None
    if tmdb_id_raw:
        try:
            manual_tmdb_id = int(tmdb_id_raw)
        except ValueError:
            from urllib.parse import quote
            raise _redirect_with_flash('TMDB ID must be numeric')

    if not new_title:
        from urllib.parse import quote
        raise _redirect_with_flash('Title is required')

    # Capture what the operator typed so we can decide whether to
    # re-enrich after the caption write.
    item_before = media_index.get_item(message_id)
    title_changed = item_before and item_before.title != new_title
    year_changed = item_before and item_before.year != new_year

    def apply(entry, item):
        entry.title = new_title
        entry.year = new_year
        entry.tags = new_tags
        entry.description = new_description
        # file_name override — not in the caption format, applied directly.
        item.file_name = new_file_name
        # Series assignment — groups standalone videos into a series page.
        if new_series_title:
            item.series_title = new_series_title
            item.series_key = series_parse.slugify(new_series_title)
            item.season = new_season if new_season is not None else 1
            item.episode = new_episode
            item.episode_end = new_episode_end
            item.movie_key = ""
        else:
            # Clearing series_title converts back to a standalone item.
            item.series_title = ""
            item.series_key = ""
            item.season = None
            item.episode = None
            item.episode_end = None
            if not item.movie_key:
                item.movie_key = compute_movie_key(
                    new_title, new_year, new_file_name or item.file_name
                )
        # Intro timestamps — always apply regardless of series/movie/standalone status
        item.intro_start = new_intro_start
        item.intro_end   = new_intro_end
        # Music metadata — always apply (new_artist / new_album_title may be blank
        # to intentionally clear the field; track_number None means "not entered").
        item.artist = new_artist
        if new_album_title != item.album_title:
            item.album_title = new_album_title
            # Key by album title only so multi-artist soundtrack albums group
            # correctly. Artist alone as a fallback groups all a performer's
            # ungrouped singles together pending a proper album scan.
            from main.utils.series import slugify as _slugify
            if item.album_title:
                item.album_key = _slugify(item.album_title)
            elif item.artist:
                item.album_key = _slugify(item.artist)
            else:
                item.album_key = ""
        item.track_number = new_track_number

    status, reason = await _rewrite_caption(message_id, apply)

    # Update the admin_locked field.
    # The modal submits the current lock state (with any per-field ✕
    # unlocks already applied), so we start from that base and then
    # auto-add locks for fields that were explicitly set.
    if status in ("written", "local-only"):
        item_after = media_index.get_item(message_id)
        if item_after is not None:
            # Start from whatever the modal submitted (handles explicit unlocks)
            submitted_locked = [
                f.strip() for f in (form.get("admin_locked") or "").split(",")
                if f.strip() in ("title", "year", "series_title")
            ]
            locked = set(submitted_locked)
            # Auto-lock non-empty fields that were explicitly provided
            if new_title:
                locked.add("title")
            if new_year is not None:
                locked.add("year")
            else:
                locked.discard("year")
            if new_series_title:
                locked.add("series_title")
            else:
                locked.discard("series_title")
            item_after.admin_locked = sorted(locked)
            await media_index._store_upsert(item_after)

    # Manual TMDB-id override wins over everything. Fetch the record
    # immediately (this isn't fire-and-forget — admin is waiting for
    # the result and a failure should be reported) and apply it,
    # which also writes the canonical metadata back to BIN.
    manual_tmdb_status = ""
    if (status in ("written", "local-only")
            and manual_tmdb_id is not None):
        ok = await media_index.enrich_with_tmdb_id(
            message_id, manual_tmdb_id, tmdb_kind, bot=StreamBot,
        )
        manual_tmdb_status = "applied" if ok else "failed"

    # If the title or year changed (and there's no manual override) and
    # TMDB is configured, retry the search-based lookup so misclassified
    # entries can be corrected by editing alone. Reset the existing
    # TMDB ID so enrich_one searches fresh by the new title rather than
    # just refreshing the old record. Skip for 'removed' / 'failed' —
    # there's nothing to re-enrich.
    elif status in ("written", "local-only") and (title_changed or year_changed):
        from main.utils import tmdb
        if tmdb.is_configured():
            item = media_index.get_item(message_id)
            if item is not None:
                async with media_index._lock:
                    _clear_tmdb_fields(item)
                    media_index._persist_unlocked()
                await media_index._store_upsert(item)
            import asyncio as _aio
            _aio.create_task(
                media_index.enrich_one(message_id, bot=StreamBot)
            )

    thumb_msg = ""
    if thumb_url and status in ("written", "local-only"):
        thumb_msg = f" — {await _apply_custom_thumbnail(message_id, thumb_url)}"

    from urllib.parse import quote
    if status == "written":
        msg = f"Updated bin:{message_id}"
        if title_changed or year_changed:
            msg += " — re-enrich queued"
        msg += thumb_msg
    elif status == "local-only":
        # Surface the specific Telegram error code so the operator
        # sees the real cause instead of a one-size diagnosis.
        # https://core.telegram.org/method/messages.editMessage
        if reason == "edit-time-expired":
            cause = (
                "Telegram returned MESSAGE_EDIT_TIME_EXPIRED. Bots can "
                "only edit their own messages within 48 hours; this "
                "message is older. The in-memory entry was updated."
            )
        elif reason == "author-required":
            cause = (
                "Telegram returned MESSAGE_AUTHOR_REQUIRED. Usually "
                "means the message was forwarded into BIN_CHANNEL (the "
                "'Forwarded from' header makes the caption non-editable "
                "even for the forwarder) or posted by another author. "
                "New uploads now use copy() instead of forward() to "
                "avoid this; pre-existing forwarded entries stay "
                "editable in memory only."
            )
        elif reason == "message-id-invalid":
            cause = (
                "Telegram returned MESSAGE_ID_INVALID but a probe "
                "confirmed the message still exists. Likely the bot "
                "lacks edit permission on this specific message. "
                "In-memory entry was updated."
            )
        else:
            cause = (
                "Telegram refused the caption edit. The in-memory "
                "entry was still updated."
            )
        msg = f"Updated bin:{message_id} in the catalogue. {cause}"
        if title_changed or year_changed:
            msg += " Re-enrich queued."
        msg += thumb_msg
    elif status == "removed":
        msg = (
            f"bin:{message_id} doesn't exist on BIN_CHANNEL anymore — "
            "removed from the catalogue. Refresh to drop the row."
        )
    else:
        msg = f"Edit failed for bin:{message_id} (see server logs)"
    raise _redirect_with_flash(msg)


# --- Bulk operations --------------------------------------------------


def _normalise_tags(raw: str) -> List[str]:
    parts = [p.strip().lstrip("#").lower() for p in raw.replace(",", " ").split()]
    return [p for p in parts if p]


def _bulk_delete_message(result: dict[str, int]) -> str:
    deleted = result.get("deleted", 0)
    removed = result.get("removed", 0)
    hidden = result.get("hidden", 0)
    failed = result.get("failed", 0)
    if deleted and not removed and not hidden and not failed:
        return f"Deleted {deleted} entr{'y' if deleted == 1 else 'ies'}"
    parts = []
    if deleted:
        parts.append(f"Deleted {deleted} from BIN")
    if removed:
        parts.append(f"Removed {removed} stale catalogue row{'s' if removed != 1 else ''}")
    if hidden:
        parts.append(
            f"Hidden {hidden} catalogue row{'s' if hidden != 1 else ''} because BIN delete failed; grant bot delete/admin rights to remove BIN messages"
        )
    if failed:
        parts.append(f"Failed {failed} entr{'y' if failed == 1 else 'ies'}")
    return "; ".join(parts) if parts else "Deleted 0 entries"


async def _bulk_delete_fallback(mid: int, result: dict[str, int]) -> None:
    try:
        msg = await StreamBot.get_messages(Var.BIN_CHANNEL, mid)
        if msg is None or getattr(msg, "empty", False):
            await media_index.remove(mid, bot=StreamBot)
            result["removed"] += 1
            return
    except Exception:
        logging.debug("admin: delete fallback probe failed for bin:%d", mid, exc_info=True)
    if await media_index.set_hidden(mid, True):
        result["hidden"] += 1
    else:
        result["failed"] += 1


async def _bulk_delete(ids: List[int]) -> dict[str, int]:
    result = {"deleted": 0, "removed": 0, "hidden": 0, "failed": 0}
    for mid in ids:
        try:
            deleted_count = await StreamBot.delete_messages(Var.BIN_CHANNEL, mid)
        except Exception:
            logging.exception("admin: delete failed for bin:%d", mid)
            await _bulk_delete_fallback(mid, result)
            continue
        if int(deleted_count or 0) <= 0:
            logging.warning("admin: delete returned 0 for bin:%d", mid)
            await _bulk_delete_fallback(mid, result)
            continue
        await media_index.remove(mid, bot=StreamBot)
        result["deleted"] += 1
    return result


async def _rewrite_caption(message_id: int, mutate) -> Tuple[str, str]:
    """Fetch a BIN message, rebuild its IndexEntry, mutate, and persist.

    ``mutate(entry, item)`` modifies the IndexEntry in place. Returns
    ``(status, reason_code)``:

    Status:
      • ``"written"`` — caption was edited on Telegram.
      • ``"local-only"`` — Telegram refused the edit; in-memory only.
      • ``"removed"`` — the message truly no longer exists.
      • ``"failed"`` — unexpected error; no state changes.

    ``reason_code`` distinguishes WHY a local-only happened so the
    admin sees the right diagnosis. Per
    https://core.telegram.org/method/messages.editMessage the
    documented edit errors are MESSAGE_ID_INVALID,
    MESSAGE_AUTHOR_REQUIRED, MESSAGE_EDIT_TIME_EXPIRED, and a few
    others. We pass through the specific code so the flash message
    can name the cause instead of guessing.
    """
    from pyrogram.errors import MessageNotModified
    from pyrogram.errors.exceptions.bad_request_400 import MessageIdInvalid
    _UNEDITABLE: tuple = ()
    try:
        from pyrogram.errors.exceptions.forbidden_403 import InlineBotRequired
        _UNEDITABLE += (InlineBotRequired,)
    except ImportError:
        pass
    try:
        from pyrogram.errors.exceptions.forbidden_403 import MessageAuthorRequired
        _UNEDITABLE += (MessageAuthorRequired,)
    except ImportError:
        pass
    # MESSAGE_EDIT_TIME_EXPIRED — Telegram caps bot self-edits at 48h
    # for private/group chats. Channels normally don't apply this to
    # admin posts, but forwarded posts sometimes do.
    _EDIT_TIME_EXPIRED: tuple = ()
    try:
        from pyrogram.errors.exceptions.bad_request_400 import (
            MessageEditTimeExpired,
        )
        _EDIT_TIME_EXPIRED += (MessageEditTimeExpired,)
    except ImportError:
        pass

    item = media_index.get_item(message_id)
    if item is None:
        return "failed", "no-item"
    entry = IndexEntry(
        title=item.title,
        year=item.year,
        description=item.description,
        tags=list(item.tags),
        tmdb_id=item.tmdb_id,
        tmdb_kind=item.tmdb_kind,
        imdb_id=item.imdb_id,
        poster_path=item.poster_path,
        backdrop_path=item.backdrop_path,
    )
    mutate(entry, item)

    # Mongo holds the authoritative catalogue now, so the BIN caption
    # rewrite is redundant — and historically the source of the
    # MESSAGE_AUTHOR_REQUIRED errors on legacy forwarded entries.
    # Skip the Telegram edit entirely; apply locally + Mongo only.
    if media_index._store_active():
        _apply_local_only(message_id, entry)
        await media_index._store_upsert(media_index.get_item(message_id))
        return "written", ""

    try:
        await StreamBot.edit_message_caption(
            chat_id=Var.BIN_CHANNEL,
            message_id=message_id,
            caption=render(entry),
        )
    except MessageNotModified:
        return "written", ""
    except MessageIdInvalid:
        # MESSAGE_ID_INVALID is overloaded: it fires both when the
        # message truly is gone AND when the bot can't reach it (often
        # a forwarded post that the bot didn't originate). Probe via
        # get_messages — if it comes back non-empty, the message
        # exists and we just can't edit its caption.
        try:
            probe = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
            still_exists = probe is not None and not getattr(probe, "empty", False)
        except Exception:
            still_exists = False
        if still_exists:
            logging.info(
                "admin: bin:%d MESSAGE_ID_INVALID but exists; in-memory only",
                message_id,
            )
            _apply_local_only(message_id, entry)
            return "local-only", "message-id-invalid"
        logging.info(
            "admin: bin:%d truly absent on Telegram; removing", message_id,
        )
        await media_index.remove(message_id, bot=StreamBot)
        return "removed", ""
    except _EDIT_TIME_EXPIRED as exc:
        logging.info(
            "admin: bin:%d MESSAGE_EDIT_TIME_EXPIRED (%s); in-memory only",
            message_id, exc.__class__.__name__,
        )
        _apply_local_only(message_id, entry)
        return "local-only", "edit-time-expired"
    except _UNEDITABLE as exc:
        logging.info(
            "admin: bin:%d caption read-only (%s); skipping caption write",
            message_id, exc.__class__.__name__,
        )
        _apply_local_only(message_id, entry)
        return "local-only", "author-required"
    except Exception:
        logging.exception("admin: edit_caption failed for bin:%d", message_id)
        return "failed", "unknown"

    # Refresh the in-memory entry from the rewritten caption.
    try:
        fresh = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
        await media_index.add_from_message(fresh)
    except Exception:
        logging.exception("admin: post-edit refresh failed for bin:%d", message_id)
    return "written", ""


def _apply_local_only(message_id: int, entry) -> None:
    """Update the in-memory HubItem fields when we couldn't push the
    edit to Telegram. Lets renames/retags still take effect on the hub
    even when the underlying channel message is read-only for our bot.
    """
    existing = media_index.get_item(message_id)
    if existing is None:
        return
    existing.title = entry.title
    existing.year = entry.year
    existing.description = entry.description
    existing.tags = list(entry.tags)


async def _bulk_retag(ids: List[int], tags: List[str]) -> int:
    def apply(entry, _item):
        entry.tags = list(tags)
    n = 0
    for mid in ids:
        status, _reason = await _rewrite_caption(mid, apply)
        if status in ("written", "local-only"):
            n += 1
    return n


async def _bulk_quality(ids: List[int], quality: str) -> int:
    # Quality is encoded into the description line so it round-trips
    # through the existing _extract_quality() regex used at index time.
    def apply(entry, item):
        # Replace an existing quality token if one is at the head of the
        # description, otherwise prepend.
        desc = (item.description or "").strip()
        for q in ("4K", "1080p", "720p", "480p", "2160p", "UHD", "FHD", "HD", "SD"):
            if desc.lower().startswith(q.lower()):
                desc = desc[len(q):].lstrip(" ·-—")
                break
        entry.description = (quality + (" · " + desc if desc else "")).strip()
    n = 0
    for mid in ids:
        status, _reason = await _rewrite_caption(mid, apply)
        if status in ("written", "local-only"):
            n += 1
    return n


def _admin_json_message(message: str, **extra) -> web.Response:
    payload = {"ok": True, "message": message}
    payload.update(extra)
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


def _admin_json_ids(raw) -> List[int]:
    ids: List[int] = []
    for value in raw or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


async def _bulk_assign_series(ids: List[int], series_title: str, season_num: int) -> int:
    series_key = series_parse.slugify(series_title)
    affected: list = []
    async with media_index._lock:
        existing_eps = set()
        for mid in sorted(ids):
            it = media_index.get_item(mid)
            if it and it.episode is not None:
                existing_eps.add(it.episode)
        next_ep = 1
        for mid in sorted(ids):
            it = media_index.get_item(mid)
            if it is None:
                continue
            it.series_title = series_title
            it.series_key = series_key
            it.season = season_num
            if it.episode is None:
                while next_ep in existing_eps:
                    next_ep += 1
                it.episode = next_ep
                existing_eps.add(next_ep)
                next_ep += 1
            it.movie_key = ""
            affected.append(it)
        media_index._persist_unlocked()

    for it in affected:
        await media_index._store_upsert(it)
    return len(affected)


def _queue_selected_enrich(ids: List[int]) -> None:
    async def _run(id_list: List[int]) -> None:
        video_count = sum(
            1 for mid in id_list
            if getattr(media_index.get_item(mid), "media_kind", "") != "audio"
        )
        media_index._enrich_state.update(
            running=True, done=0, total=video_count,
            enriched=0, failed=0, last_title="",
            started_at=time.time(), finished_at=0.0,
        )
        done = enriched = 0
        for mid in id_list:
            item = media_index.get_item(mid)
            if item:
                if getattr(item, "media_kind", "") == "audio":
                    continue
                media_index._enrich_state["last_title"] = item.title or ""
            ok = await media_index.enrich_one(mid, bot=StreamBot)
            if ok:
                enriched += 1
            done += 1
            media_index._enrich_state.update(
                done=done, enriched=enriched, failed=done - enriched,
            )
            await asyncio.sleep(0)
        media_index._enrich_state.update(running=False, finished_at=time.time())
        logging.info("react admin bulk enrich: %d/%d enriched", enriched, done)

    asyncio.create_task(_run(ids))


def _queue_selected_probe(ids: List[int]) -> None:
    from main.utils import codec_probe

    async def _run_probe(id_list: List[int]) -> None:
        codec_probe.probe_state.update(
            running=True, done=0, total=len(id_list),
            found_incompatible=0, started_at=time.time(), finished_at=0.0,
        )
        done = found = 0
        for mid in id_list:
            item = media_index.get_item(mid)
            if item is None:
                continue
            item.probed_at = 0.0
            ok = await codec_probe.probe_item(item)
            if ok:
                found += 1
            done += 1
            codec_probe.probe_state["done"] = done
            codec_probe.probe_state["found_incompatible"] = found
            await asyncio.sleep(0)
        codec_probe.probe_state.update(running=False, finished_at=time.time())
        logging.info("react admin bulk probe: %d/%d had video streams", found, done)

    asyncio.create_task(_run_probe(ids))


def _queue_selected_tmdb(ids: List[int], tmdb_id: int, tmdb_kind: str) -> None:
    async def _run_tmdb(id_list: List[int]) -> None:
        media_index._enrich_state.update(
            running=True, done=0, total=len(id_list),
            enriched=0, failed=0, last_title="",
            started_at=time.time(), finished_at=0.0,
        )
        done = enriched = 0
        for mid in id_list:
            item = media_index.get_item(mid)
            if item:
                media_index._enrich_state["last_title"] = item.title or ""
            ok = await media_index.enrich_with_tmdb_id(
                mid, tmdb_id, tmdb_kind, bot=StreamBot,
            )
            if ok:
                enriched += 1
            done += 1
            media_index._enrich_state.update(
                done=done, enriched=enriched, failed=done - enriched,
            )
            await asyncio.sleep(0)
        media_index._enrich_state.update(running=False, finished_at=time.time())
        logging.info(
            "react admin bulk tmdb-id (%s/%d): %d/%d applied",
            tmdb_kind, tmdb_id, enriched, done,
        )

    asyncio.create_task(_run_tmdb(ids))


async def _admin_dedupe_uploads() -> str:
    by_key: dict = {}
    for it in media_index._items.values():
        if not it.secure_hash or not it.file_size:
            continue
        by_key.setdefault((it.secure_hash, it.file_size), []).append(it)

    deleted = 0
    groups = 0
    for items in by_key.values():
        if len(items) <= 1:
            continue
        groups += 1
        keepers = sorted(items, key=lambda v: v.message_id)
        for extra in keepers[1:]:
            try:
                await StreamBot.delete_messages(Var.BIN_CHANNEL, extra.message_id)
            except Exception:
                logging.exception(
                    "admin: dedupe delete failed for bin:%d",
                    extra.message_id,
                )
                continue
            await media_index.remove(extra.message_id, bot=StreamBot)
            deleted += 1

    return (
        f"De-dup pass: {deleted} extra upload{'' if deleted == 1 else 's'} "
        f"removed across {groups} duplicate group{'' if groups == 1 else 's'}."
    )


async def _admin_prune_stale_entries() -> str:
    removed = await media_index.prune_stale(StreamBot, Var.BIN_CHANNEL)
    thumbs_removed = 0
    if media_index._store_active():
        try:
            thumb_ids = await media_index._store.thumb_ids()
            live_ids = set(media_index._items.keys())
            live_ids.update(
                thumb_cache.cache_id(mid, audio=True)
                for mid, item in media_index._items.items()
                if getattr(item, "media_kind", "") == "audio"
            )
            orphan_ids = [t for t in thumb_ids if t not in live_ids]
            for orphan in orphan_ids:
                try:
                    await media_index._store.remove_thumb(orphan)
                    thumbs_removed += 1
                except Exception:
                    logging.exception(
                        "admin: orphan thumb delete failed for bin:%d", orphan,
                    )
        except Exception:
            logging.exception("admin: orphan thumb scan failed")

    msg = (
        f"Pruned {removed} stale entr{'y' if removed == 1 else 'ies'} "
        f"(checked {len(media_index._items)} items)."
    )
    if thumbs_removed:
        msg += f" Cleared {thumbs_removed} orphan thumb(s)."
    return msg


async def _admin_clear_thumb_cache(*, audio_only: bool) -> str:
    ids = [
        it.message_id
        for it in media_index._items.values()
        if not audio_only or getattr(it, "media_kind", "") == "audio"
    ]
    cleared = 0
    for mid in ids:
        try:
            await thumb_cache.clear(mid)
            cleared += 1
        except Exception:
            pass
    scope = "audio item" if audio_only else "item"
    return f"Cleared thumbnail cache for {cleared} {scope}(s)"


@routes.post("/api/app/admin/action")
async def api_app_admin_action(request: web.Request) -> web.Response:
    _require_api_admin(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    action = (data.get("action") or "").strip()
    ids = _admin_json_ids(data.get("ids"))
    if not ids:
        return web.json_response({"error": "Nothing selected"}, status=400)

    if action == "delete":
        result = await _bulk_delete(ids)
        return _admin_json_message(_bulk_delete_message(result))

    if action in ("hide", "unhide"):
        hidden = action == "hide"
        n = 0
        for mid in ids:
            if await media_index.set_hidden(mid, hidden):
                n += 1
        verb = "Hidden" if hidden else "Unhidden"
        return _admin_json_message(f"{verb} {n} entries")

    if action == "retag":
        tags = _normalise_tags(data.get("tags") or "")
        n = await _bulk_retag(ids, tags)
        return _admin_json_message(f"Re-tagged {n} entries")

    if action == "quality":
        quality = (data.get("quality") or "").strip()
        if quality not in {"480p", "720p", "1080p", "4K"}:
            return web.json_response({"error": "Invalid quality"}, status=400)
        n = await _bulk_quality(ids, quality)
        return _admin_json_message(f"Updated quality on {n} entries")

    if action == "series":
        series_title = (data.get("seriesTitle") or "").strip()
        if not series_title:
            return web.json_response({"error": "Series title can't be empty"}, status=400)
        try:
            season_num = int(data.get("season") or 1)
        except (TypeError, ValueError):
            season_num = 1
        n = await _bulk_assign_series(ids, series_title, season_num)
        return _admin_json_message(
            f"Assigned series '{series_title}' (S{season_num:02d}) to {n} item(s)"
        )

    if action == "enrich":
        _queue_selected_enrich(ids)
        return _admin_json_message(
            f"Enrichment queued for {len(ids)} items",
            status=_admin_status_payload(),
        )

    if action == "probe":
        _queue_selected_probe(ids)
        return _admin_json_message(
            f"Probe queued for {len(ids)} item(s)",
            status=_admin_status_payload(),
        )

    if action == "tmdb-id":
        tmdb_kind = (data.get("tmdbKind") or "tv").strip().lower()
        if tmdb_kind not in ("tv", "movie"):
            tmdb_kind = "tv"
        try:
            tmdb_id = int(data.get("tmdbId") or 0)
        except (TypeError, ValueError):
            return web.json_response({"error": "Enter a numeric TMDB id"}, status=400)
        if tmdb_id < 1:
            return web.json_response({"error": "Enter a numeric TMDB id"}, status=400)
        _queue_selected_tmdb(ids, tmdb_id, tmdb_kind)
        return _admin_json_message(
            f"TMDB id {tmdb_id} ({tmdb_kind}) queued for {len(ids)} item(s)",
            status=_admin_status_payload(),
        )

    return web.json_response({"error": "Unknown action"}, status=400)


@routes.post("/api/app/admin/maintenance")
async def api_app_admin_maintenance(request: web.Request) -> web.Response:
    _require_api_admin(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)
    action = (data.get("action") or "").strip()

    if action == "enrich":
        force = bool(data.get("force"))
        state = media_index.enrichment_state()
        if not state.get("running"):
            asyncio.create_task(media_index.enrich_all(bot=StreamBot, force=force))
            return _admin_json_message("Enrichment started", status=_admin_status_payload())
        return _admin_json_message("Enrichment already running", status=_admin_status_payload())

    if action == "backfill-credits":
        if (
            media_index.enrichment_state().get("running")
            or media_index.credits_backfill_state().get("running")
        ):
            return _admin_json_message(
                "Credits backfill is already running",
                status=_admin_status_payload(),
            )
        asyncio.create_task(media_index.backfill_missing_credits(bot=StreamBot))
        return _admin_json_message(
            "Credits and ratings backfill queued",
            status=_admin_status_payload(),
        )

    if action == "reindex":
        state = media_index.reindex_state()
        if not state.get("running"):
            asyncio.create_task(media_index.reindex_all(bot=StreamBot))
        return _admin_json_message("Re-index queued", status=_admin_status_payload())

    if action == "probe-codecs":
        from main.utils import codec_probe
        if not codec_probe.state().get("running"):
            asyncio.create_task(codec_probe.probe_all_missing())
        return _admin_json_message("Codec probe queued", status=_admin_status_payload())

    if action == "fetch-episodes":
        if not media_index.episode_fill_state().get("running"):
            asyncio.create_task(media_index.fill_episode_details(bot=StreamBot))
        return _admin_json_message("Episode details fetch queued", status=_admin_status_payload())

    if action == "metadata-cleanup":
        if (
            media_index.enrichment_state().get("running")
            or media_index.credits_backfill_state().get("running")
            or media_index.episode_fill_state().get("running")
        ):
            return _admin_json_message(
                "Metadata backfill is already running",
                status=_admin_status_payload(),
            )
        asyncio.create_task(_run_metadata_cleanup())
        return _admin_json_message(
            "Metadata backfill queued: TMDB credits, ratings, posters, and episode metadata",
            status=_admin_status_payload(),
        )

    if action == "clear-audio-thumbs":
        return _admin_json_message(await _admin_clear_thumb_cache(audio_only=True))

    if action == "clear-all-thumbs":
        return _admin_json_message(await _admin_clear_thumb_cache(audio_only=False))

    if action == "clear-audio-tmdb":
        fixed = await media_index.clear_audio_tmdb_mismatches()
        msg = (
            f"Cleared TMDB data from {fixed} audio item(s)"
            if fixed else "No mis-enriched audio items found"
        )
        return _admin_json_message(msg)

    if action == "dedupe":
        return _admin_json_message(await _admin_dedupe_uploads())

    if action == "prune-non-admin":
        removed = await media_index.prune_non_admin_uploads(StreamBot, int(Var.BIN_CHANNEL))
        return _admin_json_message(
            f"Removed {removed} known non-admin upload{'' if removed == 1 else 's'} from the catalogue"
        )

    if action == "prune-stale":
        return _admin_json_message(await _admin_prune_stale_entries())

    if action == "migrate-to-mongo":
        import os
        if not os.environ.get("MONGO_URI"):
            return web.json_response(
                {"error": "MONGO_URI env var is not set"},
                status=400,
            )
        if not media_index.migrate_state().get("running"):
            db_name = os.environ.get("MONGO_DB") or "teledirect"
            items_coll = os.environ.get("MONGO_COLLECTION") or "items"
            meta_coll = os.environ.get("MONGO_META_COLLECTION") or "meta"
            asyncio.create_task(media_index.migrate_to_mongo(
                os.environ["MONGO_URI"], db_name, items_coll, meta_coll,
            ))
        return _admin_json_message("Migration queued", status=_admin_status_payload())

    return web.json_response({"error": "Unknown maintenance action"}, status=400)


# ── SPA sub-page endpoints ────────────────────────────────────────────────────

@routes.get("/api/app/admin/dashboard")
async def api_app_admin_dashboard(request: web.Request) -> web.Response:
    """Catalogue insights for the React admin dashboard."""
    _require_api_admin(request)
    s = media_index.dashboard_stats()
    _duplicate_details, duplicate_groups, duplicate_extras = _admin_duplicate_candidates(
        [it for it in media_index._items.values() if not it.hidden],
    )
    return web.json_response({
        **s,
        "duplicate_groups": duplicate_groups,
        "duplicate_extras": duplicate_extras,
        "storage_by_quality": [{"quality": q, "bytes": b, "label": humanbytes(b)} for q, b in s["storage_by_quality"]],
        "storage_by_codec":   [{"codec": c,   "bytes": b, "label": humanbytes(b)} for c, b in s["storage_by_codec"]],
        "year_distribution":  [{"decade": d,  "count": n} for d, n in s["year_distribution"]],
        "total_size_label":   humanbytes(s.get("total_size_bytes") or 0),
        "credits_backfill":    media_index.credits_backfill_state(),
        "recent_additions":   [{**it, "watchHref": f"/app/watch/{it['secure_hash']}{it['message_id']}", "fileSizeLabel": humanbytes(it.get("file_size") or 0)} for it in s["recent_additions"]],
        "largest_items":      [{**it, "watchHref": f"/app/watch/{it['secure_hash']}{it['message_id']}", "fileSizeLabel": humanbytes(it.get("file_size") or 0)} for it in s["largest_items"]],
    }, headers={"Cache-Control": "no-store"})


@routes.get("/api/app/admin/trending-gaps")
async def api_app_admin_trending_gaps(request: web.Request) -> web.Response:
    """TMDB trending titles missing from the library."""
    _require_api_admin(request)
    try:
        tr = await asyncio.wait_for(_trending.get_trending(), timeout=15.0)
        gaps = tr.get("missing", [])
    except Exception:
        logging.exception("api: trending_gaps fetch failed")
        gaps = []
    return web.json_response({"gaps": gaps}, headers={"Cache-Control": "no-store"})


@routes.post("/api/app/admin/trending-gaps/refresh")
async def api_app_admin_trending_gaps_refresh(request: web.Request) -> web.Response:
    """Invalidate trending cache so the next GET re-fetches."""
    _require_api_admin(request)
    _trending.invalidate()
    return web.json_response({"ok": True})


@routes.get(r"/api/app/admin/item/{id:\d+}")
async def api_app_admin_item_get(request: web.Request) -> web.Response:
    """Full item payload for the edit modal."""
    _require_api_admin(request)
    message_id = int(request.match_info["id"])
    item = media_index.get_item(message_id)
    if item is None:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(
        {**_admin_item_payload(item, set()),
         "introStart": item.intro_start,
         "introEnd": item.intro_end,
         "recapStart": item.recap_start,
         "recapEnd": item.recap_end,
         "chapters": _format_chapters_text(item.chapters),
         "trackNumber": getattr(item, "track_number", None),
         "thumbUrl": ""},
        headers={"Cache-Control": "no-store"},
    )


@routes.post(r"/api/app/admin/item/{id:\d+}/clear-tmdb")
async def api_app_admin_item_clear_tmdb(request: web.Request) -> web.Response:
    """Wipe all TMDB-derived fields for an item (JSON-auth version of /admin/clear-tmdb/{id})."""
    _require_api_admin(request)
    message_id = int(request.match_info["id"])
    item = media_index.get_item(message_id)
    if item is None:
        return web.json_response({"error": "Not found"}, status=404)
    async with media_index._lock:
        _clear_tmdb_fields(item)
        media_index._persist_unlocked()
    await media_index._store_upsert(item)
    return web.json_response({"ok": True, "item": _admin_item_payload(item, set())},
                             headers={"Cache-Control": "no-store"})


@routes.post(r"/api/app/admin/item/{id:\d+}/subtitles")
async def api_app_admin_item_upload_subtitle(request: web.Request) -> web.Response:
    """Upload and attach a durable SRT/VTT sidecar to one video."""
    _require_api_admin(request)
    message_id = int(request.match_info["id"])
    item = media_index.get_item(message_id)
    if item is None:
        return web.json_response({"error": "Not found"}, status=404)
    if getattr(item, "media_kind", "") == "audio":
        return web.json_response({"error": "Subtitles can only be attached to video"}, status=400)
    # Reject oversized regular requests before aiohttp parses their multipart
    # body.  Chunked requests remain protected by the application's 30 MB
    # maximum and the post-read limit below.
    if request.content_length is not None and request.content_length > _MAX_SIDECAR_SUBTITLE_BYTES + 64 * 1024:
        return web.json_response({"error": "Subtitle file exceeds 10 MB"}, status=413)
    try:
        form = await request.post()
        upload = form.get("file")
        # Browsers normally submit a basename, but normalize Windows paths as
        # well before using the name in Telegram metadata or API responses.
        filename = Path((getattr(upload, "filename", "") or "").replace("\\", "/")).name
        if not filename or Path(filename).suffix.lower() not in {".srt", ".vtt"}:
            return web.json_response({"error": "Choose an .srt or .vtt file"}, status=400)
        data = upload.file.read() if hasattr(upload, "file") else b""
    except Exception:
        return web.json_response({"error": "Invalid subtitle upload"}, status=400)
    if not data:
        return web.json_response({"error": "Subtitle file is empty"}, status=400)
    if len(data) > _MAX_SIDECAR_SUBTITLE_BYTES:
        return web.json_response({"error": "Subtitle file exceeds 10 MB"}, status=413)
    try:
        bin_message = await StreamBot.send_document(
            Var.BIN_CHANNEL,
            io.BytesIO(data),
            file_name=filename,
            caption=f"Subtitle sidecar for bin:{message_id}",
            disable_notification=True,
        )
        sidecar = ExternalSubtitle(
            bin_message_id=bin_message.id,
            secure_hash=get_hash(bin_message),
            language=language_from_filename(filename),
            label=derive_label(language_from_filename(filename), filename),
        )
        if not await media_index.attach_subtitle(message_id, sidecar):
            await StreamBot.delete_messages(Var.BIN_CHANNEL, bin_message.id)
            return web.json_response({"error": "Video no longer exists"}, status=404)
        item = media_index.get_item(message_id)
        if item is not None:
            await media_index._store_upsert(item)
            media_index.schedule_snapshot(StreamBot)
        return web.json_response({
            "ok": True,
            "item": _admin_item_payload(item, set()) if item else None,
            "message": f"Attached {filename}",
        })
    except Exception:
        logging.exception("admin: subtitle upload failed for bin:%d", message_id)
        return web.json_response({"error": "Could not save subtitle"}, status=502)


@routes.delete(r"/api/app/admin/item/{id:\d+}/subtitles/{bin_id:\d+}")
async def api_app_admin_item_delete_subtitle(request: web.Request) -> web.Response:
    """Remove an admin-managed durable sidecar for every viewer."""
    _require_api_admin(request)
    message_id = int(request.match_info["id"])
    bin_id = int(request.match_info["bin_id"])
    item = media_index.get_item(message_id)
    if item is None:
        return web.json_response({"error": "Not found"}, status=404)
    if not await media_index.detach_subtitle(message_id, bin_id):
        return web.json_response({"error": "Sidecar subtitle not found"}, status=404)
    item = media_index.get_item(message_id)
    if item is not None:
        await media_index._store_upsert(item)
    if not await media_index.subtitle_is_referenced(bin_id):
        try:
            await StreamBot.delete_messages(Var.BIN_CHANNEL, bin_id)
        except Exception:
            logging.warning("admin: removed sidecar link but could not delete bin:%d", bin_id)
    return web.json_response({
        "ok": True,
        "item": _admin_item_payload(item, set()) if item else None,
        "message": "Removed subtitle",
    })


@routes.post(r"/api/app/admin/item/{id:\d+}")
async def api_app_admin_item_save(request: web.Request) -> web.Response:
    """JSON edit endpoint — mirrors the classic /admin/edit/{id} form handler."""
    _require_api_admin(request)
    message_id = int(request.match_info["id"])
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    new_title = (body.get("title") or "").strip()
    if not new_title:
        return web.json_response({"error": "Title is required"}, status=400)

    def _int_or_none(val):
        try: return int(val) if val is not None and str(val).strip() else None
        except (ValueError, TypeError): return None

    def _float_or_none(val):
        try:
            v = float(val)
            return v if v >= 0 else None
        except (ValueError, TypeError):
            return None

    new_year        = _int_or_none(body.get("year"))
    new_tags        = _normalise_tags(body.get("tags") or "")
    new_description = (body.get("description") or "").strip()
    new_file_name   = (body.get("fileName") or "").strip()
    new_series_title = (body.get("seriesTitle") or "").strip()
    new_season       = _int_or_none(body.get("season"))
    new_episode      = _int_or_none(body.get("episode"))
    new_episode_end  = _int_or_none(body.get("episodeEnd"))
    new_intro_start  = _float_or_none(body.get("introStart"))
    new_intro_end    = _float_or_none(body.get("introEnd"))
    if new_intro_start is not None and new_intro_end is not None and new_intro_end <= new_intro_start:
        new_intro_end = None
    new_recap_start  = _float_or_none(body.get("recapStart"))
    new_recap_end    = _float_or_none(body.get("recapEnd"))
    if new_recap_start is not None and new_recap_end is not None and new_recap_end <= new_recap_start:
        new_recap_end = None
    new_chapters     = _parse_chapters_text(body.get("chapters") or "")
    new_artist       = (body.get("artist") or "").strip()
    new_album_title  = (body.get("albumTitle") or "").strip()
    new_track_number = _int_or_none(body.get("trackNumber"))
    thumb_url        = (body.get("thumbUrl") or "").strip()
    tmdb_id_raw      = body.get("tmdbId")
    tmdb_kind        = (body.get("tmdbKind") or "movie").lower()
    if tmdb_kind not in ("movie", "tv"):
        tmdb_kind = "movie"
    manual_tmdb_id   = _int_or_none(tmdb_id_raw)
    admin_locked_src = body.get("adminLocked") or []

    item_before = media_index.get_item(message_id)
    if item_before is None:
        return web.json_response({"error": "Item not found"}, status=404)

    title_changed = new_title != (item_before.title or "")
    year_changed  = new_year != item_before.year

    def apply(entry, item):
        entry.title       = new_title
        entry.year        = new_year
        entry.tags        = new_tags
        entry.description = new_description
        item.file_name    = new_file_name
        if new_series_title:
            item.series_title = new_series_title
            item.series_key   = series_parse.slugify(new_series_title)
            item.season       = new_season if new_season is not None else 1
            item.episode      = new_episode
            item.episode_end  = new_episode_end
            item.movie_key    = ""
        else:
            item.series_title = ""
            item.series_key   = ""
            item.season = item.episode = item.episode_end = None
            if not item.movie_key:
                item.movie_key = compute_movie_key(new_title, new_year, new_file_name or item.file_name)
        item.intro_start = new_intro_start
        item.intro_end   = new_intro_end
        item.recap_start = new_recap_start
        item.recap_end   = new_recap_end
        item.chapters    = list(new_chapters)
        item.artist      = new_artist
        if new_album_title != item.album_title:
            item.album_title = new_album_title
            from main.utils.series import slugify as _sl
            if item.album_title:
                item.album_key = _sl(item.album_title)
            elif item.artist:
                item.album_key = _sl(item.artist)
            else:
                item.album_key = ""
        item.track_number = new_track_number

    status, _reason = await _rewrite_caption(message_id, apply)

    if status in ("written", "local-only"):
        item_after = media_index.get_item(message_id)
        if item_after is not None:
            submitted = [f for f in admin_locked_src if f in ("title", "year", "series_title")]
            locked = set(submitted)
            if new_title: locked.add("title")
            if new_year is not None: locked.add("year")
            else: locked.discard("year")
            if new_series_title: locked.add("series_title")
            else: locked.discard("series_title")
            item_after.admin_locked = sorted(locked)
            await media_index._store_upsert(item_after)

    if status in ("written", "local-only") and manual_tmdb_id is not None and manual_tmdb_id > 0:
        await media_index.enrich_with_tmdb_id(message_id, manual_tmdb_id, tmdb_kind, bot=StreamBot)
    elif status in ("written", "local-only") and (title_changed or year_changed):
        from main.utils import tmdb
        if tmdb.is_configured():
            item_now = media_index.get_item(message_id)
            if item_now is not None:
                async with media_index._lock:
                    _clear_tmdb_fields(item_now)
                    media_index._persist_unlocked()
                await media_index._store_upsert(item_now)
            asyncio.create_task(media_index.enrich_one(message_id, bot=StreamBot))

    if status == "failed":
        return web.json_response({"error": "Save failed — item may no longer exist"}, status=500)

    if thumb_url:
        await _apply_custom_thumbnail(message_id, thumb_url)

    item_result = media_index.get_item(message_id)
    return web.json_response({
        "ok": True,
        "status": status,
        "item": _admin_item_payload(item_result, set()) if item_result else None,
    }, headers={"Cache-Control": "no-store"})


@routes.post(r"/admin/hide/{id:\d+}")
async def admin_hide(request: web.Request) -> web.Response:
    """Toggle hidden flag on one item. Body: action=hide|show."""
    _require_session(request)
    message_id = int(request.match_info["id"])
    form = await request.post()
    hidden = (form.get("action") or "hide") == "hide"
    found = await media_index.set_hidden(message_id, hidden)
    if not found:
        raise web.HTTPNotFound(text="Item not found")
    return web.Response(
        status=204,
        headers={"HX-Trigger": "catalogue-updated"},
    )

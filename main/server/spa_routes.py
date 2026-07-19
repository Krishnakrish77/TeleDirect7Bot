"""React SPA routes and JSON API for the media hub.

The SPA is intentionally mounted under /app first.  That keeps the existing
server-rendered UI and raw stream URL catch-all untouched while the React hub
reaches parity route by route.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html as html_lib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin

from aiohttp import web

from main.server.tmdb_images import tmdb_image_proxy, tmdb_image_url
from main.utils import codec_probe
from main.utils import cw_store
from main.utils import media_index
from main.utils import playlist_store
from main.utils import ratings_store
from main.utils import rec_engine
from main.utils import share_meta
from main.utils import thumb_cache
from main.utils import trending
from main.utils import wh_store
from main.utils.codec_probe import _clean_music_tag
from main.utils.download_urls import as_download_url, is_download_query
from main.utils.hub_query import AlbumGroup, HubItem, MovieGroup, SeriesGroup
from main.utils.human_readable import humanbytes
from main.utils.playback import should_offer_hls_for_video
from main.utils.user_auth import get_user
from main.utils import wyzie_subtitles
from main.vars import Var


routes = web.RouteTableDef()
_CW_KEY_RE = re.compile(r'^[A-Za-z0-9_-]*[A-Za-z_-](\d+)$')

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_APP_DIR = _STATIC_DIR / "app"
_APP_INDEX = _APP_DIR / "index.html"

_SORT_OPTIONS = [
    ("newest", "Newest"),
    ("oldest", "Oldest"),
    ("title_az", "Title A-Z"),
    ("title_za", "Title Z-A"),
    ("largest", "Largest"),
]

_VALID_VIEWS = {"", "list", "movies", "series", "music"}
_APP_ROUTE_RE = re.compile(r"^/app(?:/.*)?$")
_UI_COOKIE = "td_ui"
_API_CACHE_TTL = 30.0
_SLOW_HUB_LOG_MS = 1000.0
_HOME_SHELF_LIMIT_DEFAULT = 7
_HOME_RECOMMENDATIONS_TIMEOUT = 2.5
_HOME_TRENDING_TIMEOUT = 1.5
_HOME_TOP_PLAYS_TIMEOUT = 1.0
_HOME_OPTIONAL_SHELVES_TIMEOUT = max(
    _HOME_RECOMMENDATIONS_TIMEOUT,
    _HOME_TRENDING_TIMEOUT,
    _HOME_TOP_PLAYS_TIMEOUT,
)
_HOME_REC_REASONS_TIMEOUT = 0.6
_VISIBLE_ART_RECOVERY_LIMIT = 3
_VISIBLE_ART_RECOVERY_TIMEOUT = 6.0
_api_response_cache: dict[str, tuple[str, float]] = {}
_filter_cache: tuple[dict, float] | None = None
_HUB_CARD_PAYLOAD_KEYS = (
    "type",
    "itemId",
    "title",
    "subtitle",
    "year",
    "mediaKind",
    "posterUrl",
    "durationLabel",
    "quality",
    "genres",
    "externalRating",
    "ratingCounts",
    "artist",
    "albumTitle",
    "trailerKey",
    "href",
    "playHref",
    "detailsHref",
    "watchKey",
    "aspect",
    "variantCount",
    "episodeCount",
    "seasonCount",
    "trackCount",
    "watched",
    "newEpisode",
)


@routes.get("/robots.txt")
async def robots_txt(_: web.Request) -> web.Response:
    lines = [
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /api",
        "Disallow: /auth",
        "Disallow: /watch",
        "Disallow: /hls",
        "Disallow: /sub",
        "Disallow: /thumb",
        "Disallow: /app/admin",
        "Disallow: /app/live-tv",
        "Disallow: /app/watch",
        "Disallow: /app/watchlist",
        "Disallow: /app/liked-songs",
        "Disallow: /app/playlists",
        "Disallow: /app/stats",
        "Allow: /app",
        "Allow: /manifest.json",
        "Allow: /favicon.svg",
    ]
    return web.Response(
        text="\n".join(lines) + "\n",
        content_type="text/plain",
        headers={"Cache-Control": "max-age=86400"},
    )


def _json_dumps(data) -> str:
    return json.dumps(data, separators=(",", ":"))


def _json_text(text: str, *, status: int = 200) -> web.Response:
    return web.Response(
        text=text,
        content_type="application/json",
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def _json(data, *, status: int = 200) -> web.Response:
    return _json_text(_json_dumps(data), status=status)


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(value, hi))


def _home_shelf_limit() -> int:
    return _int_env("HUB_HOME_SHELVES", _HOME_SHELF_LIMIT_DEFAULT, lo=1, hi=12)


async def _home_recommendation_shelf(user_id: int) -> tuple[list, list, list]:
    mark = time.monotonic()
    profile, dismissed = await asyncio.wait_for(
        asyncio.gather(
            rec_engine._collect_signal_profile(user_id),
            rec_engine.dismissed_store.get_dismissed_ids(user_id),
        ),
        timeout=_HOME_RECOMMENDATIONS_TIMEOUT,
    )
    remaining = _HOME_RECOMMENDATIONS_TIMEOUT - (time.monotonic() - mark)
    if remaining <= 0.05:
        raise asyncio.TimeoutError
    rec_items, personal_shelves = await asyncio.wait_for(
        asyncio.gather(
            rec_engine.get_recommendations(user_id, profile=profile, dismissed=dismissed),
            rec_engine.get_personal_shelves(user_id, profile=profile, dismissed=dismissed),
        ),
        timeout=remaining,
    )
    rec_reasons = []
    remaining = _HOME_RECOMMENDATIONS_TIMEOUT - (time.monotonic() - mark)
    if rec_items and remaining > 0.05:
        try:
            rec_reasons = await asyncio.wait_for(
                rec_engine.get_recommendation_reasons(user_id, rec_items, profile=profile),
                timeout=min(_HOME_REC_REASONS_TIMEOUT, remaining),
            )
        except asyncio.TimeoutError:
            logging.warning("spa hub: recommendation reasons timed out")
        except Exception:
            logging.exception("spa hub: recommendation reasons failed")
    return rec_items, personal_shelves, rec_reasons


async def _home_trending_shelf() -> dict:
    return await asyncio.wait_for(
        trending.get_trending(),
        timeout=_HOME_TRENDING_TIMEOUT,
    )


async def _home_top_plays() -> list:
    return await asyncio.wait_for(
        wh_store.get_top_plays(40),
        timeout=_HOME_TOP_PLAYS_TIMEOUT,
    )


def _home_shelf_rank(name: str) -> int:
    normalised = (name or "").strip().lower()
    if normalised == "recommended for you":
        return 0
    if normalised.startswith("because you "):
        return 1
    if normalised == "recently added":
        return 2
    if normalised == "new episodes":
        return 3
    if normalised == "trending":
        return 4
    if normalised == "most played":
        return 5
    if normalised == "music":
        return 6
    if normalised == "series":
        return 7
    if normalised == "recently added movies":
        return 8
    if normalised == "hidden gems":
        return 9
    return 10


def _budget_home_shelves(shelves: list[dict], limit: int | None = None) -> list[dict]:
    if limit is None:
        limit = _home_shelf_limit()
    if limit <= 0:
        return []
    non_empty = [shelf for shelf in shelves if shelf.get("items")]
    ranked = sorted(
        enumerate(non_empty),
        key=lambda item: (_home_shelf_rank(str(item[1].get("name") or "")), item[0]),
    )
    return [shelf for _, shelf in ranked[:limit]]


@routes.get(r"/api/tmdb-image/{size}/{tail:.*}")
async def api_tmdb_image_proxy(request: web.Request) -> web.Response:
    return await tmdb_image_proxy(request)


def _cache_get(key: str) -> str | None:
    entry = _api_response_cache.get(key)
    if entry and entry[1] > time.monotonic():
        return entry[0]
    if entry:
        _api_response_cache.pop(key, None)
    return None


def _cache_set(key: str, text: str) -> None:
    _api_response_cache[key] = (text, time.monotonic() + _API_CACHE_TTL)


def invalidate_api_cache() -> None:
    """Clear cached SPA API payloads after catalogue mutations."""
    global _filter_cache
    _api_response_cache.clear()
    _filter_cache = None


def _base_filters() -> dict:
    global _filter_cache
    now = time.monotonic()
    if _filter_cache and _filter_cache[1] > now:
        return _filter_cache[0]
    filters = {
        "years": media_index.distinct_years(),
        "qualities": media_index.distinct_qualities(),
        "genres": media_index.distinct_genres(),
        "tags": [
            {"name": name, "count": count}
            for name, count in media_index.tag_cloud()
        ],
        "sortOptions": [
            {"value": value, "label": label}
            for value, label in _SORT_OPTIONS
        ],
        "views": [
            {"value": "", "label": "All"},
            {"value": "movies", "label": "Movies"},
            {"value": "series", "label": "Series"},
            {"value": "music", "label": "Music"},
        ],
    }
    _filter_cache = (filters, now + _API_CACHE_TTL)
    return filters


def _landing_cache_key(params: dict) -> str:
    return "hub:landing:" + repr(sorted(params.items()))


def _log_hub_timing(
    started: float,
    *,
    mode: str,
    cache: str,
    user: bool,
    params: dict,
    timings: dict[str, float],
) -> None:
    elapsed_ms = (time.monotonic() - started) * 1000
    if elapsed_ms < _SLOW_HUB_LOG_MS:
        return
    logging.info(
        "spa hub: mode=%s cache=%s user=%s elapsed=%.1fms timings=%s params=%s",
        mode,
        cache,
        user,
        elapsed_ms,
        timings,
        {
            key: params.get(key)
            for key in ("q", "tag", "quality", "genre", "year", "sort", "view", "offset", "limit")
        },
    )


def _duration(seconds: int) -> str:
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _tmdb_image(path: str, size: str = "w342") -> str:
    return tmdb_image_url(path, size)


def _thumb(item: HubItem) -> str:
    suffix = "?v=audio3" if (item.media_kind or "") == "audio" else ""
    return f"/thumb/{item.secure_hash}{item.message_id}.jpg{suffix}"


def _thumb_source_id(card) -> Optional[int]:
    poster = getattr(card, "poster_item", None)
    if poster is not None:
        return thumb_cache.cache_id(
            poster.message_id,
            audio=(poster.media_kind or "") == "audio",
        )
    message_id = getattr(card, "message_id", None)
    if not message_id:
        return None
    return thumb_cache.cache_id(
        message_id,
        audio=(getattr(card, "media_kind", "") or "") == "audio",
    )


def _rating_source_id(card) -> Optional[int]:
    new_episode = getattr(card, "new_episode_item", None)
    if new_episode is not None:
        message_id = getattr(new_episode, "message_id", None)
        return int(message_id) if message_id else None
    poster = getattr(card, "poster_item", None)
    item = poster if poster is not None else card
    if (getattr(item, "media_kind", "") or "") == "audio":
        return None
    message_id = getattr(item, "message_id", None)
    return int(message_id) if message_id else None


async def _rating_counts_for_cards(cards) -> dict[int, dict]:
    ids = []
    seen = set()
    for card in cards:
        message_id = _rating_source_id(card)
        if message_id and message_id not in seen:
            seen.add(message_id)
            ids.append(message_id)
    if not ids:
        return {}
    try:
        return await asyncio.wait_for(ratings_store.get_counts_bulk(ids), timeout=2.0)
    except asyncio.TimeoutError:
        logging.warning("spa hub: rating count aggregation timed out")
        return {}
    except Exception:
        logging.exception("spa hub: rating count aggregation failed")
        return {}


async def _watched_keys_for_user(user: dict | None) -> set[str]:
    if not user:
        return set()
    try:
        history = await asyncio.wait_for(
            wh_store.get_recent(int(user["sub"]), limit=500),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        logging.warning("spa hub: watch history aggregation timed out")
        return set()
    except Exception:
        logging.exception("spa hub: watch history aggregation failed")
        return set()
    watched_keys: set[str] = set()
    for item in history:
        key = str(item.get("cw_key") or "")
        if key:
            watched_keys.add(key)
    return watched_keys


def _watched_movie_keys_for_keys(watched_keys: set[str]) -> set[str]:
    movie_keys: set[str] = set()
    for key in watched_keys:
        match = _CW_KEY_RE.match(key)
        if not match:
            continue
        item = media_index.get_item(int(match.group(1)))
        if item and item.movie_key:
            movie_keys.add(item.movie_key)
    return movie_keys


def _with_rating_counts(payload: dict, card, counts_by_id: dict[int, dict]) -> dict:
    message_id = _rating_source_id(card)
    if not message_id:
        return payload
    counts = counts_by_id.get(message_id)
    if not counts:
        return payload
    up = int(counts.get("up") or 0)
    down = int(counts.get("down") or 0)
    if up + down <= 0:
        return payload
    payload["ratingCounts"] = {"up": up, "down": down}
    return payload


def _is_card_watched(
    card,
    watched_keys: set[str],
    watched_movie_keys: set[str] | None = None,
) -> bool:
    if not watched_keys:
        return False
    if isinstance(card, SeriesGroup):
        return False
    if isinstance(card, MovieGroup):
        return bool(watched_movie_keys and card.movie_key in watched_movie_keys)
    if isinstance(card, AlbumGroup):
        return False
    if isinstance(card, HubItem) and (card.media_kind or "") != "audio":
        return f"{card.secure_hash}{card.message_id}" in watched_keys
    return False


def _prewarm_card_thumbs(cards) -> None:
    message_ids = []
    seen = set()
    for card in cards:
        message_id = _thumb_source_id(card)
        if message_id and message_id not in seen:
            message_ids.append(message_id)
            seen.add(message_id)
    if not message_ids:
        return
    try:
        asyncio.create_task(thumb_cache.prewarm_from_store(message_ids))
    except RuntimeError:
        pass


async def _recover_visible_tmdb_art(cards, timings: dict | None = None) -> int:
    mark = time.monotonic()
    recovered = await media_index.ensure_cards_art_enriched(
        cards,
        limit=_VISIBLE_ART_RECOVERY_LIMIT,
        timeout=_VISIBLE_ART_RECOVERY_TIMEOUT,
    )
    if timings is not None:
        timings["art_recovery_ms"] = round((time.monotonic() - mark) * 1000, 1)
        timings["art_recovered"] = recovered
    return recovered


def _watch_url(item: HubItem) -> str:
    return f"/watch/{item.secure_hash}{item.message_id}"


def _app_watch_url(item: HubItem) -> str:
    return f"/app/watch/{item.secure_hash}{item.message_id}"


def _play_url(item: HubItem) -> str:
    return _app_watch_url(item)


def _safe_next_url(raw: str | None, fallback: str) -> str:
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return fallback
    if "\r" in raw or "\n" in raw:
        return fallback
    return raw


def _ui_redirect(mode: str, next_url: str) -> web.HTTPFound:
    response = web.HTTPFound(next_url)
    response.set_cookie(
        _UI_COOKIE,
        mode,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="Lax",
    )
    return response


@routes.get("/ui/react")
async def use_react_ui(request: web.Request) -> web.Response:
    next_url = _safe_next_url(request.query.get("next"), "/app")
    if not next_url.startswith("/app"):
        next_url = "/app"
    return _ui_redirect("react", next_url)


@routes.get("/ui/classic")
async def use_classic_ui(request: web.Request) -> web.Response:
    next_url = _safe_next_url(request.query.get("next"), "/")
    if next_url.startswith("/app"):
        next_url = "/"
    return _ui_redirect("classic", next_url)


def _detail_url(item: HubItem) -> str:
    if item.series_key:
        return f"/app/series/{item.series_key}"
    if item.movie_key:
        return f"/app/movie/{item.movie_key}"
    if item.album_key:
        return f"/app/album/{item.album_key}"
    return _app_watch_url(item) if (item.media_kind or "") == "audio" else _play_url(item)


def _stream_url(item: HubItem) -> str:
    return f"/{item.secure_hash}{item.message_id}"


def _download_url(item: HubItem) -> str:
    return as_download_url(_stream_url(item))


def _vlc_tracking_token(request: web.Request, item: HubItem) -> str:
    user = get_user(request)
    if not user:
        return ""
    user_id = int(user["sub"])
    token = hmac.new(
        Var.JWT_SECRET.encode(),
        f"{user_id}:{item.message_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{user_id}:{token}"


def _item_common(item: HubItem) -> dict:
    poster = _tmdb_image(item.poster_path) or _thumb(item)
    backdrop = _tmdb_image(item.backdrop_path, "w1280")
    title = _clean_music_tag(item.title or item.file_name or "Untitled")
    artist = _clean_music_tag(item.artist or "")
    return {
        "messageId": item.message_id,
        "secureHash": item.secure_hash,
        "title": title,
        "year": item.year,
        "mediaKind": item.media_kind or "video",
        "posterUrl": poster,
        "thumbUrl": _thumb(item),
        "backdropUrl": backdrop,
        "duration": item.duration or 0,
        "durationLabel": _duration(item.duration or 0),
        "fileSize": item.file_size or 0,
        "fileSizeLabel": humanbytes(item.file_size) if item.file_size else "",
        "quality": item.quality or "",
        "genres": item.tmdb_genres or [],
        "tags": item.tags or [],
        "overview": item.overview or item.description or "",
        "tmdbId": item.tmdb_id,
        "tmdbKind": item.tmdb_kind or "",
        "imdbId": item.imdb_id or "",
        "imdbHref": f"https://www.imdb.com/title/{item.imdb_id}/" if item.imdb_id else "",
        "externalRating": _external_rating(item),
        "artist": artist,
        "albumTitle": _clean_music_tag(item.album_title or ""),
        "trailerKey": item.trailer_key or "",
        "href": _watch_url(item),
        "streamHref": _stream_url(item),
        "downloadHref": _download_url(item),
        "watchKey": f"{item.secure_hash}{item.message_id}",
    }


def _external_rating(item: HubItem) -> dict | None:
    if not (
        getattr(item, "tmdb_id", None)
        and getattr(item, "tmdb_kind", "") in ("movie", "tv")
    ):
        return None
    value = round(float(getattr(item, "tmdb_vote_average", 0) or 0), 1)
    if value <= 0:
        return None
    return {
        "provider": "TMDB",
        "value": value,
        "label": f"{value:.1f}",
        "count": int(getattr(item, "tmdb_vote_count", 0) or 0),
    }


def _episode_external_rating(item: HubItem) -> dict | None:
    if not (
        getattr(item, "tmdb_id", None)
        and getattr(item, "tmdb_kind", "") == "tv"
        and getattr(item, "episode_tmdb_vote_checked_at", 0)
    ):
        return None
    value = round(float(getattr(item, "episode_tmdb_vote_average", 0) or 0), 1)
    if value <= 0:
        return None
    return {
        "provider": "TMDB",
        "value": value,
        "label": f"{value:.1f}",
        "count": int(getattr(item, "episode_tmdb_vote_count", 0) or 0),
    }


def _looks_like_episode_release_title(item: HubItem, title: str) -> bool:
    if not title:
        return False
    normalized = re.sub(r"[\s._-]+", " ", title).strip().lower()
    series = re.sub(r"[\s._-]+", " ", item.series_title or "").strip().lower()
    if series and normalized == series:
        return True
    if item.season is not None and item.episode is not None:
        season = int(item.season)
        episode = int(item.episode)
        patterns = (
            rf"\bs0?{season}\s*e0?{episode}\b",
            rf"\bs0?{season}\s*ep0?{episode}\b",
            rf"\b0?{season}x0?{episode}\b",
            rf"\bseason\s*0?{season}\s*episode\s*0?{episode}\b",
        )
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)
    if item.episode is not None:
        episode = int(item.episode)
        return bool(re.search(rf"\b(?:episode|ep)\s*0?{episode}\b", normalized, re.IGNORECASE))
    return False


def _new_episode_display_title(item: HubItem, label: str) -> str:
    if item.episode_title:
        return item.episode_title
    title = (item.title or item.file_name or "").strip()
    if label and _looks_like_episode_release_title(item, title):
        return ""
    return title


def _video_subtitle(item: HubItem, common: dict) -> str:
    parts = [
        str(item.year) if item.year else "",
        common["durationLabel"],
        item.quality or "",
    ]
    return " - ".join(part for part in parts if part)


def _card_from_item(item: HubItem, art_cache: dict | None = None) -> dict:
    common = _common_with_group_art(
        item,
        media_index.best_group_art_item(item, cache=art_cache),
    )
    is_audio = (item.media_kind or "") == "audio"
    subtitle = common["artist"] if is_audio else _video_subtitle(item, common)
    if item.series_key:
        episode_rating = _episode_external_rating(item)
        if episode_rating:
            common["externalRating"] = episode_rating
    return {
        **common,
        "type": "track" if is_audio else "item",
        "itemId": str(item.message_id),
        "subtitle": subtitle,
        "eyebrow": "Music" if is_audio else (item.quality or "Movie"),
        "badge": item.quality or common["durationLabel"],
        "href": _detail_url(item),
        "playHref": _play_url(item),
        "detailsHref": _detail_url(item),
        "aspect": "square" if is_audio else "poster",
    }


def _common_with_group_art(identity_item: HubItem, art_item: HubItem | None) -> dict:
    common = _item_common(identity_item)
    if (
        art_item is not None
        and art_item is not identity_item
        and getattr(art_item, "poster_path", "")
    ):
        art_common = _item_common(art_item)
        for key in (
            "posterUrl",
            "backdropUrl",
            "genres",
            "overview",
            "tmdbId",
            "tmdbKind",
            "imdbId",
            "imdbHref",
            "externalRating",
            "trailerKey",
        ):
            if art_common.get(key):
                common[key] = art_common[key]
    return common


def _card_from_series(card: SeriesGroup) -> dict:
    poster = card.poster_item
    common = _common_with_group_art(poster, getattr(card, "art_item", None))
    payload = {
        **common,
        "type": "series",
        "itemId": f"series:{card.series_key}",
        "title": card.series_title or common["title"],
        "subtitle": (
            f"{card.episode_count} episode"
            f"{'' if card.episode_count == 1 else 's'}"
            f" - {card.season_count} season"
            f"{'' if card.season_count == 1 else 's'}"
        ),
        "eyebrow": "Series",
        "badge": f"{card.episode_count} ep",
        "href": f"/app/series/{card.series_key}",
        "playHref": _play_url(poster),
        "detailsHref": f"/app/series/{card.series_key}",
        "aspect": "poster",
        "episodeCount": card.episode_count,
        "seasonCount": card.season_count,
    }
    new_episode = getattr(card, "new_episode_item", None)
    if new_episode is not None:
        label = _episode_label(new_episode)
        title = _new_episode_display_title(new_episode, label)
        episode_rating = _episode_external_rating(new_episode)
        payload["playHref"] = _play_url(new_episode)
        payload["watchKey"] = f"{new_episode.secure_hash}{new_episode.message_id}"
        if episode_rating:
            payload["externalRating"] = episode_rating
        else:
            payload.pop("externalRating", None)
        payload["newEpisode"] = {
            "label": label,
            "title": title,
            "playHref": _play_url(new_episode),
            "watchKey": f"{new_episode.secure_hash}{new_episode.message_id}",
        }
    return payload


def _card_from_movie(card: MovieGroup) -> dict:
    poster = card.poster_item
    common = _common_with_group_art(poster, getattr(card, "art_item", None))
    return {
        **common,
        "type": "movie",
        "itemId": f"movie:{card.movie_key}",
        "title": card.title or common["title"],
        "year": card.year,
        "subtitle": (
            f"{card.variant_count} version"
            f"{'' if card.variant_count == 1 else 's'}"
        ),
        "eyebrow": "Movie",
        "badge": f"{card.variant_count} versions",
        "href": f"/app/movie/{card.movie_key}",
        "playHref": _play_url(poster),
        "detailsHref": f"/app/movie/{card.movie_key}",
        "aspect": "poster",
        "variantCount": card.variant_count,
    }


def _card_from_album(card: AlbumGroup) -> dict:
    poster = card.poster_item
    common = _item_common(poster)
    artist = _clean_music_tag(card.artist or "")
    return {
        **common,
        "type": "album",
        "itemId": f"album:{card.album_key}",
        "title": _clean_music_tag(card.album_title or card.artist or "Unknown Album"),
        "subtitle": (
            f"{artist + ' - ' if artist else ''}{card.track_count} track"
            f"{'' if card.track_count == 1 else 's'}"
        ),
        "eyebrow": "Album",
        "badge": f"{card.track_count} track{'s' if card.track_count != 1 else ''}",
        "href": f"/app/album/{card.album_key}",
        "playHref": _app_watch_url(poster),
        "detailsHref": f"/app/album/{card.album_key}",
        "aspect": "square",
        "artist": artist,
        "trackCount": card.track_count,
    }


def _card(card, art_cache: dict | None = None) -> dict:
    if isinstance(card, SeriesGroup):
        return _card_from_series(card)
    if isinstance(card, MovieGroup):
        return _card_from_movie(card)
    if isinstance(card, AlbumGroup):
        return _card_from_album(card)
    return _card_from_item(card, art_cache=art_cache)


def _compact_hub_card_payload(payload: dict) -> dict:
    return {
        key: payload[key]
        for key in _HUB_CARD_PAYLOAD_KEYS
        if key in payload
    }


def _hub_card(
    card,
    rating_counts: dict[int, dict] | None = None,
    watched_keys: set[str] | None = None,
    watched_movie_keys: set[str] | None = None,
    art_cache: dict | None = None,
) -> dict:
    payload = _card(card, art_cache=art_cache)
    if rating_counts:
        payload = _with_rating_counts(payload, card, rating_counts)
    if watched_keys and _is_card_watched(card, watched_keys, watched_movie_keys):
        payload["watched"] = True
    return _compact_hub_card_payload(payload)


def _hero(item: HubItem, art_cache: dict | None = None) -> dict:
    common = _common_with_group_art(
        item,
        media_index.best_group_art_item(item, cache=art_cache),
    )
    details_href = _detail_url(item)
    kind = "Movie"
    title = common["title"]
    if item.series_key:
        kind = "Series"
        title = item.series_title or common["title"]
    elif item.movie_key:
        kind = "Movie"
    elif item.album_key:
        kind = "Album"
        title = _clean_music_tag(item.album_title or common["title"])
    elif item.media_kind == "audio":
        kind = "Music"
    return {
        **common,
        "type": "hero",
        "itemId": str(item.message_id),
        "title": title,
        "detailsHref": details_href,
        "playHref": _play_url(item),
        "eyebrow": kind,
        "meta": [
            str(v) for v in (
                item.year,
                item.quality,
                common["durationLabel"],
            ) if v
        ],
    }


def _parse_hub_params(request: web.Request) -> dict:
    q = (request.query.get("q") or "").strip()
    tag = (request.query.get("tag") or "").strip().lstrip("#").lower()
    quality = (request.query.get("quality") or "").strip()
    genre = (request.query.get("genre") or "").strip()
    year_raw = request.query.get("year") or ""
    try:
        year: Optional[int] = int(year_raw) if year_raw else None
    except ValueError:
        year = None
    sort = (request.query.get("sort") or "newest").strip()
    if sort not in {opt[0] for opt in _SORT_OPTIONS}:
        sort = "newest"
    view = (request.query.get("view") or "").strip().lower()
    if view not in _VALID_VIEWS:
        view = ""
    try:
        offset = max(0, int(request.query.get("offset") or 0))
    except ValueError:
        offset = 0
    try:
        limit = max(12, min(60, int(request.query.get("limit") or 24)))
    except ValueError:
        limit = 24
    return {
        "q": q,
        "tag": tag,
        "quality": quality,
        "genre": genre,
        "year": year,
        "sort": sort,
        "view": view,
        "offset": offset,
        "limit": limit,
    }


def _is_landing(params: dict) -> bool:
    return (
        not params["q"]
        and not params["tag"]
        and not params["year"]
        and not params["quality"]
        and not params["genre"]
        and not params["view"]
        and params["sort"] == "newest"
        and params["offset"] == 0
    )


def _empty_text(params: dict) -> str:
    bits = []
    if params["q"]:
        bits.append(f"matching '{params['q']}'")
    if params["year"]:
        bits.append(f"from {params['year']}")
    if params["quality"]:
        bits.append(params["quality"])
    if params["tag"]:
        bits.append(f"tagged #{params['tag']}")
    if params["genre"]:
        bits.append(f"in {params['genre']}")
    return "No entries " + ", ".join(bits) + "." if bits else "Nothing in the library yet."


def _app_query(params: dict, *, offset: Optional[int] = None) -> str:
    qs = {}
    for key in ("q", "tag", "quality", "genre", "view"):
        if params.get(key):
            qs[key] = params[key]
    if params.get("year"):
        qs["year"] = params["year"]
    if params.get("sort") and params["sort"] != "newest":
        qs["sort"] = params["sort"]
    if offset:
        qs["offset"] = offset
    return "/app" if not qs else f"/app?{urlencode(qs)}"


@routes.get("/api/me")
async def api_me(request: web.Request) -> web.Response:
    user = get_user(request)
    return _json({
        "user": user,
        "botUsername": Var.BOT_USERNAME,
        "app": {
            "name": "TeleDirect",
            "spaPath": "/app",
        },
    })


@routes.get("/api/hub")
async def api_hub(request: web.Request) -> web.Response:
    started = time.monotonic()
    timings: dict[str, float] = {}
    params = _parse_hub_params(request)
    user = get_user(request)
    cache_key = _landing_cache_key(params) if _is_landing(params) and not user else ""
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            _log_hub_timing(
                started,
                mode="shelves",
                cache="hit",
                user=False,
                params=params,
                timings=timings,
            )
            return _json_text(cached)

    mark = time.monotonic()
    base_filters = _base_filters()
    timings["filters_ms"] = round((time.monotonic() - mark) * 1000, 1)

    if _is_landing(params):
        mark = time.monotonic()
        raw_shelves = media_index.shelves()
        hero_items = media_index.pick_heroes()
        timings["shelves_ms"] = round((time.monotonic() - mark) * 1000, 1)
        optional_started = time.monotonic()
        optional_tasks: dict[str, asyncio.Future] = {
            "trending": asyncio.create_task(_home_trending_shelf()),
            "top_plays": asyncio.create_task(_home_top_plays()),
        }
        task_started = {name: optional_started for name in optional_tasks}
        if user:
            optional_tasks["recommendations"] = asyncio.create_task(
                _home_recommendation_shelf(int(user["sub"]))
            )
            task_started["recommendations"] = optional_started

        done, pending = await asyncio.wait(
            optional_tasks.values(),
            timeout=_HOME_OPTIONAL_SHELVES_TIMEOUT,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        timings["optional_shelves_ms"] = round((time.monotonic() - optional_started) * 1000, 1)

        rec_items = []
        personal_shelves = []
        rec_reasons = []
        rec_task = optional_tasks.get("recommendations")
        if rec_task is not None:
            timings["recommendations_ms"] = round(
                (time.monotonic() - task_started["recommendations"]) * 1000,
                1,
            )
            if rec_task in done:
                try:
                    rec_items, personal_shelves, rec_reasons = rec_task.result()
                except asyncio.TimeoutError:
                    logging.warning("spa hub: rec_engine timed out, skipping shelf")
                except Exception:
                    logging.exception("spa hub: rec_engine failed, skipping shelf")
                    rec_items, personal_shelves, rec_reasons = [], [], []
            else:
                logging.warning("spa hub: rec_engine timed out, skipping shelf")

        if rec_items:
            rec_meta = []
            for card in rec_items:
                tid = getattr(card, "tmdb_id", None)
                skey = getattr(card, "series_key", "")
                if tid is None:
                    poster = getattr(card, "poster_item", None)
                    if poster:
                        tid = getattr(poster, "tmdb_id", None)
                        skey = getattr(poster, "series_key", "")
                rec_meta.append(
                    {"tmdbId": tid, "kind": "tv" if skey else "movie"}
                    if tid else None
                )
            raw_shelves = [
                {
                    "name": "Recommended for you",
                    "items": rec_items,
                    "link": None,
                    "total": len(rec_items),
                    "dismissable": True,
                    "rec_meta": rec_meta,
                    "rec_reasons": rec_reasons,
                },
            ] + list(raw_shelves)
        if personal_shelves:
            raw_shelves = list(raw_shelves[:1]) + personal_shelves + list(raw_shelves[1:])

        trend_task = optional_tasks["trending"]
        timings["trending_ms"] = round((time.monotonic() - task_started["trending"]) * 1000, 1)
        if trend_task in done:
            try:
                tr = trend_task.result()
                tr_items = tr.get("in_library", [])
                if len(tr_items) >= trending._MIN_SHELF_ITEMS:
                    raw_shelves = list(raw_shelves) + [
                        {
                            "name": "Trending",
                            "items": tr_items,
                            "link": None,
                            "total": len(tr_items),
                        },
                    ]
            except asyncio.TimeoutError:
                logging.warning("spa hub: trending timed out, skipping shelf")
            except Exception:
                logging.exception("spa hub: trending failed, skipping shelf")
        else:
            logging.warning("spa hub: trending timed out, skipping shelf")

        top_task = optional_tasks["top_plays"]
        timings["top_plays_ms"] = round((time.monotonic() - task_started["top_plays"]) * 1000, 1)
        if top_task in done:
            try:
                top_plays = top_task.result()
                top_cards: list[HubItem] = []
                seen_groups: set[str] = set()
                for entry in top_plays:
                    m = _CW_KEY_RE.match(entry.get("cw_key", ""))
                    if not m:
                        continue
                    item = media_index.get_item(int(m.group(1)))
                    if item is None:
                        continue
                    group_key = item.series_key or item.movie_key or item.album_key or str(item.message_id)
                    if group_key in seen_groups:
                        continue
                    seen_groups.add(group_key)
                    top_cards.append(item)
                    if len(top_cards) >= 20:
                        break
                if len(top_cards) >= 3:
                    raw_shelves = list(raw_shelves) + [
                        {
                            "name": "Most Played",
                            "items": top_cards,
                            "link": None,
                            "total": len(top_cards),
                        },
                    ]
            except asyncio.TimeoutError:
                logging.warning("spa hub: top_plays timed out, skipping shelf")
            except Exception:
                logging.exception("spa hub: top_plays failed, skipping shelf")
        else:
            logging.warning("spa hub: top_plays timed out, skipping shelf")

        raw_shelves = _budget_home_shelves(raw_shelves)

        mark = time.monotonic()
        _prewarm_card_thumbs(
            hero_items
            + [
                item
                for shelf in raw_shelves
                for item in (shelf.get("items") or [])
            ]
        )
        timings["thumb_prewarm_ms"] = round((time.monotonic() - mark) * 1000, 1)

        mark = time.monotonic()
        shelf_source_items = [
            item
            for shelf in raw_shelves
            for item in (shelf.get("items") or [])
        ]
        art_cache = media_index.group_art_cache_for(hero_items + shelf_source_items)
        rating_counts = await _rating_counts_for_cards(shelf_source_items)
        timings["ratings_ms"] = round((time.monotonic() - mark) * 1000, 1)

        mark = time.monotonic()
        watched_keys = await _watched_keys_for_user(user)
        watched_movie_keys = _watched_movie_keys_for_keys(watched_keys)
        timings["watched_ms"] = round((time.monotonic() - mark) * 1000, 1)

        mark = time.monotonic()
        shelves = []
        for shelf in raw_shelves:
            rec_reasons = shelf.get("rec_reasons") or []
            items = []
            for index, item in enumerate(shelf.get("items") or []):
                payload = _hub_card(
                    item,
                    rating_counts,
                    watched_keys,
                    watched_movie_keys,
                    art_cache=art_cache,
                )
                if index < len(rec_reasons) and rec_reasons[index]:
                    payload["recReason"] = rec_reasons[index]
                items.append(payload)
            shelves.append({
                "name": shelf["name"],
                "href": (
                    "/app" + shelf["link"][1:]
                    if shelf.get("link") and shelf["link"].startswith("/?")
                    else shelf.get("link")
                ),
                "total": shelf.get("total", 0),
                "items": items,
                "dismissable": bool(shelf.get("dismissable")),
                "recMeta": shelf.get("rec_meta") or [],
            })
        payload = {
            "mode": "shelves",
            "params": params,
            "filters": base_filters,
            "catalogueSize": media_index.size(),
            "heroes": [_hero(item, art_cache=art_cache) for item in hero_items],
            "shelves": shelves,
            "homeShelfLimit": _home_shelf_limit(),
            "items": [],
            "total": 0,
            "nextOffset": None,
            "nextHref": None,
            "emptyText": _empty_text(params),
        }
        timings["serialize_ms"] = round((time.monotonic() - mark) * 1000, 1)
        if cache_key:
            mark = time.monotonic()
            text = _json_dumps(payload)
            timings["json_ms"] = round((time.monotonic() - mark) * 1000, 1)
            _cache_set(cache_key, text)
            _log_hub_timing(
                started,
                mode="shelves",
                cache="miss",
                user=False,
                params=params,
                timings=timings,
            )
            return _json_text(text)
        _log_hub_timing(
            started,
            mode="shelves",
            cache="bypass",
            user=bool(user),
            params=params,
            timings=timings,
        )
        return _json(payload)

    mark = time.monotonic()
    items, total = media_index.query_grouped(
        q=params["q"],
        year=params["year"],
        quality=params["quality"],
        tag=params["tag"],
        genre=params["genre"],
        sort=params["sort"],
        view=params["view"],
        offset=params["offset"],
        limit=params["limit"],
    )
    timings["query_ms"] = round((time.monotonic() - mark) * 1000, 1)
    next_offset = params["offset"] + params["limit"]
    if next_offset >= total:
        next_offset = None

    if (
        params["offset"] == 0
        and (params["q"] or params["view"] in ("movies", "series"))
    ):
        recovered = await _recover_visible_tmdb_art(items, timings)
        if recovered:
            mark = time.monotonic()
            items, total = media_index.query_grouped(
                q=params["q"],
                year=params["year"],
                quality=params["quality"],
                tag=params["tag"],
                genre=params["genre"],
                sort=params["sort"],
                view=params["view"],
                offset=params["offset"],
                limit=params["limit"],
            )
            timings["query_after_art_recovery_ms"] = round(
                (time.monotonic() - mark) * 1000,
                1,
            )
            next_offset = params["offset"] + params["limit"]
            if next_offset >= total:
                next_offset = None

    mark = time.monotonic()
    _prewarm_card_thumbs(items)
    timings["thumb_prewarm_ms"] = round((time.monotonic() - mark) * 1000, 1)

    mark = time.monotonic()
    rating_counts = await _rating_counts_for_cards(items)
    timings["ratings_ms"] = round((time.monotonic() - mark) * 1000, 1)

    mark = time.monotonic()
    watched_keys = await _watched_keys_for_user(user)
    watched_movie_keys = _watched_movie_keys_for_keys(watched_keys)
    timings["watched_ms"] = round((time.monotonic() - mark) * 1000, 1)

    mark = time.monotonic()
    art_cache = media_index.group_art_cache_for(items)
    payload = {
        "mode": "grid",
        "params": params,
        "filters": base_filters,
        "catalogueSize": media_index.size(),
        "heroes": [],
        "shelves": [],
        "homeShelfLimit": _home_shelf_limit(),
        "items": [
            _hub_card(
                item,
                rating_counts,
                watched_keys,
                watched_movie_keys,
                art_cache=art_cache,
            )
            for item in items
        ],
        "total": total,
        "nextOffset": next_offset,
        "nextHref": _app_query(params, offset=next_offset) if next_offset is not None else None,
        "emptyText": _empty_text(params),
    }
    timings["serialize_ms"] = round((time.monotonic() - mark) * 1000, 1)
    _log_hub_timing(
        started,
        mode="grid",
        cache="bypass",
        user=bool(user),
        params=params,
        timings=timings,
    )
    return _json(payload)


def _person_link(name: str) -> dict:
    return {"name": name, "href": f"/app/person/{media_index._person_slug(name)}"}


def _meta_payload(item: HubItem) -> dict:
    poster = _tmdb_image(item.poster_path) or _thumb(item)
    backdrop = _tmdb_image(item.backdrop_path, "w1280")
    title = _clean_music_tag(item.title or item.file_name or "Untitled")
    return {
        "title": title,
        "year": item.year,
        "overview": item.overview or item.description or "",
        "posterUrl": poster,
        "thumbUrl": _thumb(item),
        "backdropUrl": backdrop,
        "genres": item.tmdb_genres or [],
        "runtimeMinutes": int(item.tmdb_runtime_minutes or 0),
        "certification": item.tmdb_certification or "",
        "logoUrl": _tmdb_image(item.tmdb_logo_path, "w500"),
        "director": item.director or "",
        "directors": [_person_link(name) for name in media_index._director_credits(item.director or "")],
        "cast": [_person_link(name) for name in (item.cast or [])[:12]],
        "imdbId": item.imdb_id or "",
        "imdbHref": f"https://www.imdb.com/title/{item.imdb_id}/" if item.imdb_id else "",
        "externalRating": _external_rating(item),
        "trailerKey": item.trailer_key or "",
    }


def _episode_label(item: HubItem) -> str:
    if item.season is not None and item.episode is not None:
        end = f"-E{item.episode_end:02d}" if item.episode_end else ""
        return f"S{item.season:02d}E{item.episode:02d}{end}"
    if item.episode is not None:
        end = f"-{item.episode_end}" if item.episode_end else ""
        return f"Episode {item.episode}{end}"
    return ""


def _video_choice_payload(item: HubItem, watched_keys: set[str] | None = None) -> dict:
    common = _item_common(item)
    key = f"{item.secure_hash}{item.message_id}"
    label_bits = [item.quality or "", common["durationLabel"]]
    payload = {
        **common,
        "key": key,
        "itemId": str(item.message_id),
        "type": "item",
        "title": item.episode_title or common["title"],
        "subtitle": item.file_name or "",
        "episodeLabel": _episode_label(item),
        "episodeOverview": item.episode_overview or "",
        "episodeStillUrl": _tmdb_image(item.episode_still_path, "w300") or _thumb(item),
        "firstAired": item.episode_air_date or "",
        "label": " - ".join(bit for bit in label_bits if bit),
        "playHref": _play_url(item),
        "appHref": _app_watch_url(item),
        "classicHref": _watch_url(item),
        "href": _play_url(item),
    }
    if watched_keys and key in watched_keys:
        payload["watched"] = True
    return payload


def _episode_navigator_payload(item: HubItem) -> dict | None:
    """Return compact, variant-deduplicated episode choices for the player."""
    if not item.series_key:
        return None
    episodes = media_index.episodes_for_series(item.series_key)
    if len(episodes) < 2:
        return None

    grouped: dict[tuple[object, object, object], list[HubItem]] = {}
    for episode in episodes:
        grouped.setdefault((episode.season, episode.episode, episode.episode_end), []).append(episode)

    seasons: dict[object, list[dict]] = {}
    for (season, episode_number, episode_end), variants in grouped.items():
        # Prefer the uploaded variant already being played; otherwise use the
        # largest available file, matching the series detail-page convention.
        preferred = next((variant for variant in variants if variant.message_id == item.message_id), None)
        if preferred is None:
            preferred = max(variants, key=lambda variant: variant.file_size or 0)
        seasons.setdefault(season, []).append({
            "key": f"{preferred.secure_hash}{preferred.message_id}",
            "title": preferred.episode_title or preferred.title or _episode_label(preferred) or "Episode",
            "label": _episode_label(preferred) or "Episode",
            "posterUrl": _tmdb_image(preferred.episode_still_path, "w300") or _thumb(preferred),
            "durationLabel": _duration(preferred.duration or 0),
            "quality": preferred.quality or "",
            "playHref": _app_watch_url(preferred),
            "current": preferred.message_id == item.message_id,
            "_order": (episode_number is None, episode_number or 0, episode_end or 0),
        })

    season_payload = []
    for season, entries in sorted(seasons.items(), key=lambda pair: (pair[0] is None, pair[0] or 0)):
        entries.sort(key=lambda entry: entry["_order"])
        for entry in entries:
            entry.pop("_order", None)
        season_payload.append({
            "key": "misc" if season is None else str(season),
            "label": "Other episodes" if season is None else f"Season {season}",
            "entries": entries,
        })

    return {
        "title": item.series_title or item.title or "Series",
        "seriesHref": f"/app/series/{item.series_key}",
        "currentSeason": "misc" if item.season is None else str(item.season),
        "seasons": season_payload,
    }


def _cw_progress_pct(entry: dict | None) -> int:
    if not entry:
        return 0
    try:
        pos = float(entry.get("pos") or 0)
        dur = float(entry.get("dur") or 0)
    except (TypeError, ValueError):
        return 0
    if dur <= 0:
        return 0
    pct = pos / dur
    if pct < 0.02 or pct >= 0.95:
        return 0
    return max(1, min(94, round(pct * 100)))


async def _attach_series_playback_state(
    payload: dict,
    user_id: int | None,
    watched_keys: set[str] | None = None,
) -> None:
    if not user_id:
        return
    if watched_keys is None:
        cw_data, history = await asyncio.gather(
            cw_store.get_all(user_id),
            wh_store.get_recent(user_id, limit=500),
        )
        watched_keys = {str(item.get("cw_key") or "") for item in history}
    else:
        cw_data = await cw_store.get_all(user_id)
    for block in payload.get("seasonBlocks") or []:
        for entry in block.get("entries") or []:
            keys = [
                str(variant.get("key") or "")
                for variant in entry.get("variants") or []
                if variant.get("key")
            ]
            entry["watched"] = any(key in watched_keys for key in keys)
            entry["progressPct"] = max((_cw_progress_pct(cw_data.get(key)) for key in keys), default=0)


def _related_rows(
    item: HubItem,
    *,
    limit: int = 14,
    watched_keys: set[str] | None = None,
    watched_movie_keys: set[str] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    art_cache: dict = {}
    exclude = {
        f"movie:{item.movie_key}" if item.movie_key else "",
        f"series:{item.series_key}" if item.series_key else "",
        f"album:{item.album_key}" if item.album_key else "",
        str(item.message_id),
    }
    if item.tmdb_genres:
        cards, _total = media_index.query_grouped(
            genre=item.tmdb_genres[0],
            sort="newest",
            view="music" if item.media_kind == "audio" else "",
            offset=0,
            limit=limit + 6,
        )
        art_cache.update(media_index.group_art_cache_for(cards))
        row_items = []
        for card in cards:
            payload = _hub_card(
                card,
                watched_keys=watched_keys,
                watched_movie_keys=watched_movie_keys,
                art_cache=art_cache,
            )
            if payload.get("itemId") not in exclude:
                row_items.append(payload)
            if len(row_items) >= limit:
                break
        if row_items:
            rows.append({"name": f"More {item.tmdb_genres[0]}", "items": row_items})

    if item.media_kind == "audio" and item.artist:
        artist_slug = media_index._artist_slug(media_index._primary_artist(item.artist))
        artist_tracks = [
            track for track in media_index.tracks_by_artist_slug(artist_slug)
            if track.message_id != item.message_id
        ][:limit]
        if artist_tracks:
            rows.append({
                "name": f"More by {_clean_music_tag(media_index.artist_display_name(artist_slug))}",
                "items": [
                    _hub_card(
                        track,
                        watched_keys=watched_keys,
                        watched_movie_keys=watched_movie_keys,
                        art_cache=art_cache,
                    )
                    for track in artist_tracks
                ],
            })

    if not rows:
        for shelf in media_index.shelves(per_shelf=limit):
            art_cache.update(media_index.group_art_cache_for(shelf.get("items") or []))
            row_items = []
            for card in shelf.get("items") or []:
                payload = _hub_card(
                    card,
                    watched_keys=watched_keys,
                    watched_movie_keys=watched_movie_keys,
                    art_cache=art_cache,
                )
                if payload.get("itemId") not in exclude:
                    row_items.append(payload)
                if len(row_items) >= limit:
                    break
            if row_items:
                rows.append({"name": shelf.get("name") or "More to watch", "items": row_items})
                break
    return rows


def _video_chapters(item: HubItem) -> list[dict]:
    duration = float(item.duration or 0)
    chapters: list[dict] = []
    for chapter in item.chapters or []:
        try:
            start = float(chapter.get("start") or 0)
        except (TypeError, ValueError):
            continue
        if start < 0 or (duration > 0 and start >= duration):
            continue
        title = str(chapter.get("title") or "Chapter").strip() or "Chapter"
        chapters.append({"start": round(start, 2), "title": title[:80]})
    chapters.sort(key=lambda chapter: chapter["start"])
    return chapters


def _movie_detail_payload(
    key: str,
    *,
    watched_keys: set[str] | None = None,
    watched_movie_keys: set[str] | None = None,
) -> dict | None:
    variants = media_index.variants_for_movie(key)
    if not variants:
        return None
    enriched = next((v for v in variants if v.tmdb_id), variants[0])
    preferred = max(variants, key=lambda v: (v.file_size or 0, v.message_id))
    meta = _meta_payload(enriched)
    return {
        "kind": "movie",
        "key": key,
        "savedId": f"movie:{key}",
        "title": meta["title"],
        "year": meta["year"],
        "overview": meta["overview"],
        "posterUrl": meta["posterUrl"],
        "backdropUrl": meta["backdropUrl"],
        "genres": meta["genres"],
        "runtimeMinutes": meta["runtimeMinutes"],
        "certification": meta["certification"],
        "logoUrl": meta["logoUrl"],
        "director": meta["director"],
        "directors": meta["directors"],
        "cast": meta["cast"],
        "imdbHref": meta["imdbHref"],
        "externalRating": meta["externalRating"],
        "trailerKey": meta["trailerKey"],
        "playHref": _play_url(preferred),
        "classicHref": _watch_url(preferred),
        "variants": [_video_choice_payload(v, watched_keys=watched_keys) for v in variants],
        "related": _related_rows(
            enriched,
            watched_keys=watched_keys,
            watched_movie_keys=watched_movie_keys,
        ),
    }


def _series_blocks(episodes: list[HubItem]) -> tuple[list[dict], list[dict], bool, str, int, int]:
    numbered_seasons = sorted({e.season for e in episodes if e.season is not None})
    has_misc = any(e.season is None for e in episodes)
    seasons: dict = {}
    if len(numbered_seasons) == 1:
        seasons[numbered_seasons[0]] = list(episodes)
        has_misc = False
    else:
        for ep in episodes:
            seasons.setdefault(ep.season, []).append(ep)

    season_blocks = []
    for season, eps in sorted(seasons.items(), key=lambda kv: (kv[0] is None, kv[0])):
        by_ep: dict = {}
        extras: list[HubItem] = []
        for ep in eps:
            if ep.episode is None:
                extras.append(ep)
            else:
                by_ep.setdefault((ep.episode, ep.episode_end), []).append(ep)
        entries = []
        for ep_key in sorted(by_ep.keys()):
            variants = sorted(by_ep[ep_key], key=lambda v: -(v.file_size or 0))
            unique: list[HubItem] = []
            seen_hash: set[str] = set()
            duplicate_count = 0
            for variant in variants:
                if variant.secure_hash and variant.secure_hash in seen_hash:
                    duplicate_count += 1
                    continue
                if variant.secure_hash:
                    seen_hash.add(variant.secure_hash)
                unique.append(variant)
            entries.append({
                "rep": _video_choice_payload(unique[0]),
                "variants": [_video_choice_payload(v) for v in unique],
                "duplicateCount": duplicate_count,
                "progressPct": 0,
                "watched": False,
            })
        for ep in extras:
            entries.append({
                "rep": _video_choice_payload(ep),
                "variants": [_video_choice_payload(ep)],
                "duplicateCount": 0,
                "progressPct": 0,
                "watched": False,
            })
        season_blocks.append({"season": season, "entries": entries})

    season_options: list[dict] = [
        {"value": str(season), "label": f"Season {season}"}
        for season in numbered_seasons
    ]
    if has_misc:
        season_options.append({"value": "misc", "label": "Other episodes"})
    show_selector = len(season_options) > 1
    if show_selector:
        season_options.append({"value": "all", "label": "All seasons"})
    total_episode_count = (
        len({(e.season, e.episode) for e in episodes if e.episode is not None})
        or len(episodes)
    )
    season_count = max(1, len(numbered_seasons))
    default_selected = str(numbered_seasons[-1]) if show_selector and numbered_seasons else ("misc" if show_selector else "all")
    return season_blocks, season_options, show_selector, default_selected, total_episode_count, season_count


def _series_detail_payload(
    key: str,
    season_raw: str = "",
    *,
    watched_keys: set[str] | None = None,
    watched_movie_keys: set[str] | None = None,
) -> dict | None:
    episodes = media_index.episodes_for_series(key)
    if not episodes:
        return None
    blocks, options, show_selector, default_selected, total_count, season_count = _series_blocks(episodes)
    valid = {option["value"] for option in options}
    selected = season_raw if season_raw in valid else default_selected
    if selected == "all":
        visible = blocks
    elif selected == "misc":
        visible = [block for block in blocks if block["season"] is None]
    else:
        try:
            selected_int = int(selected)
        except ValueError:
            visible = []
        else:
            visible = [block for block in blocks if block["season"] == selected_int]

    visible_entries = [entry for block in visible for entry in block["entries"]]
    first_entry = visible_entries[0]["rep"] if visible_entries else _video_choice_payload(episodes[0])
    enriched = next((e for e in episodes if e.tmdb_id), episodes[0])
    meta = _meta_payload(enriched)
    return {
        "kind": "series",
        "key": key,
        "savedId": f"series:{key}",
        "title": episodes[0].series_title or key,
        "year": meta["year"],
        "overview": meta["overview"],
        "posterUrl": meta["posterUrl"],
        "backdropUrl": meta["backdropUrl"],
        "genres": meta["genres"],
        "runtimeMinutes": meta["runtimeMinutes"],
        "certification": meta["certification"],
        "logoUrl": meta["logoUrl"],
        "director": meta["director"],
        "directors": meta["directors"],
        "cast": meta["cast"],
        "imdbHref": meta["imdbHref"],
        "externalRating": meta["externalRating"],
        "trailerKey": meta["trailerKey"],
        "playHref": first_entry["playHref"],
        "classicHref": first_entry["classicHref"],
        "seasonOptions": options,
        "showSelector": show_selector,
        "selectedSeason": selected,
        "episodeCount": len(visible_entries) or len(visible),
        "totalEpisodeCount": total_count,
        "seasonCount": season_count,
        "seasonBlocks": visible,
        "related": _related_rows(
            enriched,
            watched_keys=watched_keys,
            watched_movie_keys=watched_movie_keys,
        ),
    }


def _album_detail_payload(
    key: str,
    *,
    watched_keys: set[str] | None = None,
    watched_movie_keys: set[str] | None = None,
) -> dict | None:
    tracks = media_index.tracks_for_album(key)
    if not tracks:
        return None
    rep = (
        next((t for t in tracks if t.poster_path), None)
        or next((t for t in tracks if t.artist and t.has_thumb), None)
        or next((t for t in tracks if t.artist), None)
        or tracks[0]
    )
    unique_artists = {t.artist for t in tracks if t.artist}
    if len(unique_artists) == 1:
        display_artist = unique_artists.pop()
    elif len(unique_artists) > 1:
        display_artist = "Various Artists"
    else:
        display_artist = ""
    artist_credits = [
        {
            "name": _clean_music_tag(credit),
            "href": f"/app/artist/{media_index._artist_slug(credit)}",
        }
        for credit in media_index._artist_credits(display_artist)
    ] if display_artist and display_artist != "Various Artists" else []
    return {
        "kind": "album",
        "key": key,
        "savedId": f"album:{key}",
        "title": _clean_music_tag(rep.album_title or rep.title or key),
        "artist": _clean_music_tag(display_artist),
        # Keep the original field for clients that have not adopted individual
        # credits yet.  It must resolve to a real artist rather than a slug of
        # the entire multi-artist credit string.
        "artistHref": artist_credits[0]["href"] if artist_credits else "",
        "artistCredits": artist_credits,
        "year": rep.year,
        "overview": rep.overview or rep.description or "",
        "posterUrl": _tmdb_image(rep.poster_path) or _thumb(rep),
        "backdropUrl": _tmdb_image(rep.backdrop_path, "w1280") or _thumb(rep),
        "trackCount": len(tracks),
        "playHref": _app_watch_url(tracks[0]),
        "tracks": [_track_payload(track) for track in tracks],
        "related": _related_rows(
            rep,
            watched_keys=watched_keys,
            watched_movie_keys=watched_movie_keys,
        ),
    }


def _artist_detail_payload(slug: str) -> dict | None:
    tracks = media_index.tracks_by_artist_slug(slug)
    if not tracks:
        return None
    albums: dict[str, list[HubItem]] = {}
    singles: list[HubItem] = []
    for track in tracks:
        if track.album_key:
            albums.setdefault(track.album_key, []).append(track)
        else:
            singles.append(track)
    album_cards = []
    for album_key, album_tracks in albums.items():
        group = media_index._build_album_group(album_tracks)
        album_cards.append(_card_from_album(group))
    album_cards.sort(key=lambda card: card.get("title") or "")
    name = _clean_music_tag(media_index.artist_display_name(slug))
    rep = next((track for track in tracks if track.poster_path), None) or tracks[0]
    return {
        "kind": "artist",
        "key": slug,
        "title": name,
        "subtitle": f"{len(tracks)} track{'' if len(tracks) == 1 else 's'}",
        "artist": name,
        "posterUrl": _tmdb_image(rep.poster_path) or _thumb(rep),
        "backdropUrl": _tmdb_image(rep.backdrop_path, "w1280") or _thumb(rep),
        "tracks": [_track_payload(track) for track in tracks],
        "albums": album_cards,
        "singles": [_track_payload(track) for track in singles],
    }


def _person_detail_payload(slug: str) -> dict | None:
    cast_items = media_index.items_by_cast_slug(slug)
    directed_items = media_index.items_by_director_slug(slug)
    if not cast_items and not directed_items:
        return None
    person_name = (
        next((n for it in cast_items for n in (it.cast or [])
              if media_index._person_slug(n) == slug), None)
        or next((n for it in directed_items
                 for n in media_index._director_credits(it.director or "")
                 if media_index._person_slug(n) == slug), None)
        or slug
    )
    if cast_items and directed_items:
        role_label = "Actor & Director"
    elif directed_items:
        role_label = "Director"
    else:
        role_label = "Actor"
    all_items = cast_items + directed_items
    rep = next((item for item in all_items if item.backdrop_path), all_items[0])
    art_cache = media_index.group_art_cache_for(all_items)
    return {
        "kind": "person",
        "key": slug,
        "title": person_name,
        "subtitle": role_label,
        "roleLabel": role_label,
        "totalUnique": len({it.message_id for it in cast_items} | {it.message_id for it in directed_items}),
        "posterUrl": _tmdb_image(rep.poster_path) or _thumb(rep),
        "backdropUrl": _tmdb_image(rep.backdrop_path, "w1280") or _thumb(rep),
        "castItems": [_card_from_item(item, art_cache=art_cache) for item in cast_items],
        "directedItems": [_card_from_item(item, art_cache=art_cache) for item in directed_items],
    }


@routes.get(r"/api/app/movie/{key:[a-z0-9][a-z0-9:\-]*}")
async def api_app_movie(request: web.Request) -> web.Response:
    user = get_user(request)
    watched_keys = await _watched_keys_for_user(user)
    watched_movie_keys = _watched_movie_keys_for_keys(watched_keys)
    variants = media_index.variants_for_movie(request.match_info["key"])
    if variants:
        await media_index.ensure_cards_art_enriched(
            variants[:1],
            limit=1,
            timeout=_VISIBLE_ART_RECOVERY_TIMEOUT,
        )
    payload = _movie_detail_payload(
        request.match_info["key"],
        watched_keys=watched_keys,
        watched_movie_keys=watched_movie_keys,
    )
    if payload is None:
        return _json({"error": "Movie not found"}, status=404)
    return _json(payload)


@routes.get(r"/api/app/series/{key:[a-z0-9][a-z0-9\-]*}")
async def api_app_series(request: web.Request) -> web.Response:
    user = get_user(request)
    watched_keys = await _watched_keys_for_user(user)
    watched_movie_keys = _watched_movie_keys_for_keys(watched_keys)
    episodes = media_index.episodes_for_series(request.match_info["key"])
    if episodes:
        await media_index.ensure_cards_art_enriched(
            episodes[:1],
            limit=1,
            timeout=_VISIBLE_ART_RECOVERY_TIMEOUT,
        )
    payload = _series_detail_payload(
        request.match_info["key"],
        (request.query.get("season") or "").strip().lower(),
        watched_keys=watched_keys,
        watched_movie_keys=watched_movie_keys,
    )
    if payload is None:
        return _json({"error": "Series not found"}, status=404)
    await _attach_series_playback_state(
        payload,
        int(user["sub"]) if user else None,
        watched_keys=watched_keys,
    )
    return _json(payload)


@routes.get(r"/api/app/album/{key:[a-z0-9][a-z0-9\-]*}")
async def api_app_album(request: web.Request) -> web.Response:
    user = get_user(request)
    watched_keys = await _watched_keys_for_user(user)
    watched_movie_keys = _watched_movie_keys_for_keys(watched_keys)
    payload = _album_detail_payload(
        request.match_info["key"],
        watched_keys=watched_keys,
        watched_movie_keys=watched_movie_keys,
    )
    if payload is None:
        return _json({"error": "Album not found"}, status=404)
    return _json(payload)


@routes.get(r"/api/app/artist/{slug:[a-z0-9][a-z0-9\-]*}")
async def api_app_artist(request: web.Request) -> web.Response:
    payload = _artist_detail_payload(request.match_info["slug"])
    if payload is None:
        return _json({"error": "Artist not found"}, status=404)
    return _json(payload)


@routes.get(r"/api/app/person/{slug:[a-z0-9][a-z0-9\-]*}")
async def api_app_person(request: web.Request) -> web.Response:
    payload = _person_detail_payload(request.match_info["slug"])
    if payload is None:
        return _json({"error": "Person not found"}, status=404)
    return _json(payload)


def _parse_watch_key(key: str) -> tuple[str, int] | None:
    match = re.match(r"^([A-Za-z0-9_-]*[A-Za-z_-])(\d+)$", key or "")
    if not match:
        return None
    try:
        return match.group(1), int(match.group(2))
    except ValueError:
        return None


def _audio_format(item: HubItem) -> str:
    codec = (getattr(item, "audio_codec", "") or "").lower()
    if codec in {"flac"}:
        return "FLAC"
    if codec in {"mp3", "mpeg"}:
        return "MP3"
    if codec in {"aac", "m4a", "mp4"}:
        return "AAC"
    if codec in {"opus"}:
        return "Opus"
    if codec in {"vorbis", "ogg"}:
        return "OGG"
    if codec in {"wav", "pcm_s16le", "pcm_s24le", "pcm_s32le"}:
        return "WAV"
    return codec.upper() if codec else ""


def _quality_label(item: HubItem) -> str:
    fmt = _audio_format(item)
    parts = [fmt] if fmt else []
    if item.audio_bit_depth:
        parts.append(f"{item.audio_bit_depth}-bit")
    if item.audio_sample_rate:
        khz = item.audio_sample_rate / 1000
        parts.append(f"{khz:g} kHz")
    if fmt in {"FLAC", "WAV", "AIFF", "ALAC"}:
        parts.append("Lossless")
    return " - ".join(parts)


def _track_payload(item: HubItem) -> dict:
    common = _item_common(item)
    title = _clean_music_tag(item.title or item.file_name or "Untitled")
    artist = _clean_music_tag(item.artist or "")
    album = _clean_music_tag(item.album_title or "")
    return {
        **common,
        "key": f"{item.secure_hash}{item.message_id}",
        "itemId": str(item.message_id),
        "type": "track",
        "title": title,
        "artist": artist,
        "albumTitle": album,
        "trackNumber": item.track_number,
        "format": _audio_format(item),
        "qualityLabel": _quality_label(item),
        "appHref": _app_watch_url(item),
        "classicHref": _watch_url(item),
        "streamHref": _stream_url(item),
        "downloadHref": _download_url(item),
        "albumHref": f"/app/album/{item.album_key}" if item.album_key else "",
    }


def _iso_datetime(value) -> str:
    if not value:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _playlist_limits_payload() -> dict:
    return {
        "maxPlaylists": getattr(playlist_store, "_MAX_PLAYLISTS", 50),
        "maxTracks": getattr(playlist_store, "_MAX_TRACKS", 500),
    }


def _playlist_cover_urls(track_entries: list[dict]) -> list[str]:
    covers: list[str] = []
    seen: set[str] = set()
    for entry in track_entries[:4]:
        try:
            message_id = int(entry.get("message_id"))
        except (TypeError, ValueError):
            continue
        item = media_index.get_item(message_id)
        if item is None:
            continue
        if entry.get("secure_hash") and entry.get("secure_hash") != item.secure_hash:
            continue
        cover = _thumb(item)
        if cover and cover not in seen:
            seen.add(cover)
            covers.append(cover)
    return covers


def _playlist_summary_payload(playlist: dict) -> dict:
    return {
        "playlistId": playlist.get("playlist_id", ""),
        "name": playlist.get("name") or "Untitled",
        "trackCount": int(playlist.get("track_count", len(playlist.get("tracks") or [])) or 0),
        "coverUrls": _playlist_cover_urls(playlist.get("cover_tracks") or playlist.get("tracks") or []),
        "createdAt": _iso_datetime(playlist.get("created_at")),
        "updatedAt": _iso_datetime(playlist.get("updated_at")),
    }


def _playlist_track_payload_from_entry(entry: dict) -> Optional[dict]:
    try:
        message_id = int(entry.get("message_id"))
    except (TypeError, ValueError):
        return None
    item = media_index.get_item(message_id)
    if item is None:
        return None
    if entry.get("secure_hash") and entry.get("secure_hash") != item.secure_hash:
        return None
    if (item.media_kind or "") != "audio":
        return None
    return _track_payload(item)


def _playlist_detail_payload(playlist: dict) -> dict:
    tracks = [
        payload for entry in (playlist.get("tracks") or [])
        if (payload := _playlist_track_payload_from_entry(entry)) is not None
    ]
    summary = _playlist_summary_payload({
        **playlist,
        "track_count": len(tracks),
        "cover_tracks": playlist.get("tracks") or [],
    })
    return {
        **summary,
        "tracks": tracks,
        "available": playlist_store.is_available(),
        **_playlist_limits_payload(),
    }


def _playlist_library_payload(playlists: list[dict]) -> dict:
    return {
        "available": playlist_store.is_available(),
        "playlists": [_playlist_summary_payload(playlist) for playlist in playlists],
        **_playlist_limits_payload(),
    }


async def _playlist_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@routes.get("/api/app/playlists")
async def api_app_playlists(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    playlists = await playlist_store.get_all(int(user["sub"]))
    return _json(_playlist_library_payload(playlists))


@routes.post("/api/app/playlists")
async def api_app_create_playlist(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    if not playlist_store.is_available():
        return _json({"error": "playlist storage unavailable"}, status=503)
    body = await _playlist_body(request)
    name = str(body.get("name") or "").strip()[:100]
    if not name:
        return _json({"error": "name required"}, status=400)
    playlist_id = await playlist_store.create(int(user["sub"]), name)
    if playlist_id is None:
        return _json({"error": "playlist limit reached"}, status=422)
    playlist = await playlist_store.get_one(int(user["sub"]), playlist_id)
    return _json(_playlist_detail_payload(playlist or {
        "playlist_id": playlist_id,
        "name": name,
        "tracks": [],
    }), status=201)


@routes.get(r"/api/app/playlists/{playlist_id:[a-f0-9]{32}}")
async def api_app_playlist_detail(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    playlist_id = request.match_info["playlist_id"]
    playlist = await playlist_store.get_one(int(user["sub"]), playlist_id)
    if playlist is None:
        return _json({"error": "playlist not found"}, status=404)
    return _json(_playlist_detail_payload(playlist))


@routes.patch(r"/api/app/playlists/{playlist_id:[a-f0-9]{32}}")
async def api_app_rename_playlist(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    body = await _playlist_body(request)
    name = str(body.get("name") or "").strip()[:100]
    if not name:
        return _json({"error": "name required"}, status=400)
    playlist_id = request.match_info["playlist_id"]
    ok = await playlist_store.rename(int(user["sub"]), playlist_id, name)
    playlist = await playlist_store.get_one(int(user["sub"]), playlist_id)
    if not ok and playlist is None:
        return _json({"error": "playlist not found"}, status=404)
    return _json(_playlist_detail_payload(playlist) if playlist else {"ok": ok})


@routes.delete(r"/api/app/playlists/{playlist_id:[a-f0-9]{32}}")
async def api_app_delete_playlist(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    await playlist_store.delete(int(user["sub"]), request.match_info["playlist_id"])
    return _json({"ok": True})


@routes.post(r"/api/app/playlists/{playlist_id:[a-f0-9]{32}}/tracks")
async def api_app_add_playlist_track(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    if not playlist_store.is_available():
        return _json({"error": "playlist storage unavailable"}, status=503)
    body = await _playlist_body(request)
    try:
        message_id = int(body.get("messageId", body.get("message_id")))
    except (TypeError, ValueError):
        return _json({"error": "invalid messageId"}, status=400)
    item = media_index.get_item(message_id)
    if item is None:
        return _json({"error": "track not found"}, status=404)
    secure_hash = str(body.get("secureHash", body.get("secure_hash", "")))
    if secure_hash and secure_hash != item.secure_hash:
        return _json({"error": "track changed"}, status=409)
    if (item.media_kind or "") != "audio":
        return _json({"error": "only audio tracks can be added to playlists"}, status=400)
    playlist_id = request.match_info["playlist_id"]
    payload = _track_payload(item)
    ok = await playlist_store.add_track(
        int(user["sub"]),
        playlist_id,
        item.message_id,
        item.secure_hash,
        payload["title"],
        payload["artist"],
    )
    if not ok:
        if not playlist_store.is_available():
            return _json({"error": "playlist storage unavailable"}, status=503)
        playlist = await playlist_store.get_one(int(user["sub"]), playlist_id)
        if playlist is None:
            return _json({"error": "playlist not found"}, status=404)
        return _json({"error": "playlist is full"}, status=422)
    playlist = await playlist_store.get_one(int(user["sub"]), playlist_id)
    return _json(_playlist_detail_payload(playlist) if playlist else {"ok": True})


@routes.delete(r"/api/app/playlists/{playlist_id:[a-f0-9]{32}}/tracks/{message_id:\d+}")
async def api_app_remove_playlist_track(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    await playlist_store.remove_track(
        int(user["sub"]),
        request.match_info["playlist_id"],
        int(request.match_info["message_id"]),
    )
    playlist = await playlist_store.get_one(int(user["sub"]), request.match_info["playlist_id"])
    return _json(_playlist_detail_payload(playlist) if playlist else {"ok": True})


@routes.post(r"/api/app/playlists/{playlist_id:[a-f0-9]{32}}/reorder")
async def api_app_reorder_playlist_tracks(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    body = await _playlist_body(request)
    raw_ids = body.get("messageIds", body.get("message_ids"))
    if not isinstance(raw_ids, list):
        return _json({"error": "messageIds required"}, status=400)
    message_ids: list[int] = []
    for raw_id in raw_ids[:getattr(playlist_store, "_MAX_TRACKS", 500)]:
        try:
            message_ids.append(int(raw_id))
        except (TypeError, ValueError):
            return _json({"error": "invalid messageIds"}, status=400)
    if len(message_ids) != len(set(message_ids)):
        return _json({"error": "duplicate messageIds"}, status=400)
    playlist_id = request.match_info["playlist_id"]
    ok = await playlist_store.reorder_tracks(int(user["sub"]), playlist_id, message_ids)
    if not ok and await playlist_store.get_one(int(user["sub"]), playlist_id) is None:
        return _json({"error": "playlist not found"}, status=404)
    playlist = await playlist_store.get_one(int(user["sub"]), playlist_id)
    return _json(_playlist_detail_payload(playlist) if playlist else {"ok": ok})


def _video_watch_payload(request: web.Request, item: HubItem) -> dict:
    common = _item_common(item)
    title = item.episode_title or common["title"]
    if item.series_title and item.season is not None and item.episode is not None:
        title = f"{item.series_title} {_episode_label(item)}"
        if item.episode_title:
            title = f"{title} - {item.episode_title}"
    absolute_stream = urljoin(Var.URL, f"{item.secure_hash}{item.message_id}")
    vlc_token = _vlc_tracking_token(request, item)
    # Native containers normally stream directly. A completed codec probe can
    # override that when the contents are not browser-decodable (for example
    # an HEVC/AV1 MP4): the HLS route will encode AVC/AAC on demand.
    source_codec = (item.video_codec or "").lower()
    # Keep a compatible HLS rendition available for devices that cannot
    # decode the original upload. The web player tries direct hardware
    # playback first and switches to this source only on a real failure.
    needs_hls_transcode = (
        codec_probe.known_unplayable(item)
        or (bool(source_codec) and source_codec not in {"h264", "avc1"})
    )
    hls_src = (
        f"/hls/{item.secure_hash}{item.message_id}/playlist.m3u8"
        if should_offer_hls_for_video(file_name=item.file_name) or needs_hls_transcode
        else ""
    )
    hls_base = f"/hls/{item.secure_hash}{item.message_id}" if hls_src else ""

    quality_variants: list[HubItem] = []
    if item.movie_key:
        quality_variants = [
            variant for variant in media_index.variants_for_movie(item.movie_key)
            if variant.message_id != item.message_id
        ]
    elif item.series_key and item.episode is not None:
        quality_variants = [
            episode for episode in media_index.episodes_for_series(item.series_key)
            if (
                episode.season == item.season
                and episode.episode == item.episode
                and episode.episode_end == item.episode_end
                and episode.message_id != item.message_id
            )
        ]

    next_ep_raw = media_index.next_episode(item)
    next_ep = None
    if next_ep_raw:
        next_item = None
        next_key = (next_ep_raw.get("url") or "").rsplit("/", 1)[-1]
        parsed_next = _parse_watch_key(next_key)
        if parsed_next:
            next_hash, next_message_id = parsed_next
            candidate = media_index.get_item(next_message_id)
            if candidate is not None and candidate.secure_hash == next_hash:
                next_item = candidate
        if next_item:
            next_ep = {
                **next_ep_raw,
                "key": f"{next_item.secure_hash}{next_item.message_id}",
                "playHref": _play_url(next_item),
                "classicHref": _watch_url(next_item),
                "posterUrl": _tmdb_image(next_item.episode_still_path, "w300") or _thumb(next_item),
            }

    return {
        **common,
        "key": f"{item.secure_hash}{item.message_id}",
        "itemId": str(item.message_id),
        "type": "video",
        "title": title,
        "subtitle": " - ".join(part for part in [
            item.series_title if item.series_key else "",
            _episode_label(item),
            item.quality or "",
        ] if part),
        "episodeLabel": _episode_label(item),
        "classicHref": _watch_url(item),
        "appHref": _app_watch_url(item),
        "directSrc": _stream_url(item),
        "hlsSrc": hls_src,
        "subtitleBase": f"/sub/{item.secure_hash}{item.message_id}",
        "audioTrackBase": hls_base,
        "streamHref": _stream_url(item),
        "absoluteStreamHref": absolute_stream,
        "downloadHref": _download_url(item),
        "vlcHref": f"vlc://{absolute_stream}",
        "vlcTrackingToken": vlc_token,
        # A known-bad source is playable through the transcode HLS fallback;
        # reserve the blocking overlay for the (rare) case with no fallback.
        "knownUnplayable": needs_hls_transcode and not hls_src,
        "preferHls": False,
        "videoCodec": item.video_codec or "",
        "pixFmt": item.pix_fmt or "",
        "qualityVariants": [_video_choice_payload(variant) for variant in quality_variants],
        "episodeNavigator": _episode_navigator_payload(item),
        "nextEpisode": next_ep,
        "introStart": float(item.intro_start or 0),
        "introEnd": float(item.intro_end or 0),
        "recapStart": float(item.recap_start or 0),
        "recapEnd": float(item.recap_end or 0),
        "chapters": _video_chapters(item),
        "duration": item.duration or 0,
        "resumeKey": f"{item.secure_hash}{item.message_id}",
        "metadata": _meta_payload(item),
    }


@routes.get(r"/api/watch/{key:[A-Za-z0-9_-]+}")
async def api_watch(request: web.Request) -> web.Response:
    parsed = _parse_watch_key(request.match_info["key"])
    if not parsed:
        return _json({"error": "Invalid media key"}, status=404)
    secure_hash, message_id = parsed
    item = media_index.get_item(message_id)
    if item is None or item.secure_hash != secure_hash:
        return _json({"error": "Item not found"}, status=404)

    if (item.media_kind or "") != "audio":
        return _json({
            "mediaKind": item.media_kind or "video",
            "classicHref": _watch_url(item),
            "item": _video_watch_payload(request, item),
        })

    tracks = []
    if item.album_key:
        tracks = media_index.tracks_for_album(item.album_key)
    if not tracks:
        tracks = [item]

    current_index = next(
        (idx for idx, track in enumerate(tracks) if track.message_id == item.message_id),
        0,
    )
    prev_track = tracks[current_index - 1] if current_index > 0 else None
    next_track = tracks[current_index + 1] if current_index + 1 < len(tracks) else None

    return _json({
        "mediaKind": "audio",
        "item": _track_payload(item),
        "prev": _track_payload(prev_track) if prev_track else None,
        "next": _track_payload(next_track) if next_track else None,
        "albumTracks": [_track_payload(track) for track in tracks],
    })


def _subtitle_request_item(key: str) -> HubItem | None:
    parsed = _parse_watch_key(key)
    if parsed is None:
        return None
    secure_hash, message_id = parsed
    item = media_index.get_item(message_id)
    if item is None or item.secure_hash != secure_hash or item.hidden or (item.media_kind or "") == "audio":
        return None
    return item


@routes.get(r"/api/app/subtitles/{key:[A-Za-z0-9_-]+}/search")
async def api_user_subtitle_search(request: web.Request) -> web.Response:
    """Search provider-side subtitles without ever disclosing its API key."""
    user = get_user(request)
    if user is None:
        return _json({"error": "Sign in to search subtitles"}, status=401)
    item = _subtitle_request_item(request.match_info["key"])
    if item is None:
        return _json({"error": "Video not found"}, status=404)
    try:
        results = await wyzie_subtitles.search(int(user["sub"]), item, request.query.get("language", ""))
        return _json({"results": results, "configured": True})
    except wyzie_subtitles.WyzieError as exc:
        return _json({"error": str(exc), "configured": wyzie_subtitles.configured()}, status=429 if "limit" in str(exc).lower() or "budget" in str(exc).lower() else 503)


@routes.post(r"/api/app/subtitles/{key:[A-Za-z0-9_-]+}/attach")
async def api_user_subtitle_attach(request: web.Request) -> web.Response:
    """Download one cached provider result for this user's local player only."""
    user = get_user(request)
    if user is None:
        return _json({"error": "Sign in to add subtitles"}, status=401)
    item = _subtitle_request_item(request.match_info["key"])
    if item is None:
        return _json({"error": "Video not found"}, status=404)
    try:
        body = await request.json()
        candidate_id = str(body.get("id") or "") if isinstance(body, dict) else ""
        data, candidate = await wyzie_subtitles.download(int(user["sub"]), item, candidate_id)
        # A user request is deliberately ephemeral: it is not sent to BIN,
        # added to the catalogue, or made visible to anyone else.
        return _json({
            "ok": True,
            "vtt": data.decode("utf-8-sig", errors="replace"),
            "label": str(candidate.get("label") or "Subtitles"),
            "language": str(candidate.get("language") or "und"),
        })
    except wyzie_subtitles.WyzieError as exc:
        return _json({"error": str(exc)}, status=429 if "limit" in str(exc).lower() or "budget" in str(exc).lower() else 503)
    except Exception:
        logging.exception("wyzie: attach failed for video %s", item.message_id)
        return _json({"error": "Could not add selected subtitle"}, status=502)


def _app_watch_share_metadata(key: str) -> dict | None:
    parsed = _parse_watch_key(key)
    if parsed is None:
        return None
    secure_hash, message_id = parsed
    item = media_index.get_item(message_id)
    if item is None or item.hidden or item.secure_hash != secure_hash:
        return None

    if (item.media_kind or "") == "audio":
        parts = [
            _clean_music_tag(item.album_title or ""),
            _clean_music_tag(item.title or item.file_name or ""),
        ]
        title = " · ".join(part for part in parts if part) or "Track"
        share_type = "music.song"
    elif item.series_key:
        base = item.series_title or item.title or "Series"
        episode = _episode_label(item)
        if episode:
            base = f"{base} {episode}"
        episode_title = item.episode_title or item.title or ""
        title = f"{base} · {episode_title}" if episode_title else base
        share_type = "video.episode"
    else:
        title = item.title or item.file_name or "Video"
        share_type = "video.movie" if item.movie_key or item.tmdb_kind == "movie" else "video.other"

    image = share_meta.item_image_url(item)
    if not image:
        image = share_meta.absolute_url(f"thumb/{item.secure_hash}{item.message_id}.jpg")
    return {
        "title": title,
        "description": share_meta.compact_description(
            item.episode_overview,
            item.overview,
            item.description,
            item.file_name,
            fallback=f"Watch {title} on TeleDirect",
        ),
        "image": image,
        "url": share_meta.absolute_url(f"app/watch/{item.secure_hash}{item.message_id}"),
        "type": share_type,
    }


def _app_movie_share_metadata(key: str) -> dict | None:
    variants = media_index.variants_for_movie(key)
    if not variants:
        return None
    enriched = next((item for item in variants if item.tmdb_id), variants[0])
    title = enriched.title or key
    if enriched.year:
        title = f"{title} ({enriched.year})"
    return {
        "title": title,
        "description": share_meta.compact_description(
            enriched.overview,
            enriched.description,
            fallback=f"Watch {title} on TeleDirect",
        ),
        "image": share_meta.item_image_url(enriched),
        "url": share_meta.absolute_url(f"app/movie/{key}"),
        "type": "video.movie",
    }


def _app_series_share_metadata(key: str) -> dict | None:
    episodes = media_index.episodes_for_series(key)
    if not episodes:
        return None
    enriched = next((item for item in episodes if item.tmdb_id), episodes[0])
    title = episodes[0].series_title or enriched.title or key
    season_count = len({episode.season for episode in episodes if episode.season is not None}) or 1
    return {
        "title": title,
        "description": share_meta.compact_description(
            enriched.overview,
            enriched.description,
            fallback=f"{season_count} season{'s' if season_count != 1 else ''} on TeleDirect",
        ),
        "image": share_meta.item_image_url(enriched),
        "url": share_meta.absolute_url(f"app/series/{key}"),
        "type": "video.tv_show",
    }


def _app_share_metadata(path: str) -> dict | None:
    match = re.match(r"^/app/(watch|movie|series)/([^/?#]+)", path or "")
    if not match:
        return None
    kind, key = match.groups()
    if kind == "watch":
        return _app_watch_share_metadata(key)
    if kind == "movie":
        return _app_movie_share_metadata(key)
    if kind == "series":
        return _app_series_share_metadata(key)
    return None


def _meta_attr(value: str) -> str:
    return html_lib.escape(value or "", quote=True)


def _app_share_meta_tags(meta: dict) -> str:
    title = _meta_attr(meta.get("title") or "TeleDirect")
    description = _meta_attr(meta.get("description") or meta.get("title") or "TeleDirect")
    share_type = _meta_attr(meta.get("type") or "website")
    share_url = _meta_attr(meta.get("url") or "")
    share_image = _meta_attr(meta.get("image") or "")
    tags = [
        f'<meta name="description" content="{description}" />',
        '<meta property="og:site_name" content="TeleDirect" />',
        f'<meta property="og:type" content="{share_type}" />',
        f'<meta property="og:title" content="{title}" />',
        f'<meta property="og:description" content="{description}" />',
    ]
    if share_url:
        tags.extend([
            f'<link rel="canonical" href="{share_url}" />',
            f'<meta property="og:url" content="{share_url}" />',
            f'<meta name="twitter:url" content="{share_url}" />',
        ])
    if share_image:
        tags.extend([
            f'<meta property="og:image" content="{share_image}" itemprop="thumbnailUrl" />',
            f'<meta property="og:image:secure_url" content="{share_image}" />',
            '<meta name="twitter:card" content="summary_large_image" />',
            f'<meta name="twitter:image" content="{share_image}" />',
        ])
    else:
        tags.append('<meta name="twitter:card" content="summary" />')
    tags.extend([
        f'<meta name="twitter:title" content="{title}" />',
        f'<meta name="twitter:description" content="{description}" />',
    ])
    return "\n    ".join(tags)


def _inject_app_share_meta(index_html: str, meta: dict) -> str:
    title = _meta_attr(meta.get("title") or "TeleDirect")
    html = re.sub(
        r"\s*<meta\s+name=[\"']description[\"'][^>]*>\s*",
        "\n    ",
        index_html,
        count=1,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"<title>.*?</title>",
        f"<title>{title}</title>",
        html,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    tags = _app_share_meta_tags(meta)
    if "</head>" in html:
        return html.replace("</head>", f"    {tags}\n  </head>", 1)
    return f"{tags}\n{html}"


def _app_index_response(request: web.Request | None = None) -> web.Response:
    if not _APP_INDEX.exists():
        text = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TeleDirect App</title>
    <style>
      body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0c0e;color:#e5e7eb;font:15px system-ui,sans-serif}
      main{max-width:34rem;padding:2rem;border:1px solid rgba(255,255,255,.12);border-radius:12px;background:#15171a}
      code{color:#fdba74}
    </style>
  </head>
  <body>
    <main>
      <h1>SPA build missing</h1>
      <p>Run <code>npm install</code> and <code>npm run build</code> inside <code>frontend/</code>, then reload <code>/app</code>.</p>
    </main>
  </body>
</html>"""
        return web.Response(text=text, content_type="text/html", status=503)
    meta = _app_share_metadata(request.path) if request is not None else None
    if meta:
        try:
            html = _APP_INDEX.read_text(encoding="utf-8")
        except OSError:
            logging.exception("app: failed to read SPA index for share metadata")
        else:
            return web.Response(
                text=_inject_app_share_meta(html, meta),
                content_type="text/html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
    return web.FileResponse(
        _APP_INDEX,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _app_download_redirect(request: web.Request) -> web.HTTPFound | None:
    if not is_download_query(request.rel_url.query):
        return None
    tail = request.match_info.get("tail", "")
    if not tail.startswith("watch/"):
        return None
    key = tail.removeprefix("watch/").split("/", 1)[0]
    if _parse_watch_key(key) is None:
        return None
    query = dict(request.rel_url.query)
    query["download"] = "1"
    return web.HTTPFound(f"/{key}?{urlencode(query)}")


@routes.get("/app")
async def spa_app(request: web.Request) -> web.Response:
    return _app_index_response(request)


@routes.get(r"/app/{tail:.*}")
async def spa_app_fallback(request: web.Request) -> web.Response:
    if not _APP_ROUTE_RE.match(request.path):
        raise web.HTTPNotFound()
    download_redirect = _app_download_redirect(request)
    if download_redirect is not None:
        return download_redirect
    return _app_index_response(request)

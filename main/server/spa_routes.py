"""React SPA routes and JSON API for the media hub.

The SPA is intentionally mounted under /app first.  That keeps the existing
server-rendered UI and raw stream URL catch-all untouched while the React hub
reaches parity route by route.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin

from aiohttp import web

from main.utils import codec_probe
from main.utils import media_index
from main.utils import thumb_cache
from main.utils.codec_probe import _clean_music_tag
from main.utils.hub_query import AlbumGroup, HubItem, MovieGroup, SeriesGroup
from main.utils.human_readable import humanbytes
from main.utils.user_auth import get_user
from main.vars import Var


routes = web.RouteTableDef()

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


@routes.get("/robots.txt")
async def robots_txt(_: web.Request) -> web.Response:
    return web.Response(
        text="User-agent: *\nDisallow: /admin\nDisallow: /api\nAllow: /app\nAllow: /\n",
        content_type="text/plain",
        headers={"Cache-Control": "max-age=86400"},
    )


def _json(data, *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, separators=(",", ":")),
        content_type="application/json",
        status=status,
        headers={"Cache-Control": "no-store"},
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
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else ""


def _thumb(item: HubItem) -> str:
    suffix = "?v=audio2" if (item.media_kind or "") == "audio" else ""
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


def _watch_url(item: HubItem) -> str:
    return f"/watch/{item.secure_hash}{item.message_id}"


def _app_watch_url(item: HubItem) -> str:
    return f"/app/watch/{item.secure_hash}{item.message_id}"


def _play_url(item: HubItem) -> str:
    if (item.media_kind or "") == "audio":
        return _app_watch_url(item)
    return _app_watch_url(item) if Var.REACT_VIDEO_BETA else _watch_url(item)


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
        "artist": artist,
        "albumTitle": _clean_music_tag(item.album_title or ""),
        "href": _watch_url(item),
        "streamHref": _stream_url(item),
        "watchKey": f"{item.secure_hash}{item.message_id}",
    }


def _video_subtitle(item: HubItem, common: dict) -> str:
    parts = [
        str(item.year) if item.year else "",
        common["durationLabel"],
        item.quality or "",
    ]
    return " - ".join(part for part in parts if part)


def _card_from_item(item: HubItem) -> dict:
    common = _item_common(item)
    is_audio = (item.media_kind or "") == "audio"
    subtitle = common["artist"] if is_audio else _video_subtitle(item, common)
    return {
        **common,
        "type": "track" if is_audio else "item",
        "itemId": str(item.message_id),
        "subtitle": subtitle,
        "eyebrow": "Music" if is_audio else (item.quality or "Video"),
        "badge": item.quality or common["durationLabel"],
        "href": _detail_url(item),
        "playHref": _play_url(item),
        "detailsHref": _detail_url(item),
        "aspect": "square" if is_audio else "poster",
    }


def _card_from_series(card: SeriesGroup) -> dict:
    poster = card.poster_item
    common = _item_common(poster)
    return {
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


def _card_from_movie(card: MovieGroup) -> dict:
    poster = card.poster_item
    common = _item_common(poster)
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


def _card(card) -> dict:
    if isinstance(card, SeriesGroup):
        return _card_from_series(card)
    if isinstance(card, MovieGroup):
        return _card_from_movie(card)
    if isinstance(card, AlbumGroup):
        return _card_from_album(card)
    return _card_from_item(card)


def _hero(item: HubItem) -> dict:
    common = _item_common(item)
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
            "reactVideoBeta": Var.REACT_VIDEO_BETA,
        },
    })


@routes.get("/api/hub")
async def api_hub(request: web.Request) -> web.Response:
    params = _parse_hub_params(request)
    base_filters = {
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

    if _is_landing(params):
        raw_shelves = media_index.shelves()
        hero_items = media_index.pick_heroes()
        _prewarm_card_thumbs(
            hero_items
            + [
                item
                for shelf in raw_shelves
                for item in (shelf.get("items") or [])
            ]
        )
        shelves = [
            {
                "name": shelf["name"],
                "href": (
                    "/app" + shelf["link"][1:]
                    if shelf.get("link") and shelf["link"].startswith("/?")
                    else shelf.get("link")
                ),
                "total": shelf.get("total", 0),
                "items": [_card(item) for item in shelf.get("items") or []],
            }
            for shelf in raw_shelves
        ]
        return _json({
            "mode": "shelves",
            "params": params,
            "filters": base_filters,
            "catalogueSize": media_index.size(),
            "heroes": [_hero(item) for item in hero_items],
            "shelves": shelves,
            "items": [],
            "total": 0,
            "nextOffset": None,
            "nextHref": None,
            "emptyText": _empty_text(params),
        })

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
    next_offset = params["offset"] + params["limit"]
    if next_offset >= total:
        next_offset = None

    _prewarm_card_thumbs(items)

    return _json({
        "mode": "grid",
        "params": params,
        "filters": base_filters,
        "catalogueSize": media_index.size(),
        "heroes": [],
        "shelves": [],
        "items": [_card(item) for item in items],
        "total": total,
        "nextOffset": next_offset,
        "nextHref": _app_query(params, offset=next_offset) if next_offset is not None else None,
        "emptyText": _empty_text(params),
    })


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
        "director": item.director or "",
        "directors": [_person_link(name) for name in media_index._director_credits(item.director or "")],
        "cast": [_person_link(name) for name in (item.cast or [])[:12]],
        "imdbId": item.imdb_id or "",
        "imdbHref": f"https://www.imdb.com/title/{item.imdb_id}/" if item.imdb_id else "",
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


def _video_choice_payload(item: HubItem) -> dict:
    common = _item_common(item)
    label_bits = [item.quality or "", common["durationLabel"]]
    return {
        **common,
        "key": f"{item.secure_hash}{item.message_id}",
        "itemId": str(item.message_id),
        "type": "item",
        "title": item.episode_title or common["title"],
        "subtitle": item.file_name or "",
        "episodeLabel": _episode_label(item),
        "episodeOverview": item.episode_overview or "",
        "episodeStillUrl": _tmdb_image(item.episode_still_path, "w300") or _thumb(item),
        "label": " - ".join(bit for bit in label_bits if bit),
        "playHref": _play_url(item),
        "appHref": _app_watch_url(item),
        "classicHref": _watch_url(item),
        "href": _play_url(item),
    }


def _related_rows(item: HubItem, *, limit: int = 14) -> list[dict]:
    rows: list[dict] = []
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
        row_items = []
        for card in cards:
            payload = _card(card)
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
                "items": [_card_from_item(track) for track in artist_tracks],
            })

    if not rows:
        for shelf in media_index.shelves(per_shelf=limit):
            row_items = []
            for card in shelf.get("items") or []:
                payload = _card(card)
                if payload.get("itemId") not in exclude:
                    row_items.append(payload)
                if len(row_items) >= limit:
                    break
            if row_items:
                rows.append({"name": shelf.get("name") or "More to watch", "items": row_items})
                break
    return rows


def _movie_detail_payload(key: str) -> dict | None:
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
        "director": meta["director"],
        "directors": meta["directors"],
        "cast": meta["cast"],
        "imdbHref": meta["imdbHref"],
        "trailerKey": meta["trailerKey"],
        "playHref": _play_url(preferred),
        "classicHref": _watch_url(preferred),
        "variants": [_video_choice_payload(v) for v in variants],
        "related": _related_rows(enriched),
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
            })
        for ep in extras:
            entries.append({
                "rep": _video_choice_payload(ep),
                "variants": [_video_choice_payload(ep)],
                "duplicateCount": 0,
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


def _series_detail_payload(key: str, season_raw: str = "") -> dict | None:
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
        "director": meta["director"],
        "directors": meta["directors"],
        "cast": meta["cast"],
        "imdbHref": meta["imdbHref"],
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
        "related": _related_rows(enriched),
    }


def _album_detail_payload(key: str) -> dict | None:
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
    return {
        "kind": "album",
        "key": key,
        "savedId": f"album:{key}",
        "title": _clean_music_tag(rep.album_title or rep.title or key),
        "artist": _clean_music_tag(display_artist),
        "artistHref": f"/app/artist/{media_index._artist_slug(display_artist)}" if display_artist and display_artist != "Various Artists" else "",
        "year": rep.year,
        "overview": rep.overview or rep.description or "",
        "posterUrl": _tmdb_image(rep.poster_path) or _thumb(rep),
        "backdropUrl": _tmdb_image(rep.backdrop_path, "w1280") or _thumb(rep),
        "trackCount": len(tracks),
        "playHref": _app_watch_url(tracks[0]),
        "tracks": [_track_payload(track) for track in tracks],
        "related": _related_rows(rep),
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
    return {
        "kind": "person",
        "key": slug,
        "title": person_name,
        "subtitle": role_label,
        "roleLabel": role_label,
        "totalUnique": len({it.message_id for it in cast_items} | {it.message_id for it in directed_items}),
        "posterUrl": _tmdb_image(rep.poster_path) or _thumb(rep),
        "backdropUrl": _tmdb_image(rep.backdrop_path, "w1280") or _thumb(rep),
        "castItems": [_card_from_item(item) for item in cast_items],
        "directedItems": [_card_from_item(item) for item in directed_items],
    }


@routes.get(r"/api/app/movie/{key:[a-z0-9][a-z0-9:\-]*}")
async def api_app_movie(request: web.Request) -> web.Response:
    payload = _movie_detail_payload(request.match_info["key"])
    if payload is None:
        return _json({"error": "Movie not found"}, status=404)
    return _json(payload)


@routes.get(r"/api/app/series/{key:[a-z0-9][a-z0-9\-]*}")
async def api_app_series(request: web.Request) -> web.Response:
    payload = _series_detail_payload(
        request.match_info["key"],
        (request.query.get("season") or "").strip().lower(),
    )
    if payload is None:
        return _json({"error": "Series not found"}, status=404)
    return _json(payload)


@routes.get(r"/api/app/album/{key:[a-z0-9][a-z0-9\-]*}")
async def api_app_album(request: web.Request) -> web.Response:
    payload = _album_detail_payload(request.match_info["key"])
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
    }


def _video_watch_payload(item: HubItem) -> dict:
    common = _item_common(item)
    title = item.episode_title or common["title"]
    if item.series_title and item.season is not None and item.episode is not None:
        title = f"{item.series_title} {_episode_label(item)}"
        if item.episode_title:
            title = f"{title} - {item.episode_title}"
    absolute_stream = urljoin(Var.URL, f"{item.secure_hash}{item.message_id}")

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
        "hlsSrc": f"/hls/{item.secure_hash}{item.message_id}/playlist.m3u8",
        "subtitleBase": f"/sub/{item.secure_hash}{item.message_id}",
        "audioTrackBase": f"/hls/{item.secure_hash}{item.message_id}",
        "streamHref": _stream_url(item),
        "absoluteStreamHref": absolute_stream,
        "downloadHref": _stream_url(item),
        "vlcHref": f"vlc://{absolute_stream}",
        "knownUnplayable": codec_probe.known_unplayable(item),
        "videoCodec": item.video_codec or "",
        "pixFmt": item.pix_fmt or "",
        "qualityVariants": [_video_choice_payload(variant) for variant in quality_variants],
        "nextEpisode": next_ep,
        "introStart": float(item.intro_start or 0),
        "introEnd": float(item.intro_end or 0),
        "duration": item.duration or 0,
        "resumeKey": f"{item.secure_hash}{item.message_id}",
        "metadata": _meta_payload(item),
        "reactVideoBeta": Var.REACT_VIDEO_BETA,
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
            "reactVideoBeta": Var.REACT_VIDEO_BETA,
            "classicHref": _watch_url(item),
            "item": _video_watch_payload(item),
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


def _app_index_response() -> web.Response:
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
    return web.FileResponse(
        _APP_INDEX,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@routes.get("/app")
async def spa_app(_request: web.Request) -> web.Response:
    return _app_index_response()


@routes.get(r"/app/{tail:.*}")
async def spa_app_fallback(request: web.Request) -> web.Response:
    if not _APP_ROUTE_RE.match(request.path):
        raise web.HTTPNotFound()
    return _app_index_response()

"""React SPA routes and JSON API for the media hub.

The SPA is intentionally mounted under /app first.  That keeps the existing
server-rendered UI and raw stream URL catch-all untouched while the React hub
reaches parity route by route.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from aiohttp import web

from main.utils import media_index
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
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _tmdb_image(path: str, size: str = "w342") -> str:
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else ""


def _thumb(item: HubItem) -> str:
    return f"/thumb/{item.secure_hash}{item.message_id}.jpg"


def _watch_url(item: HubItem) -> str:
    return f"/watch/{item.secure_hash}{item.message_id}"


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


def _card_from_item(item: HubItem) -> dict:
    common = _item_common(item)
    is_audio = (item.media_kind or "") == "audio"
    subtitle = common["artist"] if is_audio else common["fileSizeLabel"]
    return {
        **common,
        "type": "track" if is_audio else "item",
        "itemId": str(item.message_id),
        "subtitle": subtitle,
        "eyebrow": "Music" if is_audio else (item.quality or "Video"),
        "badge": item.quality or common["durationLabel"],
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
        "href": f"/series/{card.series_key}",
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
        "href": f"/movie/{card.movie_key}",
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
        "badge": f"{card.track_count} tracks",
        "href": f"/album/{card.album_key}",
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
    details_href = common["href"]
    kind = "Movie"
    if item.series_key:
        details_href = f"/series/{item.series_key}"
        kind = "Series"
    elif item.movie_key:
        details_href = f"/movie/{item.movie_key}"
    elif item.album_key:
        details_href = f"/album/{item.album_key}"
        kind = "Album"
    elif item.media_kind == "audio":
        kind = "Music"
    return {
        **common,
        "type": "hero",
        "itemId": str(item.message_id),
        "detailsHref": details_href,
        "playHref": common["href"],
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
            for shelf in media_index.shelves()
        ]
        return _json({
            "mode": "shelves",
            "params": params,
            "filters": base_filters,
            "catalogueSize": media_index.size(),
            "heroes": [_hero(item) for item in media_index.pick_heroes()],
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

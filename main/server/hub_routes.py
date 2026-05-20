"""
Media hub routes.

GET /                  → unified hub: browse + filter (year, quality, tag, sort, search)
GET /tag/{name}        → back-compat shortcut → redirects into / with ?tag=...
GET /thumb/{hash}{id}.jpg → poster image (Telegram-generated video thumb)

HTMX requests (HX-Request: true) receive just the grid fragment so any
filter change (search, year, quality, sort, tag click) can swap content
without a full reload. Non-HTMX requests get the full templated page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import hls, hub_query, media_index, thumb_cache
from main.utils.hub_query import HubItem
from main.utils.human_readable import humanbytes
from main.vars import Var


routes = web.RouteTableDef()


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


_env.filters["humansize"] = lambda b: humanbytes(b) if b else ""
_env.filters["duration"] = lambda s: _fmt_duration(int(s)) if s else ""


SORT_OPTIONS = [
    ("newest",   "Newest first"),
    ("oldest",   "Oldest first"),
    ("title_az", "Title A–Z"),
    ("title_za", "Title Z–A"),
    ("largest",  "Largest first"),
]


def _is_htmx(request: web.Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _html(body: str, push_url: Optional[str] = None) -> web.Response:
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    if push_url is not None:
        headers["HX-Push-Url"] = push_url
    return web.Response(
        text=body,
        content_type="text/html",
        charset="utf-8",
        headers=headers,
    )


def _canonical_url(params: dict, include_offset: bool = False) -> str:
    """Build a clean URL with only the active (non-default) filters set, so
    htmx's hx-push-url doesn't leave empty ?q=&year=&... query strings in
    the address bar when a filter gets cleared.

    ``include_offset`` is True only when rendering Load-More links: page-1
    of the hub is canonically ``/``, not ``/?offset=0``.
    """
    qs = {}
    if params.get("q"):
        qs["q"] = params["q"]
    if params.get("tag"):
        qs["tag"] = params["tag"]
    if params.get("year"):
        qs["year"] = params["year"]
    if params.get("quality"):
        qs["quality"] = params["quality"]
    if params.get("genre"):
        qs["genre"] = params["genre"]
    if params.get("view"):
        qs["view"] = params["view"]
    sort = params.get("sort") or "newest"
    if sort != "newest":
        qs["sort"] = sort
    if include_offset and params.get("offset"):
        qs["offset"] = params["offset"]
    return "/" if not qs else f"/?{urlencode(qs)}"


PAGE_SIZE = 24


def _parse_filters(request: web.Request) -> dict:
    q = (request.query.get("q") or "").strip()
    tag = (request.query.get("tag") or "").strip().lstrip("#").lower()
    quality = (request.query.get("quality") or "").strip()
    genre = (request.query.get("genre") or "").strip()
    year_raw = request.query.get("year") or ""
    try:
        year = int(year_raw) if year_raw else None
    except ValueError:
        year = None
    sort = (request.query.get("sort") or "newest").strip()
    if sort not in {opt[0] for opt in SORT_OPTIONS}:
        sort = "newest"
    try:
        offset = max(0, int(request.query.get("offset") or 0))
    except ValueError:
        offset = 0
    view = (request.query.get("view") or "").strip().lower()
    if view not in {"movies", "series", ""}:
        view = ""
    return dict(
        q=q, tag=tag, quality=quality, genre=genre, year=year, sort=sort,
        offset=offset, view=view,
    )


async def _render_grid(items: List,
                       next_offset: Optional[int],
                       empty_text: str,
                       params: dict) -> str:
    tpl = _env.get_template("_grid.html")
    next_url = None
    if next_offset is not None:
        next_url = _canonical_url({**params, "offset": next_offset}, include_offset=True)
    return await tpl.render_async(
        items=items, next_url=next_url, empty_text=empty_text,
        params=params,
    )


async def _render_shelves(shelves: List[dict], params: dict) -> str:
    tpl = _env.get_template("_shelves.html")
    return await tpl.render_async(shelves=shelves, params=params)


async def _render_page(items: List,
                       next_offset: Optional[int],
                       empty_text: str,
                       params: dict,
                       shelves: Optional[List[dict]] = None,
                       heroes: Optional[List] = None) -> str:
    tpl = _env.get_template("hub.html")
    next_url = None
    if next_offset is not None:
        next_url = _canonical_url({**params, "offset": next_offset}, include_offset=True)
    return await tpl.render_async(
        items=items,
        shelves=shelves,
        heroes=heroes,
        next_url=next_url,
        empty_text=empty_text,
        params=params,
        years=media_index.distinct_years(),
        qualities=media_index.distinct_qualities(),
        genres=media_index.distinct_genres(),
        tag_cloud=media_index.tag_cloud(),
        sort_options=SORT_OPTIONS,
        catalogue_size=media_index.size(),
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
    if params.get("genre"):
        bits.append(f"in {params['genre']}")
    if bits:
        return "No entries " + ", ".join(bits) + "."
    return "Nothing in the library yet — forward a video to the bot."


@routes.get("/")
async def hub_home(request: web.Request) -> web.Response:
    params = _parse_filters(request)

    # Native form submissions (Enter key on the search box) bypass htmx and
    # produce dirty URLs like /?q=foo&year=&quality=&sort=newest because the
    # browser serializes every form input. Redirect full-page requests to
    # the canonical URL so the address bar stays clean. HTMX requests are
    # handled below via the HX-Push-Url header.
    if not _is_htmx(request):
        canonical = _canonical_url(params, include_offset=True)
        if request.rel_url.path_qs != canonical:
            raise web.HTTPFound(canonical)

    # No filters, default sort, page 1 → render the curated shelf view.
    # Anything else falls through to the flat grid + pagination.
    use_shelves = (
        not params["q"]
        and not params["tag"]
        and not params["year"]
        and not params["quality"]
        and not params["view"]
        and params["sort"] == "newest"
        and params["offset"] == 0
    )

    if use_shelves:
        shelves = media_index.shelves()
        heroes = media_index.pick_heroes()
        if _is_htmx(request):
            return _html(
                await _render_shelves(shelves, params),
                push_url=_canonical_url(params, include_offset=True),
            )
        empty = _empty_text(params)
        return _html(
            await _render_page([], None, empty, params,
                               shelves=shelves, heroes=heroes),
        )

    items, total = media_index.query_grouped(
        q=params["q"], year=params["year"], quality=params["quality"],
        tag=params["tag"], genre=params["genre"],
        sort=params["sort"], view=params["view"],
        offset=params["offset"], limit=PAGE_SIZE,
    )
    next_offset = params["offset"] + PAGE_SIZE
    if next_offset >= total:
        next_offset = None
    empty = _empty_text(params)

    if _is_htmx(request):
        return _html(
            await _render_grid(items, next_offset, empty, params),
            push_url=_canonical_url(params, include_offset=True),
        )

    return _html(await _render_page(items, next_offset, empty, params))


@routes.get("/tag/{name}")
async def hub_tag(request: web.Request) -> web.Response:
    """Back-compat shortcut: /tag/foo redirects to /?tag=foo so the unified
    page picks it up with all filters available."""
    name = request.match_info["name"]
    raise web.HTTPFound(f"/?tag={name}")


# Tiny inline SVG favicon. Without this every page logs a 500 because the
# default catch-all /{path:\S+} stream-handler tries to parse "favicon.ico"
# as a BIN message id. Serving an actual icon — even a minimal one — is
# nicer than the 204 we'd otherwise need to swallow the request.
_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    b'<rect width="64" height="64" rx="14" fill="#f97316"/>'
    b'<path d="M22 20l24 12-24 12z" fill="#fff"/>'
    b'</svg>'
)


_MANIFEST_JSON = json.dumps({
    "name": "TeleDirect",
    "short_name": "TeleDirect",
    "description": "Your personal media streaming library",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0f1115",
    "theme_color": "#f97316",
    "icons": [{"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    "categories": ["entertainment"],
}, separators=(",", ":"))

@routes.get("/manifest.json")
async def pwa_manifest(_request: web.Request) -> web.Response:
    return web.Response(
        text=_MANIFEST_JSON,
        content_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@routes.get("/favicon.ico")
@routes.get("/favicon.svg")
async def favicon(_request: web.Request) -> web.Response:
    return web.Response(
        body=_FAVICON_SVG,
        content_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@routes.get("/search/suggest")
async def search_suggest(request: web.Request) -> web.Response:
    """Top-N matches for the nav dropdown.

    Cheap enough to run on every keystroke (debounced client-side):
    ~100us per item even for 1000-item catalogues. Returns a JSON list
    of {title, year, kind, url, poster_path, secure_hash, message_id}.
    """
    q = (request.query.get("q") or "").strip()
    try:
        limit = max(1, min(20, int(request.query.get("limit") or 8)))
    except ValueError:
        limit = 8
    results = media_index.suggest(q, limit=limit) if q else []
    return web.json_response(
        results,
        headers={"Cache-Control": "no-store"},
    )


@routes.get("/api/items")
async def api_items(request: web.Request) -> web.Response:
    """Bulk lookup: ``?keys=<hash><id>,<hash><id>,...`` → JSON array of
    minimal item dicts. Used by the hub's client-side "Continue
    watching" shelf, which reads keys from localStorage and needs the
    matching item metadata to render cards. Capped at 50 keys per call.
    """
    raw = (request.query.get("keys") or "").strip()
    if not raw:
        return web.json_response([], headers={"Cache-Control": "no-store"})
    keys = [k for k in raw.split(",") if k][:50]
    out = []
    for k in keys:
        m = re.match(r"^([a-zA-Z0-9_-]{6})(\d+)$", k)
        if not m:
            continue
        try:
            mid = int(m.group(2))
        except ValueError:
            continue
        item = media_index.get_item(mid)
        if item is None or item.secure_hash != m.group(1):
            continue
        # Prefer a series episode's parent show title for the card label
        # so the Continue-watching shelf reads coherently.
        label = (item.series_title or "") if item.series_key else ""
        ep_label = ""
        if item.series_key and item.season is not None and item.episode is not None:
            ep_label = f"S{item.season:02d}E{item.episode:02d}"
        out.append({
            "key": k,
            "message_id": item.message_id,
            "secure_hash": item.secure_hash,
            "title": item.title,
            "series_title": label,
            "episode_label": ep_label,
            "year": item.year,
            "poster_path": item.poster_path or "",
            "kind": "series" if item.series_key else ("movie" if item.movie_key else ""),
            "watch_url": f"/watch/{item.secure_hash}{item.message_id}",
            "thumb_url": f"/thumb/{item.secure_hash}{item.message_id}.jpg" if item.has_thumb else "",
        })
    return web.json_response(out, headers={"Cache-Control": "no-store"})


@routes.get(r"/movie/{key:[a-z0-9][a-z0-9:\-]*}")
async def hub_movie(request: web.Request) -> web.Response:
    """One movie: list every upload variant so the user picks which to play."""
    key = request.match_info["key"]
    variants = media_index.variants_for_movie(key)
    if not variants:
        raise web.HTTPNotFound(
            text="We couldn't find that movie in the catalogue.",
        )

    # Prefer the enriched variant for the page's TMDB metadata — any one
    # variant works since they all point at the same film.
    enriched = next((v for v in variants if v.tmdb_id), variants[0])
    tpl = _env.get_template("movie.html")
    body = await tpl.render_async(
        title=enriched.title,
        year=enriched.year,
        variant_count=len(variants),
        variants=variants,
        meta=enriched,
    )
    return _html(body)


@routes.get(r"/series/{key:[a-z0-9][a-z0-9\-]*}")
async def hub_series(request: web.Request) -> web.Response:
    """One series: episode list grouped by season."""
    key = request.match_info["key"]
    episodes = media_index.episodes_for_series(key)
    if not episodes:
        raise web.HTTPNotFound(
            text="We couldn't find that series in the catalogue.",
        )

    # Numbered seasons in the catalogue (excludes the None bucket).
    numbered_seasons = sorted({e.season for e in episodes if e.season})

    seasons: dict = {}
    if len(numbered_seasons) == 1:
        # Single-season show: even uploads without SxxEyy in their filename
        # must belong to that one season. Folding them in avoids the
        # confusing "Episodes" bucket sitting alongside "Season N".
        only_season = numbered_seasons[0]
        seasons[only_season] = list(episodes)
    else:
        for ep in episodes:
            seasons.setdefault(ep.season or 0, []).append(ep)

    # Within each season, collapse episodes that share the same episode
    # number into a single card whose ``variants`` list holds every
    # quality/size variant. The watch link points at the largest
    # variant (typically the highest quality); a chip row underneath
    # lets the user pick a smaller one. Items with the same
    # ``secure_hash`` are true duplicates — surfaced as a count so the
    # operator knows to clean them up.
    season_blocks = []
    for s, eps in sorted(seasons.items()):
        by_ep: dict = {}
        extras: list = []
        for e in eps:
            if e.episode is None:
                extras.append(e)
            else:
                by_ep.setdefault(e.episode, []).append(e)
        entries = []
        for ep_num in sorted(by_ep.keys()):
            variants_all = sorted(
                by_ep[ep_num], key=lambda v: -(v.file_size or 0),
            )
            seen_hash: set = set()
            unique_variants: list = []
            duplicates: list = []
            for v in variants_all:
                if v.secure_hash and v.secure_hash in seen_hash:
                    duplicates.append(v)
                else:
                    if v.secure_hash:
                        seen_hash.add(v.secure_hash)
                    unique_variants.append(v)
            entries.append({
                "rep": unique_variants[0],
                "variants": unique_variants,
                "duplicate_count": len(duplicates),
            })
        # Episodes without an episode number get their own entry each
        # (we can't reliably collapse them).
        for e in extras:
            entries.append({
                "rep": e, "variants": [e], "duplicate_count": 0,
            })
        season_blocks.append({"season": s, "entries": entries})

    # First enriched episode (if any) carries the show-level TMDB data.
    enriched = next((e for e in episodes if e.tmdb_id), episodes[0])
    tpl = _env.get_template("series.html")
    body = await tpl.render_async(
        meta=enriched,
        series_title=episodes[0].series_title or key,
        series_key=key,
        season_blocks=season_blocks,
        # Distinct (season, episode) tuples; falls back to the raw row
        # count when no episode is numbered. Surfaces "12 episodes"
        # instead of "37 uploads" when there are heavy variant clusters.
        episode_count=(
            len({(e.season, e.episode) for e in episodes if e.episode is not None})
            or len(episodes)
        ),
        # Don't count the unknown-season bucket as a season; if every
        # episode is unbucketed (no SxxEyy anywhere) fall back to 1.
        season_count=max(1, len(numbered_seasons)),
    )
    return _html(body)


@routes.get(r"/thumb/{hash:[a-zA-Z0-9_-]{6}}{id:\d+}.jpg")
async def hub_thumb(request: web.Request) -> web.Response:
    secure_hash = request.match_info["hash"]
    message_id = int(request.match_info["id"])

    async def fetch() -> Optional[bytes]:
        # Preferred path: use Telegram's own thumbnail. ~30 KB JPEG, no
        # decoding needed, available for most uploads.
        try:
            message = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
        except Exception:
            message = None
        if message is not None:
            media = (
                getattr(message, "video", None)
                or getattr(message, "animation", None)
                or getattr(message, "document", None)
            )
            if media is not None:
                thumbs = getattr(media, "thumbs", None) or []
                if thumbs:
                    try:
                        bytesio = await StreamBot.download_media(
                            thumbs[-1].file_id, in_memory=True,
                        )
                        if bytesio is not None:
                            return (
                                bytesio.getvalue()
                                if hasattr(bytesio, "getvalue") else bytes(bytesio)
                            )
                    except Exception:
                        pass  # fall through to ffmpeg fallback

        # Fallback: grab a frame via ffmpeg. Only attempt if the BIN_CHANNEL
        # message actually exists — if it's missing or empty, ffmpeg would
        # report "End of file" / "error reading header".
        # Reuse the already-fetched `message` rather than a second loopback
        # HEAD request (the stream route only accepts GET, not HEAD).
        if message is None or getattr(message, "empty", True):
            return None
        item = media_index.get_item(message_id)
        if item is None or item.secure_hash != secure_hash:
            return None
        source_url = hls.internal_stream_url(secure_hash, message_id)
        return await hls.grab_thumbnail(source_url, duration=float(item.duration or 0))

    data = await thumb_cache.cached_or_fetch(message_id, fetch)
    if data is None:
        raise web.HTTPNotFound(text="thumb not found")

    return web.Response(
        body=data,
        content_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Length": str(len(data)),
        },
    )

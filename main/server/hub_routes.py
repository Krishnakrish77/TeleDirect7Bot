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
    return dict(
        q=q, tag=tag, quality=quality, year=year, sort=sort, offset=offset,
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
                       shelves: Optional[List[dict]] = None) -> str:
    tpl = _env.get_template("hub.html")
    next_url = None
    if next_offset is not None:
        next_url = _canonical_url({**params, "offset": next_offset}, include_offset=True)
    return await tpl.render_async(
        items=items,
        shelves=shelves,
        next_url=next_url,
        empty_text=empty_text,
        params=params,
        years=media_index.distinct_years(),
        qualities=media_index.distinct_qualities(),
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
        and params["sort"] == "newest"
        and params["offset"] == 0
    )

    if use_shelves:
        shelves = media_index.shelves()
        if _is_htmx(request):
            return _html(
                await _render_shelves(shelves, params),
                push_url=_canonical_url(params, include_offset=True),
            )
        empty = _empty_text(params)
        return _html(await _render_page([], None, empty, params, shelves=shelves))

    items, total = media_index.query_grouped(
        q=params["q"], year=params["year"], quality=params["quality"],
        tag=params["tag"], sort=params["sort"],
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


@routes.get("/favicon.ico")
@routes.get("/favicon.svg")
async def favicon(_request: web.Request) -> web.Response:
    return web.Response(
        body=_FAVICON_SVG,
        content_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@routes.get(r"/movie/{key:[a-z0-9][a-z0-9:\-]*}")
async def hub_movie(request: web.Request) -> web.Response:
    """One movie: list every upload variant so the user picks which to play."""
    key = request.match_info["key"]
    variants = media_index.variants_for_movie(key)
    if not variants:
        raise web.HTTPNotFound(text="movie not found")

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
        raise web.HTTPNotFound(text="series not found")

    # Group episodes by season for the template.
    seasons: dict = {}
    for ep in episodes:
        seasons.setdefault(ep.season or 0, []).append(ep)
    season_blocks = [
        {"season": s, "episodes": eps}
        for s, eps in sorted(seasons.items())
    ]

    # First enriched episode (if any) carries the show-level TMDB data.
    enriched = next((e for e in episodes if e.tmdb_id), episodes[0])
    tpl = _env.get_template("series.html")
    body = await tpl.render_async(
        meta=enriched,
        series_title=episodes[0].series_title or key,
        series_key=key,
        season_blocks=season_blocks,
        episode_count=len(episodes),
        season_count=len(seasons),
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

        # Fallback: grab a frame via ffmpeg. Cheap thanks to input-seek
        # (only the bytes around the chosen timestamp are fetched from the
        # source). thumb_cache holds the result for 6h so we don't redo
        # this work on every page refresh.
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

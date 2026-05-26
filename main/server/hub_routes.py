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

import asyncio
import json
import logging
import re
import struct
import zlib
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import hls, hub_query, media_index, thumb_cache, rec_engine
from main.utils.user_auth import get_user
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
from main.utils.codec_probe import _clean_music_tag as _cmt
_env.filters["clean_music_tag"] = lambda s: _cmt(s) if s else s
_env.globals["bot_username"] = Var.BOT_USERNAME


SORT_OPTIONS = [
    ("newest",   "Newest first"),
    ("oldest",   "Oldest first"),
    ("title_az", "Title A–Z"),
    ("title_za", "Title Z–A"),
    ("largest",  "Largest first"),
]


def _is_htmx(request: web.Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _is_boosted(request: web.Request) -> bool:
    """htmx boost navigation sends HX-Boosted:true; filter/search swaps don't.
    Boost requests need the full page (so hx-select='#main-content' finds it).
    Filter swaps can use the smaller grid/shelves fragment."""
    return request.headers.get("HX-Boosted", "").lower() == "true"


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


# ── Render cache ──────────────────────────────────────────────────────────
# The catalogue is stored in memory and only changes when the bot forwards
# a new file or an admin prunes stale entries. Re-rendering the same Jinja2
# templates on every navigation request wastes ~300-500ms per hit.
# Cache rendered HTML for 30 seconds: fast enough to reflect new uploads
# without stale lag, while eliminating the render cost for repeated views.
import time as _time

_render_cache: dict = {}   # key → (html: str, expires: float)
_CACHE_TTL = 30.0          # seconds


def _cache_get(key: str):
    entry = _render_cache.get(key)
    if entry and entry[1] > _time.monotonic():
        return entry[0]
    return None


def _cache_set(key: str, html: str):
    _render_cache[key] = (html, _time.monotonic() + _CACHE_TTL)


def invalidate_render_cache():
    """Call this after any catalogue mutation (new upload, prune, probe)."""
    _render_cache.clear()


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
    # "list" bypasses the shelf view and shows a flat newest-first grid
    # without filtering by type — used by the "Recently added" See all link.
    if view not in {"movies", "series", "list", "music", ""}:
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
    cache_key = "shelves:" + repr(sorted(params.items()))
    cached = _cache_get(cache_key)
    if cached:
        return cached
    tpl = _env.get_template("_shelves.html")
    html = await tpl.render_async(shelves=shelves, params=params)
    _cache_set(cache_key, html)
    return html


async def _render_page(items: List,
                       next_offset: Optional[int],
                       empty_text: str,
                       params: dict,
                       shelves: Optional[List[dict]] = None,
                       heroes: Optional[List] = None) -> str:
    cache_key = "page:" + repr(sorted(params.items()))
    cached = _cache_get(cache_key)
    if cached:
        return cached
    tpl = _env.get_template("hub.html")
    next_url = None
    if next_offset is not None:
        next_url = _canonical_url({**params, "offset": next_offset}, include_offset=True)
    html = await tpl.render_async(
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
    _cache_set(cache_key, html)
    return html


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

        # Inject personalised recommendations for signed-in users.
        user = get_user(request)
        if user:
            try:
                rec_items = await asyncio.wait_for(
                    rec_engine.get_recommendations(int(user["sub"])),
                    timeout=12.0,
                )
                if rec_items:
                    shelves = [
                        {"name": "Recommended for you", "items": rec_items,
                         "link": None, "total": len(rec_items)},
                    ] + list(shelves)
            except asyncio.TimeoutError:
                logging.warning("hub: rec_engine timed out, skipping shelf")
            except Exception:
                logging.exception("hub: rec_engine failed, skipping shelf")

        if _is_htmx(request) and not _is_boosted(request):
            # Filter/search swap: return the shelves fragment only (small, fast).
            # Boost navigation needs the full page so hx-select="#main-content"
            # can find its target — fall through to _render_page below.
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

    if _is_htmx(request) and not _is_boosted(request):
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
    b'<rect width="64" height="64" rx="14" fill="#7c5cfc"/>'
    b'<path d="M22 20l24 12-24 12z" fill="#fff"/>'
    b'</svg>'
)


def _make_icon_png(size: int) -> bytes:
    """Generate a maskable PNG icon: violet bg (#7c5cfc) + centred white play triangle."""
    bg = (124, 92, 252)   # #7c5cfc
    fg = (255, 255, 255)

    # Play triangle vertices — centred, within the 80 % maskable safe zone
    s = size * 0.28
    cx, cy = size / 2.0, size / 2.0
    tx0, ty0 = cx - s * 0.55, cy - s        # top-left
    tx1, ty1 = cx - s * 0.55, cy + s        # bottom-left
    tx2, ty2 = cx + s * 0.90, cy            # right apex

    def _in_tri(px: float, py: float) -> bool:
        def _s(ax, ay, bx, by):
            return (px - bx) * (ay - by) - (ax - bx) * (py - by)
        d1, d2, d3 = _s(tx0, ty0, tx1, ty1), _s(tx1, ty1, tx2, ty2), _s(tx2, ty2, tx0, ty0)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter byte per row
        for x in range(size):
            rows.extend(fg if _in_tri(x + 0.5, y + 0.5) else bg)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )


# Pre-generate once at startup — cheap (< 50 ms) and never changes.
_ICON_192  = _make_icon_png(192)
_ICON_512  = _make_icon_png(512)
_ICON_180  = _make_icon_png(180)   # apple-touch-icon

_MANIFEST_JSON = json.dumps({
    "name": "TeleDirect",
    "short_name": "TeleDirect",
    "description": "Your personal media streaming library",
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait-primary",
    "background_color": "#0f1115",
    "theme_color": "#7c5cfc",
    "lang": "en",
    "icons": [
        {"src": "/favicon.svg",    "sizes": "any",     "type": "image/svg+xml", "purpose": "any"},
        {"src": "/icon-192.png",   "sizes": "192x192", "type": "image/png",     "purpose": "maskable"},
        {"src": "/icon-512.png",   "sizes": "512x512", "type": "image/png",     "purpose": "maskable"},
    ],
    "categories": ["entertainment"],
}, separators=(",", ":"))


@routes.get("/manifest.json")
async def pwa_manifest(_request: web.Request) -> web.Response:
    return web.Response(
        text=_MANIFEST_JSON,
        content_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@routes.get("/icon-192.png")
async def icon_192(_request: web.Request) -> web.Response:
    return web.Response(body=_ICON_192, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@routes.get("/icon-512.png")
async def icon_512(_request: web.Request) -> web.Response:
    return web.Response(body=_ICON_512, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@routes.get("/apple-touch-icon.png")
async def apple_touch_icon(_request: web.Request) -> web.Response:
    return web.Response(body=_ICON_180, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@routes.get("/favicon.ico")
@routes.get("/favicon.svg")
async def favicon(_request: web.Request) -> web.Response:
    return web.Response(
        body=_FAVICON_SVG,
        content_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


_SW_JS = """\
/* TeleDirect service worker — network-first for navigation,
   cache-first for static assets, network-only for streams/API. */
const CACHE = 'td-v1';
const SHELL = ['/', '/static/tailwind.css', '/favicon.svg', '/manifest.json'];

self.addEventListener('install', e => {
  // allSettled instead of addAll: a single unavailable resource (e.g.
  // the server restarting on Koyeb cold start) no longer aborts the
  // entire SW install, preventing the unstyled-page flash.
  e.waitUntil(
    caches.open(CACHE).then(c =>
      Promise.allSettled(SHELL.map(u =>
        c.add(new Request(u, {cache: 'reload'}))
      ))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Never cache: stream URLs, API, auth, admin, watch pages
  if (
    /^\\/[A-Za-z0-9_-]*[A-Za-z_-]\\d+$/.test(url.pathname) ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/auth/') ||
    url.pathname.startsWith('/admin') ||
    url.pathname.startsWith('/watch/') ||
    url.pathname.startsWith('/hls/')
  ) return;

  // Static assets — cache-first
  if (url.pathname.startsWith('/static/') ||
      url.pathname.match(/\\.(png|svg|ico|webmanifest|json)$/)) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).then(res => {
        if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      }))
    );
    return;
  }

  // Navigation — network-first, cached shell fallback
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match('/').then(r => r || fetch(e.request)))
    );
  }
});
"""


@routes.get("/sw.js")
async def service_worker(_request: web.Request) -> web.Response:
    return web.Response(
        text=_SW_JS,
        content_type="application/javascript",
        # no-cache so updates are picked up within one browser check cycle
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
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
        m = re.match(r"^([A-Za-z0-9_-]*[A-Za-z_-])(\d+)$", k)
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
            # Always return a /thumb/ URL — the endpoint has an ffmpeg
            # fallback for items without a native Telegram thumb. The
            # <img onerror> in the consumer will hide the image if even
            # the fallback fails (truly broken file).
            "thumb_url": f"/thumb/{item.secure_hash}{item.message_id}.jpg",
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
    cache_key = f"movie:{key}"
    body = _cache_get(cache_key)
    if not body:
        tpl = _env.get_template("movie.html")
        body = await tpl.render_async(
            title=enriched.title,
            year=enriched.year,
            variant_count=len(variants),
            variants=variants,
            meta=enriched,
        )
        _cache_set(cache_key, body)
    return _html(body)


@routes.get(r"/series/{key:[a-z0-9][a-z0-9\-]*}")
async def hub_series(request: web.Request) -> web.Response:
    """One series: episode list grouped by season.

    Server-side season pagination: ``?season=<N|misc|all>`` selects which
    bucket to render. Default for multi-season shows is the latest
    numbered season — keeps DOM size + thumb fetches bounded even on
    100+ episode catalogues.
    """
    key = request.match_info["key"]
    episodes = media_index.episodes_for_series(key)
    if not episodes:
        raise web.HTTPNotFound(
            text="We couldn't find that series in the catalogue.",
        )

    # Numbered seasons in the catalogue (excludes the None bucket).
    # Use ``is not None`` rather than truthiness so a Season 0 ("specials"
    # in TVDB convention) is kept distinct from misc/unbucketed episodes.
    numbered_seasons = sorted({e.season for e in episodes if e.season is not None})
    has_misc = any(e.season is None for e in episodes)

    seasons: dict = {}
    if len(numbered_seasons) == 1:
        # Single-season show: even uploads without SxxEyy in their filename
        # must belong to that one season. Folding them in avoids the
        # confusing "Episodes" bucket sitting alongside "Season N".
        only_season = numbered_seasons[0]
        seasons[only_season] = list(episodes)
        has_misc = False
    else:
        # Key by season number (``None`` for misc). Keeping None as the
        # bucket key — rather than coercing to 0 — preserves the
        # distinction between Season 0 specials and unbucketed misc.
        for ep in episodes:
            seasons.setdefault(ep.season, []).append(ep)

    # Within each season, collapse episodes that share the same episode
    # number into a single card whose ``variants`` list holds every
    # quality/size variant. The watch link points at the largest
    # variant (typically the highest quality); a chip row underneath
    # lets the user pick a smaller one. Items with the same
    # ``secure_hash`` are true duplicates — surfaced as a count so the
    # operator knows to clean them up.
    season_blocks = []
    # Sort by season number, putting the None (misc) bucket last so it
    # always renders below the numbered seasons. ``None`` can't be
    # compared to int directly so we sort on a tuple.
    for s, eps in sorted(seasons.items(), key=lambda kv: (kv[0] is None, kv[0])):
        by_ep: dict = {}
        extras: list = []
        for e in eps:
            if e.episode is None:
                extras.append(e)
            else:
                # Key on (episode, episode_end) so a range file E01-E03
                # and a standalone E01 are separate cards, not variants.
                by_ep.setdefault((e.episode, e.episode_end), []).append(e)
        entries = []
        for ep_key in sorted(by_ep.keys()):
            variants_all = sorted(
                by_ep[ep_key], key=lambda v: -(v.file_size or 0),
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

    # ── Season selection (server-side pagination) ─────────────────────
    # Build the dropdown options first so the template has a complete
    # picture regardless of what the user picked. Options are:
    #   - one per numbered season (oldest → newest reads naturally for TV)
    #   - "Other" iff there are unnumbered episodes (multi-season only)
    #   - "All seasons" as the cross-season search escape hatch
    season_options: list = []
    for s in numbered_seasons:
        season_options.append({"value": str(s), "label": f"Season {s}"})
    if has_misc:
        season_options.append({"value": "misc", "label": "Other episodes"})
    show_selector = len(season_options) > 1
    if show_selector:
        season_options.append({"value": "all", "label": "All seasons"})

    # Parse + validate ?season=...
    sel_raw = (request.query.get("season") or "").strip().lower()
    valid_values = {opt["value"] for opt in season_options}
    if sel_raw in valid_values:
        selected = sel_raw
    elif show_selector:
        # Default to the latest numbered season — matches the "what's
        # new" expectation for ongoing shows.
        selected = str(numbered_seasons[-1]) if numbered_seasons else "misc"
    else:
        selected = "all"  # single-season or all-misc: render everything

    # Filter the rendered blocks. ``season_blocks`` is keyed by season
    # number (None for misc/unbucketed).
    if selected == "all":
        visible_blocks = season_blocks
    elif selected == "misc":
        visible_blocks = [b for b in season_blocks if b["season"] is None]
    else:
        try:
            sel_int = int(selected)
        except ValueError:
            visible_blocks = []
        else:
            visible_blocks = [b for b in season_blocks if b["season"] == sel_int]

    # Thumbnails are rendered only for the representative entry, not for
    # each variant (variant chips show quality/size, no image). Scope
    # the L2 hydration to ``rep`` only.
    visible_episodes = [
        entry["rep"].message_id
        for blk in visible_blocks
        for entry in blk["entries"]
    ]

    # Fire-and-forget: bulk-warm L1 thumb cache from Mongo in the background
    # so the browser's parallel /thumb/ requests hit warm L1. Don't block
    # the response — the prewarm is a best-effort optimisation, not required
    # to render the page.
    asyncio.create_task(thumb_cache.prewarm_from_store(visible_episodes))

    # First enriched episode (if any) carries the show-level TMDB data.
    enriched = next((e for e in episodes if e.tmdb_id), episodes[0])
    tpl = _env.get_template("series.html")
    # Total distinct episodes across the whole series (used by the hub card
    # and the "N across M seasons" note when a specific season is selected).
    total_episode_count = (
        len({(e.season, e.episode) for e in episodes if e.episode is not None})
        or len(episodes)
    )

    # Count only what's currently visible so the header matches the page.
    # When "all" is selected this equals total_episode_count.
    visible_entries = [
        entry
        for blk in visible_blocks
        for entry in blk["entries"]
    ]
    visible_episode_count = len(visible_entries) or len(visible_blocks)

    cache_key = f"series:{key}:{selected}"
    body = _cache_get(cache_key)
    if not body:
        body = await tpl.render_async(
            meta=enriched,
            series_title=episodes[0].series_title or key,
            series_key=key,
            season_blocks=visible_blocks,
            season_options=season_options,
            show_selector=show_selector,
            selected_season=selected,
            episode_count=visible_episode_count,
            total_episode_count=total_episode_count,
            season_count=max(1, len(numbered_seasons)),
        )
        _cache_set(cache_key, body)
    return _html(body)


@routes.get(r"/album/{key:[a-z0-9][a-z0-9\-]*}")
async def hub_album(request: web.Request) -> web.Response:
    """One album: track listing."""
    key = request.match_info["key"]
    tracks = media_index.tracks_for_album(key)
    if not tracks:
        raise web.HTTPNotFound(text="Album not found in catalogue.")

    # Fire-and-forget thumb prewarm — don't block the response
    asyncio.create_task(thumb_cache.prewarm_from_store(
        t.message_id for t in tracks
    ))

    # Prefer a track that has both artist metadata AND a thumbnail for the album art.
    # Fall back to first-with-artist, then any track.
    rep = (
        next((t for t in tracks if t.artist and t.has_thumb), None)
        or next((t for t in tracks if t.artist), None)
        or tracks[0]
    )
    # Compute display artist the same way _build_album_group does — show
    # "Various Artists" for multi-artist albums (soundtracks, compilations).
    unique_artists = {t.artist for t in tracks if t.artist}
    if len(unique_artists) == 1:
        display_artist = unique_artists.pop()
    elif len(unique_artists) > 1:
        display_artist = "Various Artists"
    else:
        display_artist = ""
    cache_key = f"album:{key}"
    body = _cache_get(cache_key)
    if not body:
        tpl = _env.get_template("album.html")
        body = await tpl.render_async(
            album_title=rep.album_title or rep.title or key,
            artist=display_artist,
            album_key=key,
            tracks=tracks,
            track_count=len(tracks),
            meta=rep,
        )
        _cache_set(cache_key, body)
    return _html(body)


@routes.get(r"/thumb/{hash:[A-Za-z0-9_-]*[A-Za-z_-]}{id:\d+}.jpg")
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
            audio_msg = getattr(message, "audio", None)
            if audio_msg:
                # Audio: try Telegram's stored thumbnail first (it's the APIC
                # tag art that Telegram already extracted at upload time).
                # Only fall through to ffmpeg if Telegram has nothing stored.
                audio_thumb = getattr(audio_msg, "thumbs", None) or []
                if not audio_thumb:
                    single = getattr(audio_msg, "thumb", None)
                    if single:
                        audio_thumb = [single]
                if audio_thumb:
                    try:
                        bytesio = await StreamBot.download_media(
                            audio_thumb[-1].file_id, in_memory=True,
                        )
                        if bytesio is not None:
                            return (
                                bytesio.getvalue()
                                if hasattr(bytesio, "getvalue") else bytes(bytesio)
                            )
                    except Exception:
                        pass  # fall through to ffmpeg
                media = None  # no Telegram thumb — force ffmpeg path below
            else:
                media = (
                    getattr(message, "video", None)
                    or getattr(message, "animation", None)
                    or getattr(message, "document", None)
                )
            if media is not None:
                thumbs = getattr(media, "thumbs", None) or []
                if not thumbs:
                    single = getattr(media, "thumb", None)
                    if single:
                        thumbs = [single]
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
        # Warm the skeleton cache tail before ffmpeg. A full-file GET bypasses
        # the skeleton cache and streams from Telegram — the connection drops
        # before ffmpeg can find the MOOV atom (for non-faststart MP4).
        if item.file_size and item.file_size > 1024:
            import aiohttp as _aiohttp
            try:
                tail_start = max(0, item.file_size - 1024)
                async with _aiohttp.ClientSession() as _s:
                    async with _s.get(
                        source_url,
                        headers={"Range": f"bytes={tail_start}-"},
                        timeout=_aiohttp.ClientTimeout(total=45),
                    ) as _r:
                        await _r.read()
            except Exception:
                pass
        is_audio = getattr(item, "media_kind", "") == "audio"
        return await hls.grab_thumbnail(
            source_url,
            duration=float(item.duration or 0),
            seek=0.0 if is_audio else 1.0,  # APIC at byte 0; don't Range-seek past it
        )

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

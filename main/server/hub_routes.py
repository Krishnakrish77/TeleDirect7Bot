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

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import hub_query, media_index, thumb_cache
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


def _html(body: str) -> web.Response:
    return web.Response(
        text=body,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


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
    before_raw = request.query.get("before")
    try:
        before_id = int(before_raw) if before_raw else None
    except ValueError:
        before_id = None
    return dict(q=q, tag=tag, quality=quality, year=year, sort=sort, before_id=before_id)


async def _render_grid(items: List[HubItem],
                       next_cursor: Optional[int],
                       empty_text: str,
                       params: dict) -> str:
    tpl = _env.get_template("_grid.html")
    return await tpl.render_async(
        items=items, next_cursor=next_cursor, empty_text=empty_text,
        params=params,
    )


async def _render_page(items: List[HubItem],
                       next_cursor: Optional[int],
                       empty_text: str,
                       params: dict) -> str:
    tpl = _env.get_template("hub.html")
    return await tpl.render_async(
        items=items,
        next_cursor=next_cursor,
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
    items, next_cursor = await hub_query.query(
        q=params["q"], year=params["year"], quality=params["quality"],
        tag=params["tag"], sort=params["sort"], before_id=params["before_id"],
    )
    empty = _empty_text(params)

    if _is_htmx(request):
        return _html(await _render_grid(items, next_cursor, empty, params))

    return _html(await _render_page(items, next_cursor, empty, params))


@routes.get("/tag/{name}")
async def hub_tag(request: web.Request) -> web.Response:
    """Back-compat shortcut: /tag/foo redirects to /?tag=foo so the unified
    page picks it up with all filters available."""
    name = request.match_info["name"]
    raise web.HTTPFound(f"/?tag={name}")


@routes.get(r"/thumb/{hash:[a-zA-Z0-9_-]{6}}{id:\d+}.jpg")
async def hub_thumb(request: web.Request) -> web.Response:
    message_id = int(request.match_info["id"])

    async def fetch() -> Optional[bytes]:
        try:
            message = await StreamBot.get_messages(Var.BIN_CHANNEL, message_id)
        except Exception:
            return None
        media = (
            getattr(message, "video", None)
            or getattr(message, "animation", None)
            or getattr(message, "document", None)
        )
        if media is None:
            return None
        thumbs = getattr(media, "thumbs", None) or []
        if not thumbs:
            return None
        bytesio = await StreamBot.download_media(thumbs[-1].file_id, in_memory=True)
        if bytesio is None:
            return None
        return bytesio.getvalue() if hasattr(bytesio, "getvalue") else bytes(bytesio)

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

"""
Media hub routes.

GET /                  → hub home (browse grid)
GET /?before=<msg_id>  → next page; partial when called via HTMX
GET /search?q=...      → search results
GET /tag/{name}        → tag-filtered results
GET /thumb/{hash}{id}.jpg → poster image (Telegram-generated video thumb)

HTMX requests (HX-Request: true) receive just the grid fragment so the
search box and "Load more" button can swap content without a full reload.
Non-HTMX requests get the full templated page.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main import StreamBot
from main.utils import hub_query, thumb_cache
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
_env.filters["humansize"] = lambda b: humanbytes(b) if b else ""
_env.filters["duration"] = lambda s: _fmt_duration(int(s)) if s else ""


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _is_htmx(request: web.Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _html(body: str) -> web.Response:
    # No cache on hub HTML: the index grows in real time as new files are
    # forwarded, and a stale cached empty page is the worst first impression
    # we can give a returning user. Browsers will still see fast subsequent
    # loads via gzip + the in-process query cache.
    return web.Response(
        text=body,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def _render_grid(request: web.Request, items: List[HubItem],
                       next_cursor: Optional[int] = None,
                       empty_text: str = "Nothing in the library yet.") -> str:
    tpl = _env.get_template("_grid.html")
    return await tpl.render_async(items=items, next_cursor=next_cursor, empty_text=empty_text)


async def _render_page(request: web.Request, **ctx) -> str:
    tpl = _env.get_template("hub.html")
    return await tpl.render_async(**ctx)


@routes.get("/")
async def hub_home(request: web.Request) -> web.Response:
    before_param = request.query.get("before")
    try:
        before_id = int(before_param) if before_param else None
    except ValueError:
        before_id = None

    items, next_cursor = await hub_query.browse(before_id=before_id)

    if _is_htmx(request):
        body = await _render_grid(request, items, next_cursor)
        return _html(body)

    body = await _render_page(
        request,
        section="browse",
        query="",
        items=items,
        next_cursor=next_cursor,
        empty_text="Nothing in the library yet — forward a video to the bot.",
    )
    return web.Response(text=body, content_type="text/html")


@routes.get("/search")
async def hub_search(request: web.Request) -> web.Response:
    query = (request.query.get("q") or "").strip()

    # Empty query: behave like browse. Otherwise an HTMX search-fire on an
    # empty input (autofill / clear) would blank the grid by swapping in
    # nothing.
    if not query:
        items, next_cursor = await hub_query.browse()
        if _is_htmx(request):
            body = await _render_grid(request, items, next_cursor)
            return _html(body)
        body = await _render_page(
            request,
            section="browse",
            query="",
            items=items,
            next_cursor=next_cursor,
            empty_text="Nothing in the library yet — forward a video to the bot.",
        )
        return _html(body)

    items = await hub_query.search(query)
    if _is_htmx(request):
        body = await _render_grid(request, items, None, empty_text="No matches.")
        return _html(body)

    body = await _render_page(
        request,
        section="search",
        query=query,
        items=items,
        next_cursor=None,
        empty_text="No matches.",
    )
    return _html(body)


@routes.get("/tag/{name}")
async def hub_tag(request: web.Request) -> web.Response:
    tag = request.match_info["name"]
    items = await hub_query.by_tag(tag)

    if _is_htmx(request):
        body = await _render_grid(request, items, None,
                                  empty_text=f"Nothing tagged #{tag}.")
        return _html(body)

    body = await _render_page(
        request,
        section="tag",
        query=f"#{tag}",
        items=items,
        next_cursor=None,
        empty_text=f"No entries tagged #{tag}.",
    )
    return web.Response(text=body, content_type="text/html")


@routes.get(r"/thumb/{hash:[a-zA-Z0-9_-]{6}}{id:\d+}.jpg")
async def hub_thumb(request: web.Request) -> web.Response:
    secure_hash = request.match_info["hash"]
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

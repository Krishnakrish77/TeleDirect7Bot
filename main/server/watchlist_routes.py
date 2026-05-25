"""Watchlist API + page routes.

GET  /watchlist              — HTML page (requires login; redirects to / if not)
GET  /api/watchlist          — JSON list of item_ids for the logged-in user
POST /api/watchlist/{iid}    — add item; returns {"saved": true}
DELETE /api/watchlist/{iid}  — remove item; returns {"saved": false}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.utils.user_auth import decode_token
from main.utils import watchlist_store
from main.utils import media_index

routes = web.RouteTableDef()

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)


def _get_user(request: web.Request) -> Optional[dict]:
    """Extract and verify JWT from Authorization header OR td_session cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = decode_token(auth[7:])
        if user:
            return user
    cookie = request.cookies.get("td_session", "")
    if cookie:
        return decode_token(cookie)
    return None


def _resolve_item(item_id: str) -> Optional[dict]:
    """Turn a stored item_id into a dict suitable for the watchlist page."""
    try:
        if item_id.startswith("movie:"):
            key = item_id[6:]
            variants = media_index.variants_for_movie(key)
            if not variants:
                return None
            p = variants[0]
            return {
                "item_id": item_id,
                "url": f"/movie/{key}",
                "title": p.title or "",
                "year": p.year,
                "poster": (f"https://image.tmdb.org/t/p/w342{p.poster_path}"
                           if p.poster_path
                           else f"/thumb/{p.secure_hash}{p.message_id}.jpg"),
                "kind": "movie",
                "subtitle": f"{len(variants)} version{'s' if len(variants) != 1 else ''}",
            }
        if item_id.startswith("series:"):
            key = item_id[7:]
            eps = media_index.episodes_for_series(key)
            if not eps:
                return None
            p = eps[0]
            title = p.series_title or p.title or ""
            return {
                "item_id": item_id,
                "url": f"/series/{key}",
                "title": title,
                "year": p.year,
                "poster": (f"https://image.tmdb.org/t/p/w342{p.poster_path}"
                           if p.poster_path
                           else f"/thumb/{p.secure_hash}{p.message_id}.jpg"),
                "kind": "series",
                "subtitle": f"{len(eps)} episode{'s' if len(eps) != 1 else ''}",
            }
        if item_id.startswith("album:"):
            key = item_id[6:]
            tracks = media_index.tracks_for_album(key)
            if not tracks:
                return None
            p = tracks[0]
            return {
                "item_id": item_id,
                "url": f"/album/{key}",
                "title": p.album_title or p.artist or "Unknown Album",
                "year": p.year,
                "poster": f"/thumb/{p.secure_hash}{p.message_id}.jpg",
                "kind": "album",
                "subtitle": f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}",
            }
        # Individual item
        item = media_index.get_item(int(item_id))
        if item is None:
            return None
        return {
            "item_id": item_id,
            "url": f"/watch/{item.secure_hash}{item.message_id}",
            "title": item.title or item.file_name or "",
            "year": item.year,
            "poster": (f"https://image.tmdb.org/t/p/w342{item.poster_path}"
                       if item.poster_path
                       else f"/thumb/{item.secure_hash}{item.message_id}.jpg"),
            "kind": item.media_kind or "video",
            "subtitle": item.artist if item.media_kind == "audio" else "",
        }
    except Exception:
        logging.exception("watchlist: resolve_item failed for %s", item_id)
        return None


# ── Page ─────────────────────────────────────────────────────────────────────

@routes.get("/watchlist")
async def watchlist_page(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        raise web.HTTPFound("/")

    ids = await watchlist_store.get_ids(user["sub"])
    items = [r for iid in ids if (r := _resolve_item(iid)) is not None]

    tpl = _env.get_template("watchlist.html")
    body = await tpl.render_async(
        user=user,
        items=items,
        mongo_available=watchlist_store.is_available(),
    )
    return web.Response(text=body, content_type="text/html")


# ── API ──────────────────────────────────────────────────────────────────────

def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
    )


@routes.get("/api/watchlist")
async def api_get(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    ids = await watchlist_store.get_ids(user["sub"])
    return _json({"ids": ids})


@routes.post("/api/watchlist/{iid}")
async def api_add(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    iid = request.match_info["iid"]
    await watchlist_store.add(user["sub"], iid)
    return _json({"saved": True, "item_id": iid})


@routes.delete("/api/watchlist/{iid}")
async def api_remove(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    iid = request.match_info["iid"]
    await watchlist_store.remove(user["sub"], iid)
    return _json({"saved": False, "item_id": iid})

"""Watchlist API + page routes.

GET  /watchlist              — HTML page (requires login; redirects to / if not)
GET  /api/watchlist          — JSON list of item_ids for the logged-in user
POST /api/watchlist/{iid}    — add item; returns {"saved": true}
DELETE /api/watchlist/{iid}  — remove item; returns {"saved": false}
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.server.tmdb_images import tmdb_image_url
from main.utils.user_auth import get_user
from main.utils import watchlist_store, cw_store, rec_store
from main.utils import media_index
from main.vars import Var

routes = web.RouteTableDef()
_CW_KEY_RE = re.compile(r'^[A-Za-z0-9_-]*[A-Za-z_-](\d+)$')

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)
_env.globals["bot_username"] = Var.BOT_USERNAME

# Allowlist: plain message_id (digits only) or prefixed group key (slug chars)
_VALID_IID = re.compile(r'^(?:(?:movie|series|album):[a-z0-9_-]{1,120}|\d{1,15})$')


_get_user = get_user  # shared auth helper


def _thumb_url(item) -> str:
    suffix = "?v=audio3" if getattr(item, "media_kind", "") == "audio" else ""
    return f"/thumb/{item.secure_hash}{item.message_id}.jpg{suffix}"


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
                "poster": (tmdb_image_url(p.poster_path, "w342")
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
                "poster": (tmdb_image_url(p.poster_path, "w342")
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
                "poster": _thumb_url(p),
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
            "poster": (tmdb_image_url(item.poster_path, "w342")
                       if item.poster_path
                       else _thumb_url(item)),
            "kind": item.media_kind or "video",
            "subtitle": item.artist if item.media_kind == "audio" else "",
        }
    except Exception:
        logging.exception("watchlist: resolve_item failed for %s", item_id)
        return None


async def _items_for_user(user_id: int) -> list[dict]:
    ids = await watchlist_store.get_ids(user_id)
    items = [r for iid in ids if (r := _resolve_item(iid)) is not None]

    # Attach watch progress for individual items (cw_key = secure_hash + message_id).
    # Build a message_id → progress-fraction dict from CW data.
    cw_data = await cw_store.get_all(user_id)
    _cw_by_mid: dict = {}
    for ck, entry in cw_data.items():
        m = _CW_KEY_RE.match(ck)
        if m and entry.get("dur", 0) > 0:
            pct = min(1.0, entry["pos"] / entry["dur"])
            if 0.02 < pct < 0.95:   # only show meaningful progress
                _cw_by_mid[m.group(1)] = pct

    for it in items:
        it["cw_pct"] = _cw_by_mid.get(it["item_id"]) if it["item_id"].isdigit() else None
    return items


# ── Page ─────────────────────────────────────────────────────────────────────

@routes.get("/watchlist")
async def watchlist_page(request: web.Request) -> web.Response:
    if request.cookies.get("td_ui") == "react" and request.headers.get("HX-Request") != "true":
        raise web.HTTPFound("/app/watchlist")

    user = _get_user(request)
    if not user:
        raise web.HTTPFound("/")

    items = await _items_for_user(int(user["sub"]))
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
    ids = await watchlist_store.get_ids(int(user["sub"]))
    return _json({"ids": ids})


@routes.get("/api/app/watchlist")
async def api_app_get(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    items = await _items_for_user(int(user["sub"]))
    return _json({
        "items": items,
        "mongoAvailable": watchlist_store.is_available(),
    })


@routes.get("/api/app/liked-songs")
async def api_app_liked_songs(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    # Pre-filter IDs to audio kinds before resolving, avoiding O(N) resolution
    # of non-audio items that would be discarded anyway.
    all_ids = await watchlist_store.get_ids(int(user["sub"]))
    audio_ids = [iid for iid in all_ids if iid.startswith("album:") or iid.isdigit()]
    resolved = []
    for iid in audio_ids:
        item = _resolve_item(iid)
        if item and item.get("kind") in ("audio", "album"):
            resolved.append(item)
    # Attach CW progress only for individual audio items.
    if any(it["item_id"].isdigit() for it in resolved):
        cw_data = await cw_store.get_all(int(user["sub"]))
        cw_by_mid: dict = {}
        for ck, entry in cw_data.items():
            m = _CW_KEY_RE.match(ck)
            if m and entry.get("dur", 0) > 0:
                pct = min(1.0, entry["pos"] / entry["dur"])
                if 0.02 < pct < 0.95:
                    cw_by_mid[m.group(1)] = pct
        for it in resolved:
            it["cw_pct"] = cw_by_mid.get(it["item_id"]) if it["item_id"].isdigit() else None
    return _json({
        "items": resolved,
        "mongoAvailable": watchlist_store.is_available(),
    })


@routes.post("/api/watchlist/{iid}")
async def api_add(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    iid = request.match_info["iid"]
    if not _VALID_IID.match(iid):
        return _json({"error": "invalid item_id"}, status=400)
    await watchlist_store.add(int(user["sub"]), iid)
    await rec_store.clear_cached(int(user["sub"]))
    return _json({"saved": True, "item_id": iid})


@routes.delete("/api/watchlist/{iid}")
async def api_remove(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    iid = request.match_info["iid"]
    if not _VALID_IID.match(iid):
        return _json({"error": "invalid item_id"}, status=400)
    await watchlist_store.remove(int(user["sub"]), iid)
    await rec_store.clear_cached(int(user["sub"]))
    return _json({"saved": False, "item_id": iid})

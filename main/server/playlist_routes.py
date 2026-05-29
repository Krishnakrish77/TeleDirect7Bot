"""Playlist API + page routes.

GET    /playlists                        — list page (HTML)
GET    /playlist/{id}                    — detail page (HTML)
GET    /api/playlists                    — JSON list (for Alpine picker)
POST   /api/playlists                    — create  {name}
DELETE /api/playlists/{id}               — delete
PATCH  /api/playlists/{id}               — rename  {name}
POST   /api/playlists/{id}/tracks        — add track {message_id, secure_hash, title, artist}
DELETE /api/playlists/{id}/tracks/{mid}  — remove track
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from main.utils.user_auth import get_user
from main.utils import playlist_store, media_index
from main.vars import Var

routes = web.RouteTableDef()

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    enable_async=True,
)
_env.globals["Var"] = Var

from main.utils.codec_probe import _clean_music_tag as _cmt
_env.filters["clean_music_tag"] = lambda s: _cmt(s) if s else s

def _fmt_dur(s: int) -> str:
    if not s:
        return ""
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
_env.filters["duration"] = _fmt_dur

_VALID_ID = re.compile(r"^[a-f0-9]{32}$")   # UUID hex
_VALID_MID = re.compile(r"^\d{1,15}$")


def _json(data, *, status: int = 200) -> web.Response:
    import json
    return web.Response(text=json.dumps(data), content_type="application/json", status=status)


# ── HTML pages ────────────────────────────────────────────────────────────────

@routes.get("/playlists")
async def playlists_page(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        raise web.HTTPFound("/")
    playlists = await playlist_store.get_all(int(user["sub"]))
    tpl = _env.get_template("playlists.html")
    body = await tpl.render_async(
        user=user,
        playlists=playlists,
        available=playlist_store.is_available(),
    )
    return web.Response(text=body, content_type="text/html")


@routes.get(r"/playlist/{id:[a-f0-9]{32}}")
async def playlist_detail(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        raise web.HTTPFound("/")
    playlist_id = request.match_info["id"]
    pl = await playlist_store.get_one(int(user["sub"]), playlist_id)
    if pl is None:
        raise web.HTTPNotFound(reason="Playlist not found")

    # Enrich tracks with fresh metadata from the catalogue.
    # Skip tracks whose message_ids are no longer indexed.
    enriched = []
    for t in pl.get("tracks") or []:
        item = media_index.get_item(t["message_id"])
        if item is None:
            continue
        # Verify the stored secure_hash still matches the catalogue entry so a
        # DB compromise or migration error can't redirect a track to a different
        # file (different unique_id = different stream URL).
        if t.get("secure_hash") and t["secure_hash"] != item.secure_hash:
            continue
        enriched.append({
            "message_id": item.message_id,
            "secure_hash": item.secure_hash,
            "title": item.title or t.get("title") or item.file_name or "",
            "artist": item.artist or t.get("artist") or "",
            "duration": item.duration,
            "album_title": item.album_title or "",
            "watch_url": f"/watch/{item.secure_hash}{item.message_id}",
            "stream_url": f"{Var.URL}/{item.secure_hash}{item.message_id}",
            "art": f"/thumb/{item.secure_hash}{item.message_id}.jpg",
        })

    tpl = _env.get_template("playlist.html")
    body = await tpl.render_async(
        user=user,
        playlist=pl,
        tracks=enriched,
        available=playlist_store.is_available(),
    )
    return web.Response(text=body, content_type="text/html")


# ── JSON API ─────────────────────────────────────────────────────────────────

@routes.get("/api/playlists")
async def api_list(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    playlists = await playlist_store.get_all(int(user["sub"]))
    return _json(playlists)


@routes.post("/api/playlists")
async def api_create(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()[:100]
    except Exception:
        return _json({"error": "invalid body"}, status=400)
    if not name:
        return _json({"error": "name required"}, status=400)
    pid = await playlist_store.create(int(user["sub"]), name)
    if pid is None:
        return _json({"error": "playlist limit reached or storage unavailable"}, status=422)
    return _json({"playlist_id": pid, "name": name}, status=201)


@routes.delete(r"/api/playlists/{id:[a-f0-9]{32}}")
async def api_delete(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    await playlist_store.delete(int(user["sub"]), request.match_info["id"])
    return _json({"ok": True})


@routes.patch(r"/api/playlists/{id:[a-f0-9]{32}}")
async def api_rename(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()[:100]
    except Exception:
        return _json({"error": "invalid body"}, status=400)
    if not name:
        return _json({"error": "name required"}, status=400)
    ok = await playlist_store.rename(int(user["sub"]), request.match_info["id"], name)
    return _json({"ok": ok})


@routes.post(r"/api/playlists/{id:[a-f0-9]{32}}/tracks")
async def api_add_track(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
        message_id = int(body["message_id"])
        secure_hash = str(body.get("secure_hash", ""))[:20]
        title = str(body.get("title", ""))[:200]
        artist = str(body.get("artist", ""))[:200]
    except (KeyError, ValueError, TypeError):
        return _json({"error": "invalid body"}, status=400)
    pid = request.match_info["id"]
    ok = await playlist_store.add_track(
        int(user["sub"]), pid,
        message_id, secure_hash, title, artist,
    )
    if not ok:
        # Check whether the playlist exists to give a specific error message.
        pl = await playlist_store.get_one(int(user["sub"]), pid)
        if pl is None:
            return _json({"error": "playlist not found"}, status=404)
        return _json({"error": "playlist is full (500 track limit)"}, status=422)
    return _json({"ok": True})


@routes.delete(r"/api/playlists/{id:[a-f0-9]{32}}/tracks/{mid:\d+}")
async def api_remove_track(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    await playlist_store.remove_track(
        int(user["sub"]),
        request.match_info["id"],
        int(request.match_info["mid"]),
    )
    return _json({"ok": True})

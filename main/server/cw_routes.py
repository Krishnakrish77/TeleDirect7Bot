"""Continue-Watching sync API.

GET    /api/cw          — fetch all entries for the signed-in user
POST   /api/cw/{key}    — upsert one entry  {pos, dur, t, title}
DELETE /api/cw/{key}    — remove one entry
DELETE /api/cw          — clear all entries
"""

from __future__ import annotations

import json
import re

from aiohttp import web

from main.utils.user_auth import decode_token
from main.utils import cw_store

routes = web.RouteTableDef()

# {hash}{message_id} — e.g. AgADAx1234567 or a longer 15-char hash variant
_VALID_KEY = re.compile(r'^[A-Za-z0-9_-]{3,50}$')


def _get_user(request: web.Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = decode_token(auth[7:])
        if user:
            return user
    cookie = request.cookies.get("td_session", "")
    if cookie:
        return decode_token(cookie)
    return None


def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
    )


@routes.get("/api/cw")
async def api_get_all(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    data = await cw_store.get_all(user["sub"])
    return _json(data)


@routes.post("/api/cw/{key}")
async def api_upsert(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    key = request.match_info["key"]
    if not _VALID_KEY.match(key):
        return _json({"error": "invalid key"}, status=400)
    try:
        body = await request.json()
        pos = float(body.get("pos", 0))
        dur = float(body.get("dur", 0))
        t = int(body.get("t", 0))
        title = str(body.get("title", ""))[:200]
    except Exception:
        return _json({"error": "invalid body"}, status=400)
    if dur <= 0:
        return _json({"ok": True})
    await cw_store.upsert(user["sub"], key, pos, dur, t, title)
    return _json({"ok": True})


@routes.delete("/api/cw/{key}")
async def api_delete_one(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    key = request.match_info["key"]
    if not _VALID_KEY.match(key):
        return _json({"error": "invalid key"}, status=400)
    await cw_store.delete_one(user["sub"], key)
    return _json({"ok": True})


@routes.delete("/api/cw")
async def api_delete_all(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    await cw_store.delete_all(user["sub"])
    return _json({"ok": True})

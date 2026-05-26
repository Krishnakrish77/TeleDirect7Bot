"""Watch-history API.

POST /api/wh/{key}  — record a completed watch (called when video hits 95%)
"""

from __future__ import annotations

import json
import re

from aiohttp import web

from main.utils.user_auth import decode_token
from main.utils import wh_store

routes = web.RouteTableDef()

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


@routes.post("/api/wh/{key}")
async def api_record(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return web.Response(text='{"ok":false}', content_type="application/json", status=401)
    key = request.match_info["key"]
    if not _VALID_KEY.match(key):
        return web.Response(text='{"ok":false}', content_type="application/json", status=400)
    try:
        body = await request.json()
        title = str(body.get("title", ""))[:200]
    except Exception:
        title = ""
    await wh_store.record(int(user["sub"]), key, title)
    return web.Response(text='{"ok":true}', content_type="application/json")

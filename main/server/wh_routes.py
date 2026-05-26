"""Watch-history API.

POST /api/wh/{key}  — record a completed watch (called when video hits 95%)
"""

from __future__ import annotations

import json
import re

from aiohttp import web

from main.utils.user_auth import get_user
from main.utils import wh_store

routes = web.RouteTableDef()

_VALID_KEY = re.compile(r'^[A-Za-z0-9_-]{3,50}$')


def _json(data: dict, *, status: int = 200) -> web.Response:
    import json as _json
    return web.Response(text=_json.dumps(data), content_type="application/json", status=status)


@routes.post("/api/wh/{key}")
async def api_record(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    key = request.match_info["key"]
    if not _VALID_KEY.match(key):
        return _json({"error": "invalid key"}, status=400)
    try:
        body = await request.json()
        title = str(body.get("title", ""))[:200]
    except Exception:
        return _json({"error": "invalid body"}, status=400)
    await wh_store.record(int(user["sub"]), key, title)
    return _json({"ok": True})

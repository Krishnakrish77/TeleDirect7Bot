"""Watch-history API.

POST /api/wh/{key}  — record a completed watch (called when video hits 95%)
"""

from __future__ import annotations

import json
import re

from aiohttp import web

from main.utils.user_auth import get_user
from main.utils import media_index, rec_store, wh_store

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
    # A syntactically valid key is not enough: otherwise any signed-in user
    # can manufacture history rows and distort their stats (and global play
    # ranking). Resolve it against the exact currently indexed upload.
    item = next(
        (candidate for candidate in media_index._items.values()
         if key == f"{candidate.secure_hash}{candidate.message_id}"),
        None,
    )
    if item is None:
        return _json({"error": "unknown media"}, status=404)
    try:
        body = await request.json()
        # The client-provided title is presentation-only; persist the
        # catalogue title so callers cannot poison history labels.
        title = (item.title or item.file_name or str(body.get("title", "")))[:200]
    except Exception:
        return _json({"error": "invalid body"}, status=400)
    await wh_store.record(int(user["sub"]), key, title)
    await rec_store.clear_cached(int(user["sub"]))
    return _json({"ok": True})

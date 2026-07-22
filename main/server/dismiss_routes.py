"""Not-for-me dismissal API.

POST /api/dismiss       body: {"tmdb_id": N, "kind": "movie"|"tv"}
DELETE /api/dismiss     body: {"tmdb_id": N, "kind": "movie"|"tv"} — un-dismiss
"""

from __future__ import annotations

import json
from aiohttp import web

from main.utils.user_auth import get_user
from main.utils import ai_rec_store, dismissed_store, rec_store

routes = web.RouteTableDef()


def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data), content_type="application/json", status=status)


@routes.post("/api/dismiss")
async def api_dismiss(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
        tmdb_id = int(body["tmdb_id"])
        kind = str(body.get("kind", ""))
        if kind not in ("movie", "tv"):
            return _json({"error": "invalid kind"}, status=400)
        kind = str(body.get("kind", "movie"))
        if kind not in ("movie", "tv"):
            kind = "movie"
    except Exception:
        return _json({"error": "invalid body"}, status=400)

    await dismissed_store.dismiss(int(user["sub"]), tmdb_id, kind)
    # Clear rec cache so next hub load excludes the dismissed title
    await rec_store.clear_cached(int(user["sub"]))
    await ai_rec_store.clear_cached(int(user["sub"]))
    return _json({"ok": True})


@routes.delete("/api/dismiss")
async def api_undismiss(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
        tmdb_id = int(body["tmdb_id"])
        kind = str(body.get("kind", ""))
        if kind and kind not in ("movie", "tv"):
            return _json({"error": "invalid kind"}, status=400)
    except Exception:
        return _json({"error": "invalid body"}, status=400)

    await dismissed_store.undismiss(int(user["sub"]), tmdb_id, kind)
    await rec_store.clear_cached(int(user["sub"]))
    await ai_rec_store.clear_cached(int(user["sub"]))
    return _json({"ok": True})

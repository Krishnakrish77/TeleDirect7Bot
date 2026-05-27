"""Per-title ratings API.

POST   /api/rate/{message_id}   body: {"rating":"up"|"down"}  — set/toggle
DELETE /api/rate/{message_id}                                  — clear
GET    /api/rate/{message_id}                                  — get user's rating + counts
"""

from __future__ import annotations

import json
from aiohttp import web

from main.utils.user_auth import get_user
from main.utils import ratings_store

routes = web.RouteTableDef()


def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data), content_type="application/json", status=status)


@routes.get("/api/rate/{mid:\\d+}")
async def api_get(request: web.Request) -> web.Response:
    user = get_user(request)
    mid = int(request.match_info["mid"])
    rating = await ratings_store.get_rating(int(user["sub"]), mid) if user else None
    counts = await ratings_store.get_counts(mid)
    return _json({"rating": rating, "counts": counts})


@routes.post("/api/rate/{mid:\\d+}")
async def api_set(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    mid = int(request.match_info["mid"])
    try:
        body = await request.json()
        rating = body.get("rating", "")
        if rating not in ("up", "down"):
            return _json({"error": "invalid rating"}, status=400)
    except Exception:
        return _json({"error": "invalid body"}, status=400)

    existing = await ratings_store.get_rating(int(user["sub"]), mid)
    if existing == rating:
        # Toggle off — clicking same button again removes the rating
        await ratings_store.delete_rating(int(user["sub"]), mid)
        rating = None
    else:
        await ratings_store.set_rating(int(user["sub"]), mid, rating)

    counts = await ratings_store.get_counts(mid)
    return _json({"rating": rating, "counts": counts})


@routes.delete("/api/rate/{mid:\\d+}")
async def api_delete(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    mid = int(request.match_info["mid"])
    await ratings_store.delete_rating(int(user["sub"]), mid)
    counts = await ratings_store.get_counts(mid)
    return _json({"rating": None, "counts": counts})

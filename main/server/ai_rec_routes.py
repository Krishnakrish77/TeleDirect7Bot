"""Per-user AI recommendation endpoints (Gemini-backed, catalogue-grounded).

Both routes require a logged-in user and a configured GEMINI_API_KEY.
"""

from __future__ import annotations

from typing import Optional

from aiohttp import web

from main.utils import ai_rec, gemini
from main.utils.user_auth import get_user

routes = web.RouteTableDef()


def _uid(request: web.Request) -> Optional[int]:
    user = get_user(request)
    if not user:
        return None
    try:
        return int(user["sub"])
    except (KeyError, TypeError, ValueError):
        return None


@routes.get("/api/app/ai/recommendations")
async def ai_recommendations(request: web.Request) -> web.Response:
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    refresh = request.query.get("refresh") in ("1", "true", "yes")
    result = await ai_rec.get_ai_recommendations(uid, refresh=refresh)
    return web.json_response(result)


@routes.post("/api/app/ai/recommendations")
async def ai_recommendations_chat(request: web.Request) -> web.Response:
    if not gemini.available():
        return web.json_response({"error": "AI recommendations are not enabled"}, status=404)
    uid = _uid(request)
    if uid is None:
        return web.json_response({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = (str(body.get("query") or "")).strip()[:300]
    result = await ai_rec.get_ai_recommendations(uid, query=query or None)
    return web.json_response(result)

"""Recommendation-feedback API.

POST /api/app/recommendations/events records batched, authenticated card
engagement events. It intentionally stores no free-form query or title text.
"""

from __future__ import annotations

from aiohttp import web

from main.utils import ai_rec_store, rec_feedback_store, rec_store
from main.utils.user_auth import get_user

routes = web.RouteTableDef()

_ACTIONS = {"impression", "open", "play", "save", "unsave", "dismiss"}
_SOURCES = {"home", "ai"}
_KINDS = {"movie", "tv"}
_MAX_EVENTS = 40


def _clean_event(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip().lower()
    source = str(raw.get("source") or "").strip().lower()
    item_id = str(raw.get("itemId") or "").strip()
    if action not in _ACTIONS or source not in _SOURCES or not item_id or len(item_id) > 140:
        return None
    event = {"action": action, "source": source, "item_id": item_id}
    shelf = str(raw.get("shelf") or "").strip()
    if shelf:
        event["shelf"] = shelf[:100]
    try:
        tmdb_id = int(raw.get("tmdbId") or 0)
    except (TypeError, ValueError):
        tmdb_id = 0
    kind = str(raw.get("tmdbKind") or "").strip().lower()
    if tmdb_id > 0 and kind in _KINDS:
        event["tmdb_id"] = tmdb_id
        event["tmdb_kind"] = kind
    try:
        position = int(raw.get("position"))
    except (TypeError, ValueError):
        position = -1
    if 0 <= position < 100:
        event["position"] = position
    return event


@routes.post("/api/app/recommendations/events")
async def api_recommendation_events(request: web.Request) -> web.Response:
    user = get_user(request)
    if not user:
        return web.json_response({"error": "unauthenticated"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(raw_events, list):
        return web.json_response({"error": "events must be a list"}, status=400)
    events = [event for event in (_clean_event(raw) for raw in raw_events[:_MAX_EVENTS]) if event]
    accepted = await rec_feedback_store.record_many(int(user["sub"]), events)
    # An affirmative engagement is useful immediately; discard derived
    # candidate/pick caches so the next recommendation request learns from it.
    if accepted and any(event["action"] in {"open", "play"} for event in events):
        user_id = int(user["sub"])
        await rec_store.clear_cached(user_id)
        await ai_rec_store.clear_cached(user_id)
    return web.json_response({"ok": True, "accepted": accepted})

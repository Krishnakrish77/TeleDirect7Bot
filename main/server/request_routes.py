"""User and admin APIs for TMDB-backed title requests."""

from __future__ import annotations

from aiohttp import web

from main.server.tmdb_images import tmdb_image_url
from main.utils import request_store, tmdb
from main.utils.user_auth import get_user


routes = web.RouteTableDef()


def _user_id(request: web.Request) -> int | None:
    user = get_user(request)
    try:
        return int(user["sub"]) if user else None
    except (KeyError, TypeError, ValueError):
        return None


def _require_user(request: web.Request) -> int:
    user_id = _user_id(request)
    if user_id is None:
        raise web.HTTPUnauthorized(text="Sign in to request a title")
    return user_id


def _require_admin(request: web.Request) -> int:
    user = get_user(request)
    if not user or not user.get("is_admin"):
        raise web.HTTPForbidden(text="Admin access required")
    return int(user["sub"])


def _title_payload(title: dict) -> dict:
    availability = request_store.library_availability(int(title["tmdbId"]), str(title["kind"]))
    return {
        **title,
        "posterUrl": tmdb_image_url(str(title.get("posterPath") or ""), "w342") if title.get("posterPath") else "",
        **availability,
    }


async def _body(request: web.Request) -> dict:
    try:
        value = await request.json()
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


@routes.get("/api/app/requests/search")
async def search_requests(request: web.Request) -> web.Response:
    _require_user(request)
    query = (request.query.get("q") or "").strip()
    if len(query) < 2:
        return web.json_response({"items": []})
    if not tmdb.is_configured():
        return web.json_response({"error": "Title discovery is not configured"}, status=503)
    rows = await tmdb.search_titles(query)
    return web.json_response({"items": [_title_payload(row) for row in rows]})


@routes.get(r"/api/app/requests/title/{kind:movie|tv}/{tmdb_id:\d+}")
async def request_title(request: web.Request) -> web.Response:
    _require_user(request)
    title = await tmdb.fetch_request_title(int(request.match_info["tmdb_id"]), request.match_info["kind"])
    if title is None:
        return web.json_response({"error": "Title was not found"}, status=404)
    return web.json_response(_title_payload(title))


@routes.get("/api/app/requests")
async def my_requests(request: web.Request) -> web.Response:
    user_id = _require_user(request)
    if not request_store.is_available():
        return web.json_response({"error": "Requests need MongoDB storage"}, status=503)
    return web.json_response({"items": await request_store.list_for_user(user_id)})


@routes.post("/api/app/requests")
async def create_request(request: web.Request) -> web.Response:
    user_id = _require_user(request)
    if not request_store.is_available():
        return web.json_response({"error": "Requests need MongoDB storage"}, status=503)
    body = await _body(request)
    try:
        tmdb_id = int(body.get("tmdbId") or 0)
    except (TypeError, ValueError):
        tmdb_id = 0
    kind = str(body.get("kind") or "")
    if not tmdb_id or kind not in {"movie", "tv"}:
        return web.json_response({"error": "Choose a valid movie or series"}, status=400)
    title = await tmdb.fetch_request_title(tmdb_id, kind)
    if title is None:
        return web.json_response({"error": "Title was not found"}, status=404)
    availability = request_store.library_availability(tmdb_id, kind)
    seasons = body.get("seasons") if isinstance(body.get("seasons"), list) else []
    if kind == "movie" and availability["inLibrary"]:
        return web.json_response({"error": "This movie is already in your library"}, status=409)
    if kind == "tv":
        try:
            requested = {int(value) for value in seasons}
        except (TypeError, ValueError):
            requested = set()
        if requested and requested.issubset(set(availability["availableSeasons"])):
            return web.json_response({"error": "Those seasons are already in your library"}, status=409)
    saved, outcome = await request_store.create(user_id, title, seasons)
    if outcome == "created":
        # Avoid showing the just-requested title again from an in-memory AI
        # discovery cache during the same session.
        try:
            from main.utils import ai_rec
            ai_rec._external_pick_cache.pop(user_id, None)
        except Exception:
            pass
        return web.json_response({"item": saved, "duplicate": False}, status=201)
    if outcome == "duplicate":
        return web.json_response({"item": saved, "duplicate": True})
    messages = {
        "limit": "You have reached the limit of 5 open requests",
        "seasons_required": "Choose at least one season",
        "unavailable": "Requests need MongoDB storage",
    }
    return web.json_response({"error": messages.get(outcome, "Could not save your request")}, status=400 if outcome != "unavailable" else 503)


@routes.delete(r"/api/app/requests/{request_id:[a-f0-9]{32}}")
async def cancel_request(request: web.Request) -> web.Response:
    user_id = _require_user(request)
    if not await request_store.cancel(user_id, request.match_info["request_id"]):
        return web.json_response({"error": "That open request was not found"}, status=404)
    return web.json_response({"ok": True})


@routes.get("/api/app/admin/requests")
async def admin_requests(request: web.Request) -> web.Response:
    _require_admin(request)
    if not request_store.is_available():
        return web.json_response({"error": "Requests need MongoDB storage"}, status=503)
    return web.json_response({"items": await request_store.list_admin(request.query.get("status") or "")})


@routes.post(r"/api/app/admin/requests/{kind:movie|tv}/{tmdb_id:\d+}/state")
async def set_request_state(request: web.Request) -> web.Response:
    _require_admin(request)
    body = await _body(request)
    state = str(body.get("state") or "")
    changed = await request_store.set_title_state(
        int(request.match_info["tmdb_id"]), request.match_info["kind"], state, str(body.get("note") or ""),
    )
    if not changed:
        return web.json_response({"error": "No matching open requests"}, status=404)
    return web.json_response({"ok": True, "changed": changed})


@routes.get("/requests")
@routes.get("/admin/requests")
async def requests_spa(request: web.Request) -> web.Response:
    from main.server.spa_routes import _app_index_response
    return _app_index_response(request)

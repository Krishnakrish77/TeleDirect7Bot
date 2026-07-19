"""Continue-Watching sync API.

GET    /api/cw          — fetch all entries for the signed-in user
POST   /api/cw/{key}    — upsert one entry  {pos, dur, t, title}
DELETE /api/cw/{key}    — remove one entry
DELETE /api/cw          — clear all entries
"""

from __future__ import annotations

import json
import math
import re
import time

from aiohttp import web

from main.utils.user_auth import get_user
from main.utils import cw_store

routes = web.RouteTableDef()

# {hash}{message_id} — e.g. AgADAx1234567 or a longer 15-char hash variant
_VALID_KEY = re.compile(r'^[A-Za-z0-9_-]{3,50}$')


_get_user = get_user  # shared auth helper


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
    data = await cw_store.get_all(int(user["sub"]))
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
        started_at = int(body.get("startedAt", t))
        title = str(body.get("title", ""))[:200]
        device_id = str(body.get("deviceId", ""))[:64]
        device_label = str(body.get("deviceLabel", ""))[:64]
    except Exception:
        return _json({"error": "invalid body"}, status=400)
    if not all(math.isfinite(value) for value in (pos, dur)) or dur <= 0 or pos < 0:
        return _json({"error": "invalid progress"}, status=400)
    now_ms = int(time.time() * 1000)
    # Use server time for future-skewed clients so a fast device cannot later
    # overwrite a deletion tombstone created by another device.
    if t <= 0 or t > now_ms:
        t = now_ms
    if started_at <= 0 or started_at > now_ms:
        started_at = t
    # Completion is authoritative on the server too.  This prevents stale
    # near-finished entries being displayed or later synced by another device.
    if pos / dur >= 0.95:
        await cw_store.delete_one(int(user["sub"]), key)
        return _json({"ok": True})
    await cw_store.upsert(int(user["sub"]), key, pos, dur, t, title, started_at,
                          device_id=device_id, device_label=device_label)
    return _json({"ok": True})


@routes.delete("/api/cw/{key}")
async def api_delete_one(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    key = request.match_info["key"]
    if not _VALID_KEY.match(key):
        return _json({"error": "invalid key"}, status=400)
    await cw_store.delete_one(int(user["sub"]), key)
    return _json({"ok": True})


@routes.delete("/api/cw")
async def api_delete_all(request: web.Request) -> web.Response:
    user = _get_user(request)
    if not user:
        return _json({"error": "unauthenticated"}, status=401)
    await cw_store.delete_all(int(user["sub"]))
    return _json({"ok": True})

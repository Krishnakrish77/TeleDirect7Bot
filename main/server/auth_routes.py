"""Telegram Login Widget auth endpoints."""
from __future__ import annotations

import json

from aiohttp import web

from main.utils.user_auth import verify_telegram_payload, create_token

routes = web.RouteTableDef()


@routes.post("/auth/telegram")
async def auth_telegram(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")

    if not verify_telegram_payload(data):
        raise web.HTTPForbidden(text="Invalid Telegram auth payload")

    token = create_token(data)
    resp = web.Response(
        text=json.dumps({"token": token, "ok": True}),
        content_type="application/json",
        status=200,
    )
    # Set a session cookie so server-rendered pages (e.g. /admin) receive
    # the token automatically without JS needing to set headers.
    # The JS side also stores it in sessionStorage for client-side use.
    resp.set_cookie(
        "td_session", token,
        httponly=False,      # readable by JS for Authorization header use
        samesite="Lax",
        secure=False,        # set to True in production with HTTPS
        path="/",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )
    return resp


@routes.post("/auth/logout")
async def auth_logout(request: web.Request) -> web.Response:
    return web.Response(
        text='{"ok":true}',
        content_type="application/json",
        status=200,
    )

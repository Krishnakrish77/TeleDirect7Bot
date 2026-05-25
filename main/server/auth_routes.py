"""Telegram Login Widget auth endpoints."""
from __future__ import annotations

import json

from aiohttp import web

from main.utils.user_auth import verify_telegram_payload, create_token
from main.vars import Var

routes = web.RouteTableDef()

_SECURE = Var.HAS_SSL or Var.ON_KOYEB


def _set_session_cookie(resp: web.Response, value: str, max_age: int) -> None:
    resp.set_cookie(
        "td_session", value,
        httponly=False,     # JS needs to read it for Authorization header use
        samesite="Lax",
        secure=_SECURE,
        path="/",
        max_age=max_age,
    )


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
    _set_session_cookie(resp, token, max_age=60 * 60 * 24 * 30)
    return resp


@routes.post("/auth/logout")
async def auth_logout(request: web.Request) -> web.Response:
    resp = web.Response(text='{"ok":true}', content_type="application/json")
    _set_session_cookie(resp, "", max_age=0)
    return resp

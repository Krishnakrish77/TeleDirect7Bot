"""Telegram Login Widget auth endpoints."""
from __future__ import annotations

import json
import logging

from aiohttp import web

from main.utils.user_auth import verify_telegram_payload, create_token, revoke_token
from main.vars import Var

routes = web.RouteTableDef()

_SECURE = Var.HAS_SSL or Var.ON_KOYEB


def _set_session_cookie(resp: web.Response, value: str, max_age: int) -> None:
    resp.set_cookie(
        "td_session", value,
        httponly=True,  # JS MUST NOT read cookie directly; token lives in JSON response body + sessionStorage('td:auth')
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
    # Server-side revocation: invalidate the current session's jti so a
    # copied Bearer token or restored cookie from before logout is rejected.
    # Absent/expired token is a no-op; we still clear the cookie.
    for src in (request.headers.get("Authorization", ""), request.cookies.get("td_session", "")):
        raw = src[7:] if src.startswith("Bearer ") else src
        if not raw:
            continue
        try:
            if await revoke_token(raw):
                logging.info("auth: revoked session on logout")
                break
        except Exception:
            logging.exception("auth: revoke on logout failed")
    resp = web.Response(text='{"ok":true}', content_type="application/json")
    _set_session_cookie(resp, "", max_age=0)
    return resp

"""Signed-token auth for the /admin web routes.

The bot never stores admin passwords. The owner DMs ``/admin`` to the bot;
the bot replies with a one-time URL containing an HMAC-signed token tied
to the owner's Telegram user id with a 15-minute expiry. Visiting that URL
exchanges the token for a short session cookie used by the rest of the
admin routes.

Token format: ``<payload>.<signature>`` where ``payload`` is urlsafe-b64
JSON ``{"u": user_id, "e": unix_expiry, "n": random_nonce}`` and
``signature`` is HMAC-SHA256 over the payload bytes using a server secret
derived from the bot token (so it survives restarts without an extra env
var, but no plaintext secret ever ships in URLs).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from main.vars import Var


_TOKEN_TTL = 15 * 60       # one-time link expires 15 min after issue
_SESSION_TTL = 60 * 60     # session cookie good for one hour
_COOKIE_NAME = "admin_session"


def _secret() -> bytes:
    """Derive a stable HMAC key from BOT_TOKEN + optional salt env var."""
    salt = os.environ.get("ADMIN_TOKEN_SALT", "")
    return hashlib.sha256((Var.BOT_TOKEN + salt).encode("utf-8")).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: bytes) -> str:
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def _verify(token: str) -> Optional[dict]:
    if not token or "." not in token:
        return None
    body, sig = token.split(".", 1)
    try:
        payload = _b64d(body)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig)):
            return None
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    if time.time() >= float(data.get("e", 0)):
        return None
    return data


def issue_one_time_token(user_id: int) -> str:
    """Token embedded in the URL that the bot sends in DM."""
    payload = json.dumps({
        "u": int(user_id),
        "e": time.time() + _TOKEN_TTL,
        "n": secrets.token_urlsafe(8),
        "k": "ot",  # one-time
    }, separators=(",", ":")).encode("utf-8")
    return _sign(payload)


def issue_session_token(user_id: int) -> str:
    """Cookie value set after the one-time token is exchanged."""
    payload = json.dumps({
        "u": int(user_id),
        "e": time.time() + _SESSION_TTL,
        "n": secrets.token_urlsafe(8),
        "k": "s",
    }, separators=(",", ":")).encode("utf-8")
    return _sign(payload)


def verify_session(token: str) -> Optional[int]:
    """Return the authenticated user_id or None."""
    data = _verify(token)
    if data is None:
        return None
    if data.get("k") != "s":
        return None
    if int(data["u"]) != int(Var.OWNER_ID):
        return None
    return int(data["u"])


def verify_one_time(token: str) -> Optional[int]:
    data = _verify(token)
    if data is None:
        return None
    if data.get("k") != "ot":
        return None
    if int(data["u"]) != int(Var.OWNER_ID):
        return None
    return int(data["u"])


COOKIE_NAME = _COOKIE_NAME
SESSION_TTL = _SESSION_TTL

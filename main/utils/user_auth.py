"""Telegram Login Widget verification + JWT session helpers."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Optional

import jwt

from main.vars import Var

_ALGORITHM = "HS256"
_TTL = 60 * 60 * 24 * 30  # 30 days


def verify_telegram_payload(data: dict) -> bool:
    """Verify the hash Telegram attaches to Login Widget callbacks."""
    check_hash = data.get("hash", "")
    if not check_hash:
        return False

    data_check = {k: v for k, v in data.items() if k != "hash"}
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(data_check.items())
    )

    secret_key = hashlib.sha256(Var.BOT_TOKEN.encode()).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Check signature and freshness (< 24 h)
    fresh = (time.time() - int(data.get("auth_date", 0))) < 86_400
    return fresh and hmac.compare_digest(expected, check_hash)


def create_token(telegram_data: dict) -> str:
    payload = {
        "sub": int(telegram_data["id"]),
        "name": telegram_data.get("first_name", ""),
        "username": telegram_data.get("username", ""),
        "photo": telegram_data.get("photo_url", ""),
        "is_admin": int(telegram_data["id"]) == Var.OWNER_ID,
        "iat": int(time.time()),
        "exp": int(time.time()) + _TTL,
    }
    return jwt.encode(payload, Var.JWT_SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, Var.JWT_SECRET, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None

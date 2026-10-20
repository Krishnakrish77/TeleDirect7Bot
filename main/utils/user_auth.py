"""Telegram Login Widget verification + JWT session helpers."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

import jwt

from main.vars import Var

_ALGORITHM = "HS256"
_TTL = 60 * 60 * 24 * 30  # 30 days

# Mongo-backed revocation denylist for per-token logout. Same shape as
# cw_store/request_store: lazily resolved Motor db from media_index._store.
_revoked_prefix = "revoked_jtis"
_revoked_jtis: set[str] = set()  # in-process fallback when Mongo unavailable
_revoked_indexes_ready = False


def _get_db():
    try:
        from main.utils import media_index as _mi
        s = _mi._store
        if s is None or not hasattr(s, "_client"):
            return None
        return s._client[s._db_name]
    except Exception:
        return None


async def _ensure_revoked_indexes() -> None:
    global _revoked_indexes_ready
    if _revoked_indexes_ready:
        return
    db = _get_db()
    if db is None:
        return
    try:
        coll = db[_revoked_prefix]
        await coll.create_index("exp", expireAfterSeconds=0)
        _revoked_indexes_ready = True
    except Exception:
        logging.exception("user_auth: ensure_revoked_indexes failed")


async def revoke_jti(jti: str, exp: int) -> None:
    """Persist jti in the revoke denylist until its exp; best-effort."""
    if not jti:
        return
    # Warm in-process set immediately so logout takes effect even before
    # Mongo op completes (and when Mongo is absent entirely).
    _revoked_jtis.add(jti)
    db = _get_db()
    if db is None:
        return
    try:
        from datetime import datetime, timezone
        await _ensure_revoked_indexes()
        coll = db[_revoked_prefix]
        exp_dt = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        await coll.update_one({"_id": jti}, {"$set": {"exp": exp_dt}}, upsert=True)
    except Exception:
        logging.exception("user_auth: revoke_jti(%s) failed", jti)


async def revoke_token(token: str) -> bool:
    """Revoke a raw JWT by jti/exp if valid; returns whether a jti was present."""
    payload = decode_token(token)
    if not payload or not payload.get("jti"):
        return False
    await revoke_jti(str(payload["jti"]), int(payload.get("exp", 0) or int(time.time()) + _TTL))
    return True


def is_revoked_sync(jti: str | None) -> bool:
    """In-process revocation check (called on hot path by decode_token)."""
    if not jti:
        return False
    return jti in _revoked_jtis


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
    try:
        fresh = (time.time() - int(data.get("auth_date", 0))) < 86_400
    except (ValueError, TypeError):
        return False
    return fresh and hmac.compare_digest(expected, check_hash)


def create_token(telegram_data: dict) -> str:
    payload = {
        "sub": str(int(telegram_data["id"])),
        "name": telegram_data.get("first_name", ""),
        "username": telegram_data.get("username", ""),
        "photo": telegram_data.get("photo_url", ""),
        "is_admin": int(telegram_data["id"]) == Var.OWNER_ID,
        "jti": secrets.token_hex(8),
        "iat": int(time.time()),
        "exp": int(time.time()) + _TTL,
    }
    return jwt.encode(payload, Var.JWT_SECRET, algorithm=_ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, Var.JWT_SECRET, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    # PyJWT requires sub to be a string; keep JWT spec compliant but expose
    # it to legacy callers as int for backward compat until frontend types cut over.
    if "sub" in payload:
        try:
            payload["sub"] = int(payload["sub"])
        except (TypeError, ValueError):
            return None
    # Server-side revocation: tokens explicitly logged out are rejected even
    # though their signature and exp are still valid.
    if payload.get("jti") and is_revoked_sync(str(payload["jti"])):
        return None
    return payload

def get_user(request) -> Optional[dict]:
    """Extract and verify JWT from Authorization header or td_session cookie."""
    from aiohttp import web as _web
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = decode_token(auth[7:])
        if user:
            return user
    cookie = request.cookies.get("td_session", "")
    if cookie:
        return decode_token(cookie)
    return None

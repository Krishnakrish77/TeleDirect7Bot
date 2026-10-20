"""Optional time-limited HMAC signed links for private sharing.

Stream URLs are public capability links by default (``/{hash}{id}`` validated
against the Telegram unique_id in ``main/utils/file_properties.py``). This
module adds an *expiry-bearing* token for private-share flows where
the operator wants a time-boxed URL instead of a permanent bearer link.

New route ``/s/{token}`` is registered in ``main/server/stream_routes.py``
and verifies via :func:`verify_signed`; callers obtain tokens via
:func:`sign_message` inside leader handlers or admin flows.

Secret: ``Var.JWT_SECRET`` (same key that signs ``td_session`` JWTs) so no
new env is required. Tokens are URL-safe base64 with no padding.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import time
from typing import Optional


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def sign_message(message_id: int, *, ttl_seconds: int = 86400) -> str:
    from main.vars import Var

    exp = int(time.time()) + int(ttl_seconds)
    payload = f"{message_id}:{exp}".encode()
    sig = _hmac.new(Var.JWT_SECRET.encode(), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


def verify_signed(token: str) -> Optional[int]:
    """Return the message_id if the token is valid and not expired, else None."""
    from main.vars import Var

    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
        expected = _hmac.new(Var.JWT_SECRET.encode(), payload, hashlib.sha256).digest()
        if not _hmac.compare_digest(sig, expected):
            return None
        mid_s, exp_s = payload.decode().split(":", 1)
        if int(exp_s) < int(time.time()):
            return None
        return int(mid_s)
    except Exception:
        return None

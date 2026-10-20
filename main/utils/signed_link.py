"""Optional time-limited HMAC signed links for private sharing.

Stream URLs are public capability links by default (``/{hash}{id}`` validated
against the Telegram unique_id in ``main/utils/file_properties.py``). This
module adds an *expiry-bearing* token for time-boxed private-share URLs
instead of a permanent bearer link.

New route ``/s/{token}`` is registered in ``main/server/stream_routes.py``
and verifies via :func:`verify_signed`; tokens can be minted via
:func:`sign_message`.

Secret: ``Var.JWT_SECRET`` (same key that signs ``td_session`` JWTs) so no
new env is required. Tokens are URL-safe base64 with no padding.

Expiry is the only revocation mechanism — there is no denylist. Rotating
``JWT_SECRET`` invalidates all signed links (and all sessions).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac as _hmac
import time
from typing import Optional

_PURPOSE = "sl:"  # distinguish from JWT/refresh tokens under same key


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def sign_message(message_id: int, *, ttl_seconds: int = 86400) -> str:
    if message_id <= 0:
        raise ValueError(f"message_id must be positive, got {message_id!r}")
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds!r}")
    from main.vars import Var

    exp = int(time.time()) + int(ttl_seconds)
    payload = f"{_PURPOSE}{message_id}:{exp}".encode()
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
        text = payload.decode()
        if not text.startswith(_PURPOSE):
            return None
        mid_s, exp_s = text[len(_PURPOSE):].split(":", 1)
        if int(exp_s) < int(time.time()):
            return None
        mid = int(mid_s)
        if mid <= 0:
            return None
        return mid
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None

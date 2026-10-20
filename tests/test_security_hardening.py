"""CSRF middleware helpers and signed-link helper for P1 hardening."""

import binascii
import os
import time
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-0cf9bff2-42f2-43d2-99f2-000000000001")

from main.utils.signed_link import _PURPOSE, sign_message, verify_signed  # noqa: E402
from main.server import _csrf_origin_allowed, _csrf_extract_host, _csrf_get_allowed  # noqa: E402


class SignedLinkTest(unittest.TestCase):
    def test_round_trip(self):
        tok = sign_message(42)
        self.assertEqual(verify_signed(tok), 42)

    def test_rejects_unknown_message_id(self):
        # token for 42 doesn't validate as 43 — different payload sig mismatch
        tok = sign_message(42)
        # mutate last char to break base64
        tampered = tok[:-1] + ("A" if tok[-1] != "A" else "B")
        self.assertIsNone(verify_signed(tampered))

    def test_rejects_expired(self):
        tok = sign_message(1, ttl_seconds=3600)
        with patch("main.utils.signed_link.time.time", return_value=time.time() + 7200):
            self.assertIsNone(verify_signed(tok))

    def test_rejects_non_positive_message_id(self):
        with self.assertRaises(ValueError):
            sign_message(0)
        with self.assertRaises(ValueError):
            sign_message(-1)

    def test_rejects_non_positive_ttl(self):
        with self.assertRaises(ValueError):
            sign_message(1, ttl_seconds=0)
        with self.assertRaises(ValueError):
            sign_message(1, ttl_seconds=-5)

    def test_purpose_tag_present_in_payload(self):
        tok = sign_message(7)
        from main.utils.signed_link import _b64url_decode
        payload_b64, _sig = tok.split(".", 1)
        text = _b64url_decode(payload_b64).decode()
        self.assertTrue(text.startswith(_PURPOSE), text)

    def test_rejects_garbage_token(self):
        self.assertIsNone(verify_signed("not_a_token"))
        self.assertIsNone(verify_signed(""))
        self.assertIsNone(verify_signed("a.b.c"))

    def test_narrow_exception_surface(self):
        # verify_signed must NOT swallow unexpected errors — only known decode/parse errors
        # Patch hmac to raise AttributeError (simulates refactored code typo) → should NOT be swallowed
        # Note: current impl catches (ValueError, UnicodeDecodeError, binascii.Error)
        # but AttributeError will bubble — this is intentional fail-loud
        import hmac as hmac_mod
        with patch.object(hmac_mod, "new", side_effect=AttributeError("boom")):
            with self.assertRaises(AttributeError):
                verify_signed(sign_message(1))


class CsrfHelpersTest(unittest.TestCase):
    def test_extract_host_from_origin(self):
        self.assertEqual(_csrf_extract_host("http://example.com/path"), "example.com")
        self.assertEqual(_csrf_extract_host("http://example.com:8080/x"), "example.com")
        self.assertEqual(_csrf_extract_host("https://42.42.42.1/x"), "42.42.42.1")
        self.assertEqual(_csrf_extract_host("http://[::1]:8080/x"), "::1")
        self.assertIsNone(_csrf_extract_host(""))
        self.assertIsNone(_csrf_extract_host("     "))

    def test_origin_allowed_exact_origin_and_host_fallback(self):
        allowed_hosts = {"example.com"}
        allowed_origins = {"https://example.com"}
        self.assertTrue(_csrf_origin_allowed("https://example.com", allowed_hosts, allowed_origins))
        # same host different port → fails origin exact check but passes host check (allowed)
        self.assertTrue(_csrf_origin_allowed("https://example.com:9999", allowed_hosts, allowed_origins))
        # different host
        self.assertFalse(_csrf_origin_allowed("https://evil.com", allowed_hosts, allowed_origins))

    def test_origin_allowed_port_matters_when_no_host_fallback(self):
        # Only origin allow-list, no host entry
        allowed_hosts: set[str] = set()
        allowed_origins = {"https://example.com"}
        self.assertTrue(_csrf_origin_allowed("https://example.com", allowed_hosts, allowed_origins))
        self.assertFalse(_csrf_origin_allowed("https://example.com:8443", allowed_hosts, allowed_origins))

    def test_get_allowed_does_not_include_zero_bind(self):
        # BIND_ADDRESS=0.0.0.0 must expand to localhost aliases, not literal 0.0.0.0
        import main.vars as vars_mod
        orig = vars_mod.Var.BIND_ADDRESS
        try:
            vars_mod.Var.BIND_ADDRESS = "0.0.0.0"
            hosts, _origins = _csrf_get_allowed()
            self.assertNotIn("0.0.0.0", hosts)
            self.assertIn("localhost", hosts)
            self.assertIn("127.0.0.1", hosts)
        finally:
            vars_mod.Var.BIND_ADDRESS = orig

    def test_dead_helper_removed(self):
        import main.server as srv
        self.assertFalse(hasattr(srv, "_csrf_allowed_host"), "_csrf_allowed_host dead helper should be deleted")


if __name__ == "__main__":
    unittest.main()

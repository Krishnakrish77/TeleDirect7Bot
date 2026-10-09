import asyncio
import importlib
import ipaddress
import os
import unittest
from unittest.mock import patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

request_routes = importlib.import_module("main.server.request_routes")


class FakeRequest:
    """Direct (untrusted) peer: the client IP is the socket peer itself."""

    def __init__(self, ip: str):
        self.headers = {}
        self.remote = ip
        self.query = {}


class ProxiedRequest:
    """Peer is a trusted proxy; the client IP arrives via X-Forwarded-For."""

    def __init__(self, forwarded_ip: str):
        self.headers = {"X-Forwarded-For": forwarded_ip}
        self.remote = "10.0.0.5"
        self.query = {}


class AnonRateLimitTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        request_routes._anon_hits.clear()

    async def test_anon_create_limit_is_enforced_per_ip(self):
        with patch("main.server.stream_routes._TRUSTED_PROXY_NETWORKS", ()):
            ip = "203.0.113.7"
            for _ in range(request_routes._ANON_LIMITS["create"]):
                await request_routes._anon_rate_limit(FakeRequest(ip), "create")
            with self.assertRaises(Exception) as ctx:
                await request_routes._anon_rate_limit(FakeRequest(ip), "create")
            self.assertEqual(getattr(ctx.exception, "status_code", None), 429)
            # A different IP is unaffected.
            await request_routes._anon_rate_limit(FakeRequest("198.51.100.9"), "create")

    async def test_xff_is_honoured_only_for_trusted_proxies(self):
        trusted = (ipaddress.ip_network("10.0.0.0/8"),)
        with patch("main.server.stream_routes._TRUSTED_PROXY_NETWORKS", trusted):
            for _ in range(request_routes._ANON_LIMITS["search"]):
                await request_routes._anon_rate_limit(ProxiedRequest("203.0.113.7"), "search")
            with self.assertRaises(Exception) as ctx:
                await request_routes._anon_rate_limit(ProxiedRequest("203.0.113.7"), "search")
            self.assertEqual(getattr(ctx.exception, "status_code", None), 429)
            # Different forwarded client behind the same proxy is unaffected.
            await request_routes._anon_rate_limit(ProxiedRequest("198.51.100.9"), "search")

    async def test_untrusted_peer_cannot_spoof_xff(self):
        with patch("main.server.stream_routes._TRUSTED_PROXY_NETWORKS", ()):
            # All XFF-spoofing clients collapse onto their real peer address.
            for _ in range(request_routes._ANON_LIMITS["create"]):
                request = FakeRequest("203.0.113.7")
                request.headers = {"X-Forwarded-For": f"spoofed-{id(request)}"}
                await request_routes._anon_rate_limit(request, "create")
            request = FakeRequest("203.0.113.7")
            request.headers = {"X-Forwarded-For": "another-spoof"}
            with self.assertRaises(Exception) as ctx:
                await request_routes._anon_rate_limit(request, "create")
            self.assertEqual(getattr(ctx.exception, "status_code", None), 429)

    async def test_stale_entries_are_swept(self):
        with patch("main.server.stream_routes._TRUSTED_PROXY_NETWORKS", ()):
            request_routes._anon_hits[("create", "1.2.3.4")] = [-999.0]
            await request_routes._anon_rate_limit(FakeRequest("198.51.100.9"), "create")
            self.assertNotIn(("create", "1.2.3.4"), request_routes._anon_hits)


if __name__ == "__main__":
    unittest.main()

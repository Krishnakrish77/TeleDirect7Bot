import os
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from aiohttp import web

from main.utils.custom_dl import MediaSessionUnavailable

stream_routes = importlib.import_module("main.server.stream_routes")


class _FakeClient:
    def __init__(self, name="client"):
        self.name = name


class _FakeFileId:
    unique_id = "abc123"
    file_size = 1000
    mime_type = "video/mp4"
    file_name = 'bad";\r\nname తెలుగు.mp4'


class _FakeStreamer:
    async def get_file_properties(self, message_id):
        return _FakeFileId()

    async def generate_media_session(self, client, file_id):
        return object()


class _BadSessionStreamer(_FakeStreamer):
    async def generate_media_session(self, client, file_id):
        raise MediaSessionUnavailable("auth failed")


class _LargeFakeFileId(_FakeFileId):
    file_size = stream_routes.skeleton_cache.HEAD_SIZE + 100


class _UnavailableStreamer:
    async def get_file_properties(self, message_id):
        return _LargeFakeFileId()

    async def generate_media_session(self, client, file_id):
        raise MediaSessionUnavailable("auth failed")


class _FakeRequest:
    def __init__(
        self,
        *,
        method="HEAD",
        headers=None,
        http_range=slice(None, None, 1),
        query=None,
    ):
        self.method = method
        self.headers = headers or {}
        self._http_range = http_range
        self.rel_url = SimpleNamespace(query=query or {})
        self.remote = "127.0.0.1"

    @property
    def http_range(self):
        if isinstance(self._http_range, Exception):
            raise self._http_range
        return self._http_range


class StreamRouteDownloadTest(unittest.IsolatedAsyncioTestCase):
    async def _call_media_streamer(self, request):
        client = _FakeClient()
        with (
            patch.object(stream_routes, "multi_clients", {0: client}),
            patch.object(stream_routes, "work_loads", {0: 0}),
            patch.object(stream_routes, "class_cache", {client: _FakeStreamer()}),
            patch.object(stream_routes, "_client_cooldowns", {}),
        ):
            return await stream_routes.media_streamer(request, 42, "abc")

    async def test_media_session_auth_failure_returns_retryable_503(self):
        client = _FakeClient()
        with (
            patch.object(stream_routes, "multi_clients", {0: client}),
            patch.object(stream_routes, "work_loads", {0: 0}),
            patch.object(stream_routes, "class_cache", {client: _UnavailableStreamer()}),
            patch.object(stream_routes, "_total_active", 0),
            patch.object(stream_routes, "_ip_active", {}),
            patch.object(stream_routes, "_client_cooldowns", {}),
        ):
            with self.assertRaises(web.HTTPServiceUnavailable) as ctx:
                await stream_routes.media_streamer(
                    _FakeRequest(method="GET", query={"download": "1"}),
                    42,
                    "abc",
                )

        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.headers["Retry-After"], "5")
        self.assertEqual(stream_routes._total_active, 0)
        self.assertEqual(stream_routes._ip_active, {})

    async def test_direct_stream_falls_back_to_second_client(self):
        bad_client = _FakeClient("bad")
        good_client = _FakeClient("good")
        large_file = _LargeFakeFileId()
        large_file.file_size = (
            stream_routes.skeleton_cache.HEAD_SIZE
            + stream_routes.skeleton_cache.TAIL_SIZE
            + 4096
        )

        class GoodStreamer(_FakeStreamer):
            async def get_file_properties(self, message_id):
                return large_file

            async def yield_file(self, *args):
                yield b"x"

        class BadStreamer(_BadSessionStreamer):
            async def get_file_properties(self, message_id):
                return large_file

        request = _FakeRequest(
            method="GET",
            headers={"Range": "bytes=2100000-2100000"},
            http_range=slice(2100000, 2100001, 1),
            query={"download": "1"},
        )
        cooldowns = {}
        with (
            patch.object(stream_routes, "multi_clients", {0: bad_client, 1: good_client}),
            patch.object(stream_routes, "work_loads", {0: 0, 1: 1}),
            patch.object(
                stream_routes,
                "class_cache",
                {bad_client: BadStreamer(), good_client: GoodStreamer()},
            ),
            patch.object(stream_routes, "_total_active", 0),
            patch.object(stream_routes, "_ip_active", {}),
            patch.object(stream_routes, "_client_cooldowns", cooldowns),
        ):
            response = await stream_routes.media_streamer(request, 42, "abc")

        self.assertEqual(response.status, 206)
        self.assertIn(0, cooldowns)
        self.assertNotIn(1, cooldowns)

    async def test_skeleton_fetch_falls_back_to_second_client(self):
        bad_client = _FakeClient("bad")
        good_client = _FakeClient("good")
        calls = []
        cooldowns = {}

        async def fake_tail(message_id, file_size, tg_connect, file_id, index):
            calls.append(index)
            if index == 0:
                raise stream_routes.skeleton_cache.SkeletonFetchError("bad client")
            return b"abcdef"

        with (
            patch.object(stream_routes, "multi_clients", {0: bad_client, 1: good_client}),
            patch.object(stream_routes, "work_loads", {0: 0, 1: 0}),
            patch.object(
                stream_routes,
                "class_cache",
                {bad_client: _FakeStreamer(), good_client: _FakeStreamer()},
            ),
            patch.object(stream_routes, "_client_cooldowns", cooldowns),
            patch.object(stream_routes.skeleton_cache, "get_or_fetch_tail", fake_tail),
        ):
            body = await stream_routes._fetch_skeleton_with_fallback(
                "tail",
                42,
                "abc",
                1000,
                0,
            )

        self.assertEqual(body, b"abcdef")
        self.assertEqual(calls, [0, 1])
        self.assertIn(0, cooldowns)
        self.assertNotIn(1, cooldowns)

    async def test_download_head_forces_attachment_and_escapes_filename(self):
        response = await self._call_media_streamer(
            _FakeRequest(query={"download": "1"})
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Length"], "1000")
        self.assertNotIn("Content-Range", response.headers)
        disposition = response.headers["Content-Disposition"]
        self.assertTrue(disposition.startswith('attachment; filename="'))
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertIn("%E0%B0%A4%E0%B1%86", disposition)
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertNotIn('bad";', disposition)

    async def test_playback_head_keeps_inline_for_video(self):
        response = await self._call_media_streamer(_FakeRequest())

        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))

    async def test_suffix_range_is_normalised_before_headers(self):
        response = await self._call_media_streamer(
            _FakeRequest(
                headers={"Range": "bytes=-500"},
                http_range=slice(-500, None, 1),
                query={"download": "1"},
            )
        )

        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers["Content-Range"], "bytes 500-999/1000")
        self.assertEqual(response.headers["Content-Length"], "500")

    async def test_unsatisfiable_range_returns_416(self):
        with self.assertRaises(web.HTTPRequestRangeNotSatisfiable) as ctx:
            await self._call_media_streamer(
                _FakeRequest(
                    headers={"Range": "bytes=2000-"},
                    http_range=slice(2000, None, 1),
                    query={"download": "1"},
                )
            )

        self.assertEqual(ctx.exception.headers["Content-Range"], "bytes */1000")

    async def test_malformed_range_returns_416(self):
        with self.assertRaises(web.HTTPRequestRangeNotSatisfiable) as ctx:
            await self._call_media_streamer(
                _FakeRequest(
                    headers={"Range": "bad"},
                    http_range=ValueError("range not in acceptable format"),
                    query={"download": "1"},
                )
            )

        self.assertEqual(ctx.exception.headers["Content-Range"], "bytes */1000")

    def test_download_requests_do_not_use_probe_head_shortcut(self):
        self.assertFalse(
            stream_routes._should_serve_probe_head(
                download_request=True,
                range_header=False,
                from_bytes=0,
                file_size=stream_routes.skeleton_cache.HEAD_SIZE + 1,
            )
        )
        self.assertTrue(
            stream_routes._should_serve_probe_head(
                download_request=False,
                range_header=False,
                from_bytes=0,
                file_size=stream_routes.skeleton_cache.HEAD_SIZE + 1,
            )
        )


if __name__ == "__main__":
    unittest.main()

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

stream_routes = importlib.import_module("main.server.stream_routes")


class _FakeClient:
    pass


class _FakeFileId:
    unique_id = "abc123"
    file_size = 1000
    mime_type = "video/mp4"
    file_name = 'bad";\r\nname తెలుగు.mp4'


class _FakeStreamer:
    async def get_file_properties(self, message_id):
        return _FakeFileId()


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
        ):
            return await stream_routes.media_streamer(request, 42, "abc")

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

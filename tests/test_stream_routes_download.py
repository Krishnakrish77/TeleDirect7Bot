import os
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from aiohttp import web

from main.utils.custom_dl import MediaSessionUnavailable
from main.utils.file_properties import matches_secure_hash
from main.utils.hub_query import HubItem
from main.utils import cw_store, media_index, rec_store, wh_store

stream_routes = importlib.import_module("main.server.stream_routes")
render_template = importlib.import_module("main.utils.render_template")


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


def _video_item(**overrides) -> HubItem:
    data = {
        "message_id": 42,
        "secure_hash": "abc",
        "title": "Bad Santa",
        "year": 2014,
        "description": "",
        "tags": [],
        "duration": 2580,
        "file_size": 1000,
        "has_thumb": True,
        "quality": "720p",
        "file_name": "Castle.S06E14.720p.mkv",
        "media_kind": "video",
    }
    data.update(overrides)
    return HubItem(**data)


class _FakeRequest:
    def __init__(
        self,
        *,
        method="HEAD",
        headers=None,
        http_range=slice(None, None, 1),
        query=None,
        path="abc42",
    ):
        self.method = method
        self.headers = headers or {}
        self._http_range = http_range
        self.rel_url = SimpleNamespace(query=query or {})
        self.remote = "127.0.0.1"
        self.match_info = {"path": path}
        self.cookies = {}

    @property
    def http_range(self):
        if isinstance(self._http_range, Exception):
            raise self._http_range
        return self._http_range


class StreamRouteDownloadTest(unittest.IsolatedAsyncioTestCase):
    def test_legacy_hash_validation_stays_scoped_to_the_catalogued_value(self):
        self.assertTrue(
            matches_secure_hash(
                "abc123",
                "legacy-hash",
                catalogued_hash="legacy-hash",
            )
        )
        self.assertFalse(
            matches_secure_hash(
                "abc123",
                "legacy-hash",
                catalogued_hash="another-hash",
            )
        )

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

    async def test_stream_accepts_the_catalogued_legacy_hash_for_the_same_item(self):
        client = _FakeClient()
        legacy_item = _video_item(secure_hash="legacy-hash")
        with (
            patch.object(stream_routes, "multi_clients", {0: client}),
            patch.object(stream_routes, "class_cache", {client: _FakeStreamer()}),
            patch.object(stream_routes.media_index, "get_item", return_value=legacy_item),
        ):
            _, _, file_id = await stream_routes._file_for_index(0, 42, "legacy-hash")

        self.assertEqual(file_id.unique_id, "abc123")

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

    async def test_classic_watch_download_query_streams_attachment(self):
        client = _FakeClient()
        with (
            patch.object(stream_routes, "multi_clients", {0: client}),
            patch.object(stream_routes, "work_loads", {0: 0}),
            patch.object(stream_routes, "class_cache", {client: _FakeStreamer()}),
            patch.object(stream_routes, "_client_cooldowns", {}),
        ):
            response = await stream_routes.watch_handler(
                _FakeRequest(query={"download": "1"}, path="abc42")
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Length"], "1000")
        self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))

    async def test_watch_page_share_metadata_uses_series_episode(self):
        item = _video_item(
            title="Castle",
            series_key="castle",
            series_title="Castle",
            season=6,
            episode=14,
            episode_title="Bad Santa",
            episode_overview="Castle and Beckett investigate a case tied to a TV star.",
            poster_path="/castle-poster.jpg",
        )

        async def fake_get_file_ids(*_args):
            return _FakeFileId()

        with (
            patch.object(render_template, "get_file_ids", fake_get_file_ids),
            patch.object(render_template.media_index, "get_item", return_value=item),
            patch.object(render_template.media_index, "next_episode", return_value=None),
            patch.object(render_template.media_index, "episodes_for_series", return_value=[item]),
            patch.object(render_template.Var, "URL", "https://media.example/"),
            patch.object(render_template.share_meta.Var, "URL", "https://media.example/"),
        ):
            html = await render_template.render_page(42, "abc")

        self.assertIn('<title>Castle S06E14 · Bad Santa</title>', html)
        self.assertIn('property="og:title" content="Castle S06E14 · Bad Santa"', html)
        self.assertIn('property="og:description" content="Castle and Beckett investigate a case tied to a TV star."', html)
        self.assertIn('property="og:type" content="video.episode"', html)
        self.assertIn('property="og:url" content="https://media.example/watch/abc42"', html)
        self.assertIn('property="og:image" content="https://image.tmdb.org/t/p/w780/castle-poster.jpg"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)

    async def test_watch_page_rejects_abbreviated_secure_hash(self):
        async def fake_get_file_ids(*_args):
            return _FakeFileId()

        with patch.object(render_template, "get_file_ids", fake_get_file_ids):
            with self.assertRaises(render_template.InvalidHash):
                await render_template.render_page(42, "a")

    async def test_vlc_progress_carries_stable_session_start(self):
        item = _video_item(duration=1000)
        stream_routes._vlc_cw_debounce.clear()
        stream_routes._vlc_cw_session_started.clear()
        with (
            patch.object(media_index, "get_item", return_value=item),
            patch.object(cw_store, "upsert", new=AsyncMock()) as upsert,
        ):
            await stream_routes._vlc_track(7, 42, 100, 1000, "progress")
            await stream_routes._vlc_track(7, 42, 200, 1000, "progress")

        self.assertEqual(upsert.await_count, 2)
        first_started = upsert.await_args_list[0].args[-1]
        second_started = upsert.await_args_list[1].args[-1]
        self.assertEqual(first_started, second_started)

    async def test_vlc_completion_invalidates_recommendations(self):
        item = _video_item(duration=1000)
        with (
            patch.object(media_index, "get_item", return_value=item),
            patch.object(wh_store, "record", new=AsyncMock()) as record,
            patch.object(cw_store, "delete_one", new=AsyncMock()),
            patch.object(rec_store, "clear_cached", new=AsyncMock()) as clear_cached,
        ):
            await stream_routes._vlc_track(7, 42, 950, 1000, "complete")

        record.assert_awaited_once()
        clear_cached.assert_awaited_once_with(7)

    async def test_watch_page_share_metadata_ignores_hidden_catalogue_item(self):
        item = _video_item(
            title="Hidden Movie",
            overview="This should not leak into social previews.",
            poster_path="/hidden-poster.jpg",
            hidden=True,
        )

        async def fake_get_file_ids(*_args):
            return _FakeFileId()

        with (
            patch.object(render_template, "get_file_ids", fake_get_file_ids),
            patch.object(render_template.media_index, "get_item", return_value=item),
            patch.object(render_template.media_index, "next_episode", return_value=None),
            patch.object(render_template.Var, "URL", "https://media.example/"),
            patch.object(render_template.share_meta.Var, "URL", "https://media.example/"),
        ):
            html = await render_template.render_page(42, "abc")

        self.assertIn("<title>Hidden Movie</title>", html)
        self.assertNotIn('property="og:title" content="Hidden Movie"', html)
        self.assertNotIn("This should not leak into social previews.", html)
        self.assertNotIn("hidden-poster.jpg", html)

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

"""Tests for admin-side Wyzie subtitle search/fetch endpoints."""

import asyncio
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import media_index, wyzie_subtitles

admin_routes = importlib.import_module("main.server.admin_routes")


class _AdminUser(dict):
    pass


def _request(json_body=None):
    return SimpleNamespace(
        match_info={"id": "42"},
        query={"language": "en"},
        json=AsyncMock(return_value=json_body or {"id": "c1"}),
        headers={},
    )


class AdminSubtitleEndpointsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        wyzie_subtitles._cache.clear()
        self.admin = {"sub": "7"}
        from tests.test_admin_catalogue import _item as make_item
        self.item = make_item(42, tmdb_id=None)
        self.item.imdb_id = "tt3986620"
        self.item.season = 4
        self.item.episode = 4
        self.item.file_name = "Silicon.Valley.S04E04.mkv"
        self.item.title = "Silicon Valley"

    async def test_search_returns_provider_results(self):
        results = [{"id": "c1", "label": "English", "language": "en"}]
        with patch.object(admin_routes.media_index, "get_item", return_value=self.item), patch.object(
            admin_routes.wyzie_subtitles, "search", AsyncMock(return_value=results),
        ) as search_mock, patch.object(
            admin_routes, "_require_api_admin", return_value=self.admin,
        ):
            response = await admin_routes.api_admin_item_subtitle_search(_request())
        payload = json.loads(response.text)
        self.assertEqual(payload["results"], results)
        search_mock.assert_awaited_once_with(7, self.item, "en")

    async def test_search_rejects_audio_items(self):
        self.item.media_kind = "audio"
        with patch.object(admin_routes.media_index, "get_item", return_value=self.item), patch.object(
            admin_routes, "_require_api_admin", return_value=self.admin,
        ):
            response = await admin_routes.api_admin_item_subtitle_search(_request())
        self.assertEqual(response.status, 400)

    async def test_fetch_attaches_durable_sidecar(self):
        vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi"
        candidate = {"id": "c1", "label": "English", "language": "en", "format": "srt", "fileName": "en.srt"}
        sent = []

        async def fake_send(*_args, **_kwargs):
            sent.append(True)
            return SimpleNamespace(id=999)

        captured_sidecar = []

        async def fake_attach(mid, sidecar):
            captured_sidecar.append(sidecar)
            return True

        with patch.object(admin_routes.media_index, "get_item", return_value=self.item), patch.object(
            admin_routes, "_require_api_admin", return_value=self.admin,
        ), patch.object(
            wyzie_subtitles, "download", AsyncMock(return_value=(b"1\n00:00:00,000 --> 00:00:01,000\nHi", candidate)),
        ) as download_mock, patch.object(
            admin_routes.StreamBot, "send_document", fake_send,
        ), patch.object(
            admin_routes.media_index, "attach_subtitle", fake_attach,
        ), patch.object(
            admin_routes.media_index, "_store_upsert", AsyncMock(),
        ) as upsert_mock:
            response = await admin_routes.api_admin_item_subtitle_fetch(_request({"id": "c1"}))

        payload = json.loads(response.text)
        self.assertTrue(payload["ok"])
        self.assertIn("Attached", payload["message"])
        download_mock.assert_awaited_once_with(7, self.item, "c1")
        self.assertEqual(len(captured_sidecar), 1)
        self.assertEqual(captured_sidecar[0].bin_message_id, 999)
        self.assertIn("WEBVTT", vtt.decode())
        upsert_mock.assert_awaited_once()

    async def test_fetch_rolls_back_bin_message_when_video_gone(self):
        candidate = {"id": "c1", "label": "English", "language": "en", "format": "srt", "fileName": "en.srt"}
        deleted = []

        async def fake_send(*_args, **_kwargs):
            return SimpleNamespace(id=1000)

        async def fake_delete(*args, **_kwargs):
            deleted.append(args[-1] if isinstance(args[-1], int) else args[1] if len(args) > 1 else args[0])
            return True

        with patch.object(admin_routes.media_index, "get_item", return_value=self.item), patch.object(
            admin_routes, "_require_api_admin", return_value=self.admin,
        ), patch.object(
            wyzie_subtitles, "download", AsyncMock(return_value=(b"data", candidate)),
        ), patch.object(
            admin_routes.StreamBot, "send_document", fake_send,
        ), patch.object(
            admin_routes.media_index, "attach_subtitle", AsyncMock(return_value=False),
        ), patch.object(admin_routes.StreamBot, "delete_messages", fake_delete):
            response = await admin_routes.api_admin_item_subtitle_fetch(_request({"id": "c1"}))

        self.assertEqual(response.status, 404)
        self.assertEqual(deleted, [1000])  # BIN upload rolled back

    async def test_fetch_maps_provider_errors(self):
        candidate = {"id": "c1", "label": "English", "language": "en", "format": "srt", "fileName": "en.srt"}
        cases = [
            (wyzie_subtitles.QuotaUnavailable("Daily limit"), 429),
            (wyzie_subtitles.WyzieError("Subtitle provider is unavailable"), 503),
            (wyzie_subtitles.WyzieError("Daily attach limit reached"), 429),
        ]
        for error, expected_status in cases:
            with self.subTest(error=str(error)):
                with patch.object(admin_routes.media_index, "get_item", return_value=self.item), patch.object(
                    admin_routes, "_require_api_admin", return_value=self.admin,
                ), patch.object(
                    wyzie_subtitles, "download", AsyncMock(side_effect=error),
                ):
                    response = await admin_routes.api_admin_item_subtitle_fetch(_request({"id": "c1"}))
                self.assertEqual(response.status, expected_status)

    async def test_fetch_rejects_invalid_selection(self):
        with patch.object(admin_routes.media_index, "get_item", return_value=self.item), patch.object(
            admin_routes, "_require_api_admin", return_value=self.admin,
        ):
            response = await admin_routes.api_admin_item_subtitle_fetch(_request({"id": ""}))
        self.assertEqual(response.status, 400)

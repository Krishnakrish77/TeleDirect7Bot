import importlib
import os
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

stats_routes = importlib.import_module("main.server.stats_routes")
wh_routes = importlib.import_module("main.server.wh_routes")
from main.utils import media_index
from main.utils.hub_query import HubItem


def _item() -> HubItem:
    return HubItem(
        message_id=101,
        secure_hash="hash",
        title="Example Movie",
        year=2026,
        description="",
        tags=[],
        duration=3600,
        file_size=100,
        has_thumb=False,
        file_name="Example.Movie.mkv",
        movie_key="example-movie",
        media_kind="video",
    )


class _Request:
    def __init__(self, key: str, body: dict | None = None):
        self.match_info = {"key": key}
        self._body = body or {}

    async def json(self):
        return self._body


class StatsRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_stats_has_no_activity(self):
        with (
            patch.object(stats_routes.cw_store, "get_all", new=AsyncMock(return_value={})),
            patch.object(stats_routes.wh_store, "get_recent", new=AsyncMock(return_value=[])),
            patch.object(stats_routes.wh_store, "get_events", new=AsyncMock(return_value=[])),
        ):
            payload = await stats_routes._stats_payload(1)

        self.assertFalse(payload["has_activity"])
        self.assertEqual(payload["in_progress"], 0)

    async def test_stats_include_in_progress_time_and_label_current_title(self):
        previous = dict(media_index._items)
        try:
            media_index._items.clear()
            media_index._items[101] = _item()
            with (
                patch.object(stats_routes.cw_store, "get_all", new=AsyncMock(return_value={
                    "hash101": {"pos": 600, "dur": 3600, "t": 1_700_000_000_000, "startedAt": 1_700_000_000_000},
                })),
                patch.object(stats_routes.wh_store, "get_recent", new=AsyncMock(return_value=[])),
                patch.object(stats_routes.wh_store, "get_events", new=AsyncMock(return_value=[])),
            ):
                payload = await stats_routes._stats_payload(1)

            self.assertTrue(payload["has_activity"])
            self.assertEqual(payload["in_progress"], 1)
            self.assertEqual(payload["total_seconds"], 600)
            self.assertEqual(payload["top_title_label"], "Continue watching")
            self.assertEqual(payload["top_title"]["title"], "Example Movie")
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    async def test_legacy_summary_and_new_events_are_not_double_counted(self):
        previous = dict(media_index._items)
        try:
            media_index._items.clear()
            media_index._items[101] = _item()
            watched_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
            with (
                patch.object(stats_routes.cw_store, "get_all", new=AsyncMock(return_value={})),
                patch.object(stats_routes.wh_store, "get_recent", new=AsyncMock(return_value=[{
                    "cw_key": "hash101", "play_count": 3, "watched_at": watched_at,
                }])),
                patch.object(stats_routes.wh_store, "get_events", new=AsyncMock(return_value=[{
                    "cw_key": "hash101", "watched_at": watched_at,
                }])),
            ):
                payload = await stats_routes._stats_payload(1)

            self.assertEqual(payload["total_plays"], 3)
            self.assertEqual(payload["total_seconds"], 3 * 3600)
        finally:
            media_index._items.clear()
            media_index._items.update(previous)

    async def test_watch_history_rejects_unknown_keys_and_uses_catalogue_title(self):
        previous = dict(media_index._items)
        original_get_user = wh_routes.get_user
        try:
            media_index._items.clear()
            media_index._items[101] = _item()
            wh_routes.get_user = lambda _request: {"sub": 7}
            rejected = await wh_routes.api_record(_Request("invented999", {"title": "Fake"}))
            self.assertEqual(rejected.status, 404)

            with (
                patch.object(wh_routes.wh_store, "record", new=AsyncMock()) as record,
                patch.object(wh_routes.rec_store, "clear_cached", new=AsyncMock()) as clear_cached,
            ):
                accepted = await wh_routes.api_record(_Request("hash101", {"title": "Fake"}))
            self.assertEqual(accepted.status, 200)
            record.assert_awaited_once_with(7, "hash101", "Example Movie")
            clear_cached.assert_awaited_once_with(7)
        finally:
            wh_routes.get_user = original_get_user
            media_index._items.clear()
            media_index._items.update(previous)

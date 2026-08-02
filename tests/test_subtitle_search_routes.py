import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

spa_routes = importlib.import_module("main.server.spa_routes")


class SubtitleSearchRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_provider_id_gets_one_title_metadata_recovery_attempt(self):
        item = SimpleNamespace(
            message_id=42,
            secure_hash="hash",
            hidden=False,
            media_kind="video",
            imdb_id="",
            tmdb_id=None,
        )

        async def enrich(_message_id):
            item.tmdb_id = 286217
            return True

        with (
            patch.object(spa_routes.media_index, "get_item", return_value=item),
            patch.object(spa_routes.media_index, "enrich_one", AsyncMock(side_effect=enrich)) as enrich_one,
        ):
            result = await spa_routes._subtitle_search_item("hash42")

        self.assertIs(result, item)
        enrich_one.assert_awaited_once_with(42)
        self.assertEqual(result.tmdb_id, 286217)

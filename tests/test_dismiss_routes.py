import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

dismiss_routes = importlib.import_module("main.server.dismiss_routes")


class _Request:
    async def json(self):
        return {"tmdb_id": 123, "kind": "tv"}


class DismissRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_undismiss_uses_request_kind_and_invalidates_recommendations(self):
        with (
            patch.object(dismiss_routes, "get_user", return_value={"sub": 7}),
            patch.object(dismiss_routes.dismissed_store, "undismiss", new=AsyncMock()) as undismiss,
            patch.object(dismiss_routes.rec_store, "clear_cached", new=AsyncMock()) as clear_cached,
            patch.object(dismiss_routes.ai_rec_store, "clear_cached", new=AsyncMock()) as clear_ai_cached,
        ):
            response = await dismiss_routes.api_undismiss(_Request())

        self.assertEqual(response.status, 200)
        undismiss.assert_awaited_once_with(7, 123, "tv")
        clear_cached.assert_awaited_once_with(7)
        clear_ai_cached.assert_awaited_once_with(7)

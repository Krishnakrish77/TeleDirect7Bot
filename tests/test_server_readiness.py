import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server import mongo_readiness_middleware


class MongoReadinessMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_subtitle_requests_remain_available_during_reconnect(self):
        request = make_mocked_request("GET", "/sub/hash123/list.json")
        handler = AsyncMock(return_value=web.json_response([]))

        with patch("main.utils.media_index.store_ready", return_value=False):
            response = await mongo_readiness_middleware(request, handler)

        handler.assert_awaited_once_with(request)
        self.assertEqual(response.status, 200)

    async def test_catalogue_routes_stay_in_maintenance_during_reconnect(self):
        request = make_mocked_request("GET", "/api/app/watch/example")
        handler = AsyncMock(return_value=web.json_response({"unexpected": True}))

        with patch("main.utils.media_index.store_ready", return_value=False):
            response = await mongo_readiness_middleware(request, handler)

        handler.assert_not_awaited()
        self.assertEqual(response.status, 503)


if __name__ == "__main__":
    unittest.main()

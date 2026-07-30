import importlib
import json
import os
import unittest
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

ai_rec_routes = importlib.import_module("main.server.ai_rec_routes")


class _Request:
    def __init__(self, query=None):
        self.query = query or {}


class AiRecommendationsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_plain_open_returns_a_valid_cache_without_spending_a_token(self):
        cached = {"items": [{"href": "/cached"}], "cached": True}
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.ai_rec, "get_cached_ai_recommendations", new=AsyncMock(return_value=cached)),
            patch.object(ai_rec_routes, "_take_token") as take_token,
            patch.object(ai_rec_routes.ai_rec, "get_ai_recommendations", new=AsyncMock()) as recommendations,
        ):
            response = await ai_rec_routes.ai_recommendations(_Request())

        self.assertEqual(json.loads(response.text), cached)
        take_token.assert_not_called()
        recommendations.assert_not_awaited()

    async def test_plain_cache_miss_uses_the_bounded_agent_and_caches_its_result(self):
        result = {"items": [{"href": "/tailored"}]}
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.ai_rec, "get_cached_ai_recommendations", new=AsyncMock(return_value=None)),
            patch.object(ai_rec_routes, "_take_token", return_value=True) as take_token,
            patch.object(ai_rec_routes.ai_rec, "get_ai_recommendations", new=AsyncMock(return_value=result)) as recommendations,
        ):
            response = await ai_rec_routes.ai_recommendations(_Request())

        self.assertEqual(json.loads(response.text), result)
        take_token.assert_called_once_with(7)
        recommendations.assert_awaited_once_with(7, refresh=False, agentic=True)

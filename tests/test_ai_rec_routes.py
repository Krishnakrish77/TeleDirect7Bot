import importlib
import json
import os
import unittest
from unittest.mock import ANY, AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

ai_rec_routes = importlib.import_module("main.server.ai_rec_routes")


class _Request:
    def __init__(self, query=None):
        self.query = query or {}


class _StreamRequest(_Request):
    def __init__(self, body):
        super().__init__()
        self._body = body

    async def json(self):
        return self._body


class _StreamResponse:
    def __init__(self, *args, **kwargs):
        self.status = kwargs.get("status", 200)
        self.headers = kwargs.get("headers", {})
        self.writes = []

    async def prepare(self, _request):
        return self

    async def write(self, body):
        self.writes.append(body)

    async def write_eof(self):
        return None


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

    async def test_initial_stream_uses_cache_then_emits_one_result(self):
        cached = {"items": [{"href": "/cached"}], "cached": True}
        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.web, "StreamResponse", _StreamResponse),
            patch.object(ai_rec_routes.ai_rec, "get_cached_ai_recommendations", new=AsyncMock(return_value=cached)),
            patch.object(ai_rec_routes, "_take_token") as take_token,
            patch.object(ai_rec_routes.ai_rec, "get_ai_recommendations", new=AsyncMock()) as recommendations,
        ):
            response = await ai_rec_routes.ai_recommendations_stream(_StreamRequest({"initial": True}))

        self.assertEqual(b"".join(response.writes).decode(), 'event: result\ndata: {"items":[{"href":"/cached"}],"cached":true}\n\n')
        take_token.assert_not_called()
        recommendations.assert_not_awaited()

    async def test_initial_stream_cache_miss_forwards_live_agent_status(self):
        async def generate(_uid, *, progress, **_kwargs):
            await progress("Searching your library")
            return {"items": [{"href": "/tailored"}]}

        with (
            patch.object(ai_rec_routes, "get_user", return_value={"sub": 7}),
            patch.object(ai_rec_routes.gemini, "available", return_value=True),
            patch.object(ai_rec_routes.web, "StreamResponse", _StreamResponse),
            patch.object(ai_rec_routes.ai_rec, "get_cached_ai_recommendations", new=AsyncMock(return_value=None)),
            patch.object(ai_rec_routes, "_take_token", return_value=True),
            patch.object(ai_rec_routes.ai_rec, "get_ai_recommendations", new=AsyncMock(side_effect=generate)) as recommendations,
        ):
            response = await ai_rec_routes.ai_recommendations_stream(_StreamRequest({"initial": True}))

        events = b"".join(response.writes).decode()
        self.assertIn('event: status\ndata: {"message":"Searching your library"}', events)
        self.assertIn('event: result\ndata: {"items":[{"href":"/tailored"}]}', events)
        recommendations.assert_awaited_once_with(7, query=None, refresh=False, agentic=True, progress=ANY)

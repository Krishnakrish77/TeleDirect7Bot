import importlib
import json
import os
import unittest
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

rec_feedback_routes = importlib.import_module("main.server.rec_feedback_routes")


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class RecommendationFeedbackRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_only_valid_events_and_invalidates_after_open(self):
        request = _Request({"events": [
            {"action": "impression", "source": "home", "itemId": "series:show", "position": 0},
            {"action": "open", "source": "ai", "itemId": "movie:film", "tmdbId": 99, "tmdbKind": "movie"},
            {"action": "oops", "source": "ai", "itemId": "bad"},
        ]})
        with (
            patch.object(rec_feedback_routes, "get_user", return_value={"sub": 7}),
            patch.object(rec_feedback_routes.rec_feedback_store, "record_many", new=AsyncMock(return_value=2)) as record_many,
            patch.object(rec_feedback_routes.rec_store, "clear_cached", new=AsyncMock()) as clear_recs,
            patch.object(rec_feedback_routes.ai_rec_store, "clear_cached", new=AsyncMock()) as clear_ai,
        ):
            response = await rec_feedback_routes.api_recommendation_events(request)

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["accepted"], 2)
        recorded = record_many.await_args.args[1]
        self.assertEqual([event["action"] for event in recorded], ["impression", "open"])
        self.assertEqual(recorded[1]["tmdb_id"], 99)
        clear_recs.assert_awaited_once_with(7)
        clear_ai.assert_awaited_once_with(7)

    async def test_rejects_unauthenticated_events(self):
        with patch.object(rec_feedback_routes, "get_user", return_value=None):
            response = await rec_feedback_routes.api_recommendation_events(_Request({"events": []}))
        self.assertEqual(response.status, 401)

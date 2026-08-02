import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import wyzie_subtitles


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return [{
            "id": "candidate-1",
            "url": "https://sub.wyzie.io/c/example/id/candidate-1?format=srt",
            "format": "srt",
            "language": "en",
            "display": "English",
        }]


class _Session:
    def __init__(self):
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, _url, *, params):
        self.params = params
        return _Response()


class WyzieSubtitleSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_queries_all_sources_available_to_the_key(self):
        session = _Session()
        item = SimpleNamespace(
            message_id=42,
            imdb_id="tt3659388",
            tmdb_id=None,
            season=None,
            episode=None,
        )
        wyzie_subtitles._cache.clear()
        with (
            patch.object(wyzie_subtitles.Var, "WYZIE_API_KEY", "test-key"),
            patch.object(wyzie_subtitles, "_reserve", AsyncMock()),
            patch.object(wyzie_subtitles, "ClientSession", return_value=session),
        ):
            results = await wyzie_subtitles.search(7, item)

        self.assertEqual(session.params["source"], "all")
        self.assertEqual(session.params["id"], "tt3659388")
        self.assertEqual(results, [{
            "id": "candidate-1", "format": "srt", "language": "en", "label": "English",
            "release": "", "fileName": "subtitle.srt", "hearingImpaired": False, "source": "",
        }])

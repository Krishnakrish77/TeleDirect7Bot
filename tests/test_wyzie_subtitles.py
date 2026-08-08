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

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else [{
            "id": "candidate-1",
            "url": "https://sub.wyzie.io/c/example/id/candidate-1?format=srt",
            "format": "srt",
            "language": "en",
            "display": "English",
        }]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.payload


class _Session:
    def __init__(self, response=None):
        self.params = None
        self.response = response or _Response()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, _url, *, params):
        self.params = params
        return self.response


class WyzieSubtitleSearchTest(unittest.IsolatedAsyncioTestCase):
    def test_direct_opensubtitles_result_is_kept(self):
        candidate = wyzie_subtitles._candidate({
            "id": "1956307067",
            "url": "https://d1.opensubtitles.org/en/download/subencoding-utf8/src-api/v1/file/1956307067",
            "format": "srt",
            "language": "en",
            "display": "English",
            "source": "charlie",
        })

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], "1956307067")
        self.assertEqual(candidate["format"], "srt")

    async def test_search_uses_the_default_source_before_broad_fallback(self):
        session = _Session()
        item = SimpleNamespace(
            message_id=42,
            imdb_id="tt3659388",
            tmdb_id=None,
            season=2,
            episode=5,
        )
        wyzie_subtitles._cache.clear()
        with (
            patch.object(wyzie_subtitles.Var, "WYZIE_API_KEY", "test-key"),
            patch.object(wyzie_subtitles, "_reserve", AsyncMock()),
            patch.object(wyzie_subtitles, "ClientSession", return_value=session),
        ):
            results = await wyzie_subtitles.search(7, item)

        self.assertNotIn("source", session.params)
        self.assertEqual(session.params["id"], "tt3659388")
        self.assertEqual(session.params["season"], "2")
        self.assertEqual(session.params["episode"], "5")
        self.assertEqual(results, [{
            "id": "candidate-1", "format": "srt", "language": "en", "label": "English",
            "release": "", "fileName": "subtitle.srt", "hearingImpaired": False, "source": "",
        }])

    async def test_search_retries_all_sources_after_default_returns_no_results(self):
        default_source = _Session(_Response([]))
        all_sources = _Session()
        item = SimpleNamespace(message_id=42, imdb_id="tt4154796", tmdb_id=None, season=None, episode=None)
        wyzie_subtitles._cache.clear()
        with (
            patch.object(wyzie_subtitles.Var, "WYZIE_API_KEY", "test-key"),
            patch.object(wyzie_subtitles, "_reserve", AsyncMock()),
            patch.object(wyzie_subtitles, "ClientSession", side_effect=[default_source, all_sources]),
        ):
            results = await wyzie_subtitles.search(7, item)

        self.assertNotIn("source", default_source.params)
        self.assertEqual(all_sources.params["source"], "all")
        self.assertEqual([result["id"] for result in results], ["candidate-1"])

    async def test_search_does_not_cache_empty_provider_results(self):
        item = SimpleNamespace(message_id=42, imdb_id="tt4154796", tmdb_id=None, season=None, episode=None)
        wyzie_subtitles._cache.clear()
        sessions = [_Session(_Response([])) for _ in range(4)]
        with (
            patch.object(wyzie_subtitles.Var, "WYZIE_API_KEY", "test-key"),
            patch.object(wyzie_subtitles, "_reserve", AsyncMock()),
            patch.object(wyzie_subtitles, "ClientSession", side_effect=sessions),
        ):
            self.assertEqual(await wyzie_subtitles.search(7, item), [])
            self.assertEqual(await wyzie_subtitles.search(7, item), [])

        self.assertNotIn((42, ""), wyzie_subtitles._cache)
        self.assertEqual(sessions[0].params.get("source"), None)
        self.assertEqual(sessions[1].params["source"], "all")
        self.assertEqual(sessions[2].params.get("source"), None)
        self.assertEqual(sessions[3].params["source"], "all")

    def test_release_like_the_video_is_ranked_first_without_dropping_others(self):
        item = SimpleNamespace(
            file_name="The.Show.S01E02.1080p.WEB-DL.mkv",
            series_title="The Show",
            title="The Show",
        )
        candidates = [
            {"id": "wrong", "release": "Another.Show.S01E04.720p", "fileName": "another.srt"},
            {"id": "match", "release": "The.Show.S01E02.1080p.WEB-DL", "fileName": "the.show.srt"},
        ]

        ranked = wyzie_subtitles._rank_release_matches(item, candidates)

        self.assertEqual([candidate["id"] for candidate in ranked], ["match", "wrong"])

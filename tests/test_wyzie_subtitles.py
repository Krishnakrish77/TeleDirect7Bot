import os
import time
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

    def test_download_status_classification_sends_expired_403_links_to_research(self):
        """A dead cached link must go down the re-search path, not surface
        the generic 'no longer available' error. OpenSubtitles mirrors
        answer expired signed URLs with 403 as often as 404/410."""
        ok = [200]
        transient = [429, 500, 502, 503, 504]
        gone = [403, 404, 410]
        error = [401, 406, 451]

        for status in ok:
            self.assertEqual(wyzie_subtitles._status_class(status), "ok", status)
        for status in transient:
            self.assertEqual(wyzie_subtitles._status_class(status), "transient", status)
        for status in gone:
            self.assertEqual(wyzie_subtitles._status_class(status), "gone", status)
        for status in error:
            self.assertEqual(wyzie_subtitles._status_class(status), "error", status)

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
            patch.object(wyzie_subtitles, "_check_quota", AsyncMock()),
            patch.object(wyzie_subtitles, "_commit_quota", AsyncMock()),
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
            patch.object(wyzie_subtitles, "_check_quota", AsyncMock()),
            patch.object(wyzie_subtitles, "_commit_quota", AsyncMock()),
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
            patch.object(wyzie_subtitles, "_check_quota", AsyncMock()),
            patch.object(wyzie_subtitles, "_commit_quota", AsyncMock()),
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


class WyzieDownloadReliabilityTest(unittest.IsolatedAsyncioTestCase):
    def _item(self, **overrides):
        base = SimpleNamespace(message_id=42, imdb_id="tt3659388", tmdb_id=None, season=None, episode=None)
        return SimpleNamespace(**{**base.__dict__, **overrides})

    def _seed_cache(self, item):
        wyzie_subtitles._cache.clear()
        wyzie_subtitles._cache[item.message_id] = {
            "": (time.monotonic(), [{
                "id": "candidate-1", "url": "https://sub.wyzie.io/c/example/id/candidate-1?format=srt",
                "format": "srt", "language": "en", "label": "English",
                "release": "", "fileName": "subtitle.srt", "hearingImpaired": False, "source": "",
            }]),
        }

    async def test_failed_download_does_not_consume_quota(self):
        item = self._item()
        self._seed_cache(item)
        committed = []

        async def fail_download(url):
            raise wyzie_subtitles._TransientDownloadError("timed out")

        async def commit(user_id, action, item_id=None):
            committed.append(action)

        with patch.object(wyzie_subtitles, "_check_quota", AsyncMock()), patch.object(
            wyzie_subtitles, "_commit_quota", commit,
        ), patch.object(wyzie_subtitles, "_download_bytes", fail_download), patch.object(
            wyzie_subtitles.asyncio, "sleep", AsyncMock(),
        ):
            with self.assertRaises(wyzie_subtitles.WyzieError):
                await wyzie_subtitles.download(7, item, "candidate-1")
        self.assertEqual(committed, [])  # failure is free to retry

    async def test_transient_download_failure_retries_once_then_succeeds(self):
        item = self._item()
        self._seed_cache(item)
        attempts = []

        async def flaky_download(url):
            attempts.append(url)
            if len(attempts) == 1:
                raise wyzie_subtitles._TransientDownloadError("connection reset")
            return b"WEBVTT"

        async def noop(*_args):
            return None

        with patch.object(wyzie_subtitles, "_check_quota", AsyncMock()), patch.object(
            wyzie_subtitles, "_commit_quota", noop,
        ), patch.object(wyzie_subtitles, "_download_bytes", flaky_download), patch.object(
            wyzie_subtitles.asyncio, "sleep", AsyncMock(),
        ) as sleep_mock:
            data, _found = await wyzie_subtitles.download(7, item, "candidate-1")
        self.assertEqual(data, b"WEBVTT")
        self.assertEqual(len(attempts), 2)
        sleep_mock.assert_awaited_once()

    async def test_gone_download_link_refreshes_cache_and_succeeds(self):
        """A dead cached URL (404) must re-search for fresh links, not error."""
        item = self._item()
        self._seed_cache(item)  # cached URL is stale — provider will 404 it
        searches = []

        async def fake_search(user_id, item_arg, language=""):
            searches.append(item_arg.message_id)
            # Fresh search replaces the cache with a working URL under the
            # same candidate id.
            wyzie_subtitles._cache[item_arg.message_id] = {
                "": (time.monotonic(), [{
                    "id": "candidate-1", "url": "https://sub.wyzie.io/fresh", "format": "srt",
                    "language": "en", "label": "English", "release": "",
                    "fileName": "subtitle.srt", "hearingImpaired": False, "source": "",
                }]),
            }
            return []

        calls: list[str] = []

        async def download_by_url(url):
            calls.append(url)
            if url.endswith("candidate-1?format=srt"):
                raise wyzie_subtitles._LinkGoneError("download link expired")  # via 404 path
            return b"WEBVTT"

        async def noop(*_args):
            return None

        with patch.object(wyzie_subtitles, "search", fake_search), patch.object(
            wyzie_subtitles, "_check_quota", AsyncMock(),
        ), patch.object(wyzie_subtitles, "_commit_quota", noop), patch.object(
            wyzie_subtitles, "_download_bytes", download_by_url,
        ):
            data, found = await wyzie_subtitles.download(7, item, "candidate-1")

        self.assertEqual(data, b"WEBVTT")
        self.assertEqual(searches, [42])  # exactly one re-search
        self.assertEqual(calls[-1], "https://sub.wyzie.io/fresh")  # fresh URL used
        self.assertEqual(found["id"], "candidate-1")

    async def test_gone_link_twice_surfaces_friendly_error(self):
        item = self._item()
        self._seed_cache(item)
        searches = []

        async def fake_search(user_id, item_arg, language=""):
            searches.append(item_arg.message_id)
            return []

        async def always_gone(url):
            raise wyzie_subtitles._LinkGoneError("download link expired")

        async def noop(*_args):
            return None

        with patch.object(wyzie_subtitles, "search", fake_search), patch.object(
            wyzie_subtitles, "_check_quota", AsyncMock(),
        ), patch.object(wyzie_subtitles, "_commit_quota", noop), patch.object(
            wyzie_subtitles, "_download_bytes", always_gone,
        ):
            with self.assertRaises(wyzie_subtitles.WyzieError) as caught:
                await wyzie_subtitles.download(7, item, "candidate-1")
        self.assertIn("no longer offered", str(caught.exception))
        self.assertEqual(len(searches), 1)  # bounded: one recovery, no loops

    async def test_expired_search_cache_researches_transparently(self):
        item = self._item()
        wyzie_subtitles._cache.clear()  # nothing cached — old code raised "expired"
        searched = []

        async def fake_search(user_id, item_arg, language=""):
            searched.append(item_arg.message_id)
            self._seed_cache(item_arg)
            return []

        async def noop(*_args):
            return None

        with patch.object(wyzie_subtitles, "search", fake_search), patch.object(
            wyzie_subtitles, "_check_quota", AsyncMock(),
        ), patch.object(wyzie_subtitles, "_commit_quota", noop), patch.object(
            wyzie_subtitles, "_download_bytes", AsyncMock(return_value=b"WEBVTT"),
        ):
            data, _found = await wyzie_subtitles.download(7, item, "candidate-1")
        self.assertEqual(data, b"WEBVTT")
        self.assertEqual(searched, [42])  # re-search happened inside download

    async def test_quota_check_raises_retryable_daily_error(self):
        item = self._item()
        self._seed_cache(item)
        downloads = []

        async def count_check(user_id, action, item_id=None):
            raise wyzie_subtitles.QuotaUnavailable("Daily attach limit reached. Try again tomorrow.")

        async def never_called(url):
            downloads.append(url)
            return b""

        with patch.object(wyzie_subtitles, "_check_quota", count_check), patch.object(
            wyzie_subtitles, "_download_bytes", never_called,
        ):
            with self.assertRaises(wyzie_subtitles.QuotaUnavailable) as caught:
                await wyzie_subtitles.download(7, item, "candidate-1")
        self.assertEqual(caught.exception.retry_after, 3600)
        self.assertEqual(downloads, [])  # no download attempt when quota is out

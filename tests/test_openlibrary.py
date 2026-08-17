import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import openlibrary
from main.utils.openlibrary import cover_url, normalise_search_doc


class _Response:
    def __init__(self, status, data=None):
        self.status = status
        self._data = data or {"docs": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"unexpected status {self.status}")

    async def json(self, **_kwargs):
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return next(self.responses)


def test_normalise_search_result_and_client_round_trip():
    raw = {
        "key": "/works/OL12345W",
        "title": "The Example Book",
        "author_name": ["Ada Author", "Ben Writer"],
        "first_publish_year": 1999,
        "cover_i": 42,
        "isbn": ["9781234567890"],
        "publisher": ["Example Press"],
        "language": ["eng"],
        "number_of_pages_median": 321,
        "first_sentence": "An opening sentence.",
        "subject": ["Science fiction", "Space opera"],
    }
    candidate = normalise_search_doc(raw)

    assert candidate == {
        "key": "/works/OL12345W", "title": "The Example Book",
        "authors": ["Ada Author", "Ben Writer"], "year": 1999,
        "coverId": 42, "isbn": "9781234567890", "publisher": "Example Press",
        "language": "eng", "pageCount": 321, "description": "An opening sentence.",
        "subjects": ["Science fiction", "Space opera"],
    }
    assert normalise_search_doc(candidate) == candidate
    assert cover_url(42) == "https://covers.openlibrary.org/b/id/42-L.jpg"


def test_normalise_rejects_non_work_keys():
    assert normalise_search_doc({"key": "/books/OL12M", "title": "Nope"}) is None
    assert normalise_search_doc("not a result") is None


class OpenLibrarySearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_retries_one_transient_upstream_response(self):
        session = _Session([
            _Response(503),
            _Response(200, {"docs": [{"key": "/works/OL12345W", "title": "The Example Book"}]}),
        ])
        sleep = AsyncMock()

        with patch.object(openlibrary.aiohttp, "ClientSession", return_value=session), patch.object(openlibrary.asyncio, "sleep", sleep):
            items = await openlibrary.search_books("Example Book")

        self.assertEqual(session.calls, 2)
        sleep.assert_awaited_once_with(openlibrary._RETRY_DELAY_SECONDS)
        self.assertEqual(items, [{
            "key": "/works/OL12345W", "title": "The Example Book", "authors": [], "year": None,
            "coverId": 0, "isbn": "", "publisher": "", "language": "", "pageCount": 0,
            "description": "", "subjects": [],
        }])

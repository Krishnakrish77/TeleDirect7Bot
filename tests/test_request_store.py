import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import media_index, request_store


class RequestStoreTests(unittest.TestCase):
    def test_explicit_season_normalisation_is_bounded_and_deduplicated(self):
        self.assertEqual(request_store._as_seasons([3, "1", 3, 0, -1, "no", 201, 2]), [1, 2, 3])

    def test_library_availability_is_tmdb_identity_based(self):
        original = media_index._items
        try:
            media_index._items = {
                1: SimpleNamespace(hidden=False, tmdb_id=10, tmdb_kind="movie", season=None),
                2: SimpleNamespace(hidden=False, tmdb_id=22, tmdb_kind="tv", season=2),
                3: SimpleNamespace(hidden=False, tmdb_id=22, tmdb_kind="tv", season=1),
                4: SimpleNamespace(hidden=True, tmdb_id=22, tmdb_kind="tv", season=3),
            }
            self.assertEqual(request_store.library_availability(10, "movie"), {"inLibrary": True, "availableSeasons": []})
            self.assertEqual(request_store.library_availability(22, "tv"), {"inLibrary": True, "availableSeasons": [1, 2]})
            self.assertEqual(request_store.library_availability(99, "movie"), {"inLibrary": False, "availableSeasons": []})
        finally:
            media_index._items = original

    def test_list_for_user_returns_safe_documents(self):
        class Cursor:
            async def to_list(self, length):
                self.length = length
                return [{
                    "request_id": "a" * 32, "tmdb_id": 1, "kind": "movie", "title": "Example",
                    "requested_seasons": [], "available_seasons": [], "status": "pending",
                }]

        class Collection:
            def find(self, query, sort):
                self.query = query
                self.sort = sort
                return Cursor()

        collection = Collection()
        with patch.object(request_store, "_ensure_indexes", new=AsyncMock()), patch.object(request_store, "_get_db", return_value={"media_requests": collection}):
            rows = asyncio.run(request_store.list_for_user(7))
        self.assertEqual(rows[0]["title"], "Example")
        self.assertEqual(collection.query, {"user_id": 7})


if __name__ == "__main__":
    unittest.main()

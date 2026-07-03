import unittest
import os
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import media_index, tmdb
from main.utils.hub_query import HubItem


def video_item(**overrides):
    data = {
        "message_id": 101,
        "secure_hash": "hash",
        "title": "Operator Title",
        "year": 2024,
        "description": "",
        "tags": ["manual"],
        "duration": 7200,
        "file_size": 1024,
        "has_thumb": True,
        "quality": "1080p",
        "file_name": "operator-title.mkv",
        "movie_key": "operator-title::2024",
        "tmdb_id": 12345,
        "tmdb_kind": "movie",
        "poster_path": "/keep-poster.jpg",
        "backdrop_path": "/keep-backdrop.jpg",
        "overview": "Keep this overview",
        "tmdb_genres": ["Keep"],
    }
    data.update(overrides)
    return HubItem(**data)


class MediaIndexCreditsBackfillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._items = dict(media_index._items)
        self._credits_state = dict(media_index._credits_state)
        media_index._items.clear()
        media_index._credits_state.update(
            running=False,
            done=0,
            total=0,
            updated=0,
            failed=0,
            started_at=0.0,
            finished_at=0.0,
            last_title="",
        )

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._items)
        media_index._credits_state.clear()
        media_index._credits_state.update(self._credits_state)

    async def test_backfills_missing_credits_without_replacing_core_metadata(self):
        item = video_item()
        media_index._items[item.message_id] = item
        hit = tmdb.TMDBHit(
            tmdb_id=12345,
            kind="movie",
            title="Different TMDB Title",
            year=2025,
            overview="Do not copy this",
            poster_path="/new-poster.jpg",
            backdrop_path="/new-backdrop.jpg",
            genres=["New"],
            imdb_id="tt12345",
            vote_average=7.4,
            vote_count=500,
            cast=["Actor One", "Actor Two"],
            director="Director One",
        )

        with (
            patch.object(media_index.tmdb, "is_configured", return_value=True),
            patch.object(media_index.tmdb, "fetch_by_id", new=AsyncMock(return_value=hit)) as fetch_by_id,
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_store_upsert", new=AsyncMock()),
            patch.object(media_index, "schedule_snapshot"),
        ):
            result = await media_index.backfill_missing_credits(bot=object())

        fetch_by_id.assert_awaited_once_with(12345, "movie")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(item.cast, ["Actor One", "Actor Two"])
        self.assertEqual(item.director, "Director One")
        self.assertEqual(item.imdb_id, "tt12345")
        self.assertEqual(item.tmdb_vote_average, 7.4)
        self.assertEqual(item.tmdb_vote_count, 500)
        self.assertGreater(item.tmdb_vote_checked_at, 0)
        self.assertEqual(item.title, "Operator Title")
        self.assertEqual(item.year, 2024)
        self.assertEqual(item.movie_key, "operator-title::2024")
        self.assertEqual(item.poster_path, "/keep-poster.jpg")
        self.assertEqual(item.backdrop_path, "/keep-backdrop.jpg")
        self.assertEqual(item.overview, "Keep this overview")
        self.assertEqual(item.tmdb_genres, ["Keep"])

    async def test_skips_items_without_existing_tmdb_id(self):
        item = video_item(tmdb_id=None, tmdb_kind="")
        media_index._items[item.message_id] = item

        with (
            patch.object(media_index.tmdb, "is_configured", return_value=True),
            patch.object(media_index.tmdb, "fetch_by_id", new=AsyncMock()) as fetch_by_id,
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_store_upsert", new=AsyncMock()),
        ):
            result = await media_index.backfill_missing_credits()

        fetch_by_id.assert_not_awaited()
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["updated"], 0)


if __name__ == "__main__":
    unittest.main()

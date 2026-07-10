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

    async def test_backfills_missing_ratings_even_when_credits_exist(self):
        item = video_item(
            cast=["Existing Actor"],
            director="Existing Director",
            imdb_id="tt-existing",
            tmdb_vote_average=0.0,
            tmdb_vote_count=0,
            tmdb_vote_checked_at=0.0,
        )
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
            imdb_id="tt-new",
            vote_average=7.9,
            vote_count=1500,
            cast=["New Actor"],
            director="New Director",
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
        self.assertEqual(item.cast, ["Existing Actor"])
        self.assertEqual(item.director, "Existing Director")
        self.assertEqual(item.imdb_id, "tt-existing")
        self.assertEqual(item.tmdb_vote_average, 7.9)
        self.assertEqual(item.tmdb_vote_count, 1500)
        self.assertGreater(item.tmdb_vote_checked_at, 0)

    async def test_backfill_marks_zero_vote_responses_checked(self):
        item = video_item(
            cast=["Existing Actor"],
            director="Existing Director",
            tmdb_vote_average=0.0,
            tmdb_vote_count=0,
            tmdb_vote_checked_at=0.0,
        )
        media_index._items[item.message_id] = item
        hit = tmdb.TMDBHit(
            tmdb_id=12345,
            kind="movie",
            title="Operator Title",
            year=2024,
            overview="",
            poster_path="",
            backdrop_path="",
            genres=[],
            imdb_id="",
            vote_average=0.0,
            vote_count=0,
        )

        with (
            patch.object(media_index.tmdb, "is_configured", return_value=True),
            patch.object(media_index.tmdb, "fetch_by_id", new=AsyncMock(return_value=hit)) as fetch_by_id,
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_store_upsert", new=AsyncMock()),
        ):
            result = await media_index.backfill_missing_credits()

        fetch_by_id.assert_awaited_once_with(12345, "movie")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(item.tmdb_vote_average, 0.0)
        self.assertEqual(item.tmdb_vote_count, 0)
        self.assertGreater(item.tmdb_vote_checked_at, 0)

    async def test_enrich_one_skips_generic_titles(self):
        item = video_item(
            title="(Untitled)",
            file_name="Untitled.mp4",
            movie_key="untitled",
            tmdb_id=None,
            tmdb_kind="",
        )
        media_index._items[item.message_id] = item

        with (
            patch.object(media_index.tmdb, "is_configured", return_value=True),
            patch.object(media_index.tmdb, "lookup_movie", new=AsyncMock()) as lookup_movie,
            patch.object(media_index.tmdb, "lookup_series", new=AsyncMock()) as lookup_series,
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_store_upsert", new=AsyncMock()),
        ):
            result = await media_index.enrich_one(item.message_id)

        self.assertFalse(result)
        lookup_movie.assert_not_awaited()
        lookup_series.assert_not_awaited()
        self.assertGreater(item.enriched_at, 0)

    async def test_enrich_one_allows_exact_tmdb_id_for_generic_titles(self):
        item = video_item(title="(Untitled)", file_name="Untitled.mp4", movie_key="untitled")
        media_index._items[item.message_id] = item
        hit = tmdb.TMDBHit(
            tmdb_id=12345,
            kind="movie",
            title="Manual Pick",
            year=2024,
            overview="Exact admin match",
            poster_path="/poster.jpg",
            backdrop_path="/backdrop.jpg",
            genres=["Drama"],
            imdb_id="tt-manual",
            vote_average=6.5,
            vote_count=50,
        )

        with (
            patch.object(media_index.tmdb, "is_configured", return_value=True),
            patch.object(media_index.tmdb, "fetch_by_id", new=AsyncMock(return_value=hit)) as fetch_by_id,
            patch.object(media_index.tmdb, "lookup_movie", new=AsyncMock()) as lookup_movie,
            patch.object(media_index.tmdb, "lookup_series", new=AsyncMock()) as lookup_series,
            patch.object(media_index.tmdb, "fetch_trailer", new=AsyncMock(return_value="")),
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "persist_now", new=AsyncMock()),
            patch.object(media_index, "_store_upsert", new=AsyncMock()),
        ):
            result = await media_index.enrich_one(item.message_id)

        self.assertTrue(result)
        fetch_by_id.assert_awaited_once_with(12345, "movie")
        lookup_movie.assert_not_awaited()
        lookup_series.assert_not_awaited()
        self.assertEqual(item.title, "Manual Pick")
        self.assertEqual(item.tmdb_vote_average, 6.5)

    def test_pick_heroes_skips_generic_enriched_titles(self):
        generic = video_item(
            message_id=301,
            title="(Untitled)",
            file_name="Untitled.mp4",
            movie_key="untitled",
            tmdb_id=111,
            backdrop_path="/bad-backdrop.jpg",
            overview="Bad match",
        )
        valid = video_item(
            message_id=201,
            title="Iron Man",
            file_name="Iron Man.mkv",
            movie_key="iron-man::2008",
            tmdb_id=222,
            backdrop_path="/good-backdrop.jpg",
            overview="Good match",
        )
        media_index._items.update({
            generic.message_id: generic,
            valid.message_id: valid,
        })

        heroes = media_index.pick_heroes(limit=3)

        self.assertEqual([item.title for item in heroes], ["Iron Man"])

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

    def test_dashboard_reports_credits_coverage(self):
        movie_missing_credits = video_item(
            message_id=201,
            media_kind="video",
            tmdb_id=12345,
            tmdb_kind="movie",
            cast=[],
            director="",
            tmdb_vote_average=7.0,
            tmdb_vote_count=100,
            tmdb_vote_checked_at=1.0,
        )
        tv_with_cast = video_item(
            message_id=202,
            media_kind="video",
            tmdb_id=67890,
            tmdb_kind="tv",
            cast=["Actor One"],
            director="",
            tmdb_vote_average=8.0,
            tmdb_vote_count=200,
            tmdb_vote_checked_at=1.0,
        )
        rating_gap = video_item(
            message_id=204,
            media_kind="video",
            tmdb_id=98765,
            tmdb_kind="movie",
            cast=["Actor Two"],
            director="Director Two",
        )
        overlap_gap = video_item(
            message_id=205,
            media_kind="video",
            tmdb_id=11223,
            tmdb_kind="movie",
            cast=[],
            director="",
        )
        no_tmdb = video_item(
            message_id=203,
            media_kind="video",
            tmdb_id=None,
            tmdb_kind="",
        )
        media_index._items.update({
            movie_missing_credits.message_id: movie_missing_credits,
            tv_with_cast.message_id: tv_with_cast,
            rating_gap.message_id: rating_gap,
            overlap_gap.message_id: overlap_gap,
            no_tmdb.message_id: no_tmdb,
        })

        quality = media_index.dashboard_stats()["metadata_quality"]

        self.assertEqual(quality["video_items"], 5)
        self.assertEqual(quality["tmdb_enriched_video_items"], 4)
        self.assertEqual(quality["missing_credits"], 2)
        self.assertEqual(quality["missing_ratings"], 2)
        self.assertEqual(quality["missing_tmdb_metadata"], 3)
        self.assertEqual(quality["missing_tmdb_id"], 1)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from collections import Counter
from unittest.mock import patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import rec_engine
from main.utils.hub_query import HubItem


def make_item(
    message_id: int = 1,
    *,
    title: str = "The Invisible Guest",
    tmdb_id: int = 123,
    genres: list[str] | None = None,
) -> HubItem:
    return HubItem(
        message_id=message_id,
        secure_hash=f"hash-{message_id}",
        title=title,
        year=2016,
        description="",
        tags=[],
        duration=6000,
        file_size=1024,
        has_thumb=True,
        tmdb_id=tmdb_id,
        tmdb_kind="movie",
        tmdb_genres=genres or ["Mystery", "Thriller"],
    )


async def empty_history(_user_id: int, limit: int = 80) -> list:
    return []


async def empty_watchlist(_user_id: int) -> list:
    return []


async def empty_ratings(_user_id: int, limit: int = 200) -> list:
    return []


async def empty_continue(_user_id: int) -> dict:
    return {}


class RecEngineSignalTest(unittest.IsolatedAsyncioTestCase):
    async def test_partial_continue_entry_is_seed_but_not_watched_label(self):
        item = make_item()

        async def continue_map(_user_id: int) -> dict:
            return {"movie1": {"pos": 1200, "dur": 6000, "title": "The Invisible Guest"}}

        with (
            patch.object(rec_engine.wh_store, "get_recent", empty_history),
            patch.object(rec_engine.watchlist_store, "get_ids", empty_watchlist),
            patch.object(rec_engine.ratings_store, "get_user_ratings", empty_ratings),
            patch.object(rec_engine.cw_store, "get_all", continue_map),
            patch.object(rec_engine, "_item_for_cw_key", return_value=item),
        ):
            profile = await rec_engine._collect_signal_profile(7)

        self.assertEqual(profile["seeds"], [(123, "movie")])
        self.assertGreater(profile["seed_genres"]["Mystery"], 0)
        self.assertEqual(profile["recent_titles"], [])

    async def test_completed_history_can_feed_watched_label(self):
        item = make_item()

        async def history(_user_id: int, limit: int = 80) -> list:
            return [{"cw_key": "movie1", "play_count": 1}]

        with (
            patch.object(rec_engine.wh_store, "get_recent", history),
            patch.object(rec_engine.watchlist_store, "get_ids", empty_watchlist),
            patch.object(rec_engine.ratings_store, "get_user_ratings", empty_ratings),
            patch.object(rec_engine.cw_store, "get_all", empty_continue),
            patch.object(rec_engine, "_item_for_cw_key", return_value=item),
        ):
            profile = await rec_engine._collect_signal_profile(7)

        self.assertEqual(profile["recent_titles"], ["The Invisible Guest"])

    async def test_partial_only_shelf_uses_like_copy(self):
        cards = [
            make_item(10, title="Mystery One", tmdb_id=201),
            make_item(11, title="Mystery Two", tmdb_id=202),
            make_item(12, title="Mystery Three", tmdb_id=203),
        ]

        async def collect_profile(_user_id: int) -> dict:
            return {
                "seed_genres": Counter({"Mystery": 2.0}),
                "exclude_tmdb": set(),
                "recent_titles": [],
            }

        async def dismissed(_user_id: int) -> set:
            return set()

        def query_grouped(**_kwargs):
            return cards, len(cards)

        with (
            patch.object(rec_engine, "_collect_signal_profile", collect_profile),
            patch.object(rec_engine.dismissed_store, "get_dismissed_ids", dismissed),
            patch.object(rec_engine.media_index, "query_grouped", query_grouped),
        ):
            shelves = await rec_engine.get_personal_shelves(7, limit=3)

        self.assertEqual(shelves[0]["name"], "Because you like Mystery")


if __name__ == "__main__":
    unittest.main()

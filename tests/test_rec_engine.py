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
    keywords: list[str] | None = None,
    series_key: str = "",
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
        tmdb_keywords=keywords or [],
        series_key=series_key,
        series_title=title if series_key else "",
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
    async def test_keywords_are_a_low_weight_recommendation_tiebreaker(self):
        previous = dict(rec_engine.media_index._items)
        try:
            plain = make_item(8, tmdb_id=88, genres=["Drama"])
            keyword_match = make_item(9, tmdb_id=99, genres=["Drama"], keywords=["heist"])
            rec_engine.media_index._items.clear()
            rec_engine.media_index._items.update({8: plain, 9: keyword_match})

            cards = rec_engine._rank_candidate_cards(
                [(88, "movie", 1), (99, "movie", 1)],
                {"seed_genres": Counter(), "seed_keywords": Counter({"heist": 1.0}), "negative_genres": Counter()},
            )

            self.assertEqual(rec_engine._card_tmdb(cards[0]), (99, "movie"))
        finally:
            rec_engine.media_index._items.clear()
            rec_engine.media_index._items.update(previous)

    async def test_movie_and_tv_with_same_tmdb_id_remain_distinct_candidates(self):
        previous = dict(rec_engine.media_index._items)
        try:
            movie = make_item(10, title="Movie 77", tmdb_id=77)
            series = make_item(11, title="Series 77", tmdb_id=77, series_key="series-77")
            rec_engine.media_index._items.clear()
            rec_engine.media_index._items.update({10: movie, 11: series})

            cards = rec_engine._rank_candidate_cards(
                [(77, "movie", 1), (77, "tv", 1)],
                {"seed_genres": Counter(), "negative_genres": Counter()},
            )

            self.assertEqual(
                {rec_engine._card_tmdb(card) for card in cards},
                {(77, "movie"), (77, "tv")},
            )
        finally:
            rec_engine.media_index._items.clear()
            rec_engine.media_index._items.update(previous)

    async def test_partial_continue_entry_is_a_seed(self):
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

    async def test_recommendation_open_is_a_gentle_profile_signal(self):
        item = make_item(tmdb_id=456, genres=["Comedy"])

        async def feedback(_user_id: int, limit: int = 80) -> list:
            return [{"tmdb_id": 456, "tmdb_kind": "movie", "action": "open"}]

        with (
            patch.object(rec_engine.wh_store, "get_recent", empty_history),
            patch.object(rec_engine.watchlist_store, "get_ids", empty_watchlist),
            patch.object(rec_engine.ratings_store, "get_user_ratings", empty_ratings),
            patch.object(rec_engine.cw_store, "get_all", empty_continue),
            patch.object(rec_engine.rec_feedback_store, "get_recent_opens", feedback),
            patch.object(rec_engine.media_index, "card_for_tmdb_id", return_value=item),
        ):
            profile = await rec_engine._collect_signal_profile(7)

        self.assertEqual(profile["seeds"], [(456, "movie")])
        self.assertGreater(profile["seed_genres"]["Comedy"], 0)

    async def test_explicit_like_is_selected_when_history_exceeds_seed_cap(self):
        history_items = [make_item(index + 1, tmdb_id=100 + index) for index in range(8)]
        liked_item = make_item(99, tmdb_id=999, genres=["Drama"])

        async def history(_user_id: int, limit: int = 80) -> list:
            return [{"cw_key": f"movie{index + 1}", "play_count": 1} for index in range(8)]

        async def ratings(_user_id: int, limit: int = 200) -> list:
            return [{"message_id": 99, "rating": "up"}]

        def item_for_key(key: str):
            return history_items[int(key.removeprefix("movie")) - 1]

        with (
            patch.object(rec_engine.wh_store, "get_recent", history),
            patch.object(rec_engine.watchlist_store, "get_ids", empty_watchlist),
            patch.object(rec_engine.ratings_store, "get_user_ratings", ratings),
            patch.object(rec_engine.cw_store, "get_all", empty_continue),
            patch.object(rec_engine, "_item_for_cw_key", side_effect=item_for_key),
            patch.object(rec_engine.media_index, "get_item", return_value=liked_item),
        ):
            profile = await rec_engine._collect_signal_profile(7)

        self.assertIn((999, "movie"), profile["seeds"])
        self.assertEqual(len(profile["seeds"]), 8)

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

    async def test_completed_history_shelf_uses_genre_copy(self):
        cards = [
            make_item(10, title="Mystery One", tmdb_id=201),
            make_item(11, title="Mystery Two", tmdb_id=202),
            make_item(12, title="Mystery Three", tmdb_id=203),
        ]

        async def collect_profile(_user_id: int) -> dict:
            return {
                "seed_genres": Counter({"Mystery": 2.0}),
                "exclude_tmdb": set(),
            }

        async def dismissed(_user_id: int) -> set:
            return set()

        with (
            patch.object(rec_engine, "_collect_signal_profile", collect_profile),
            patch.object(rec_engine.dismissed_store, "get_dismissed_ids", dismissed),
            patch.object(rec_engine.media_index, "query_grouped", return_value=(cards, len(cards))),
        ):
            shelves = await rec_engine.get_personal_shelves(7, limit=3)

        self.assertEqual(shelves[0]["name"], "Because you like Mystery")


if __name__ == "__main__":
    unittest.main()

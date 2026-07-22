import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import media_index
from main.utils.hub_query import HubItem


def video_item(message_id: int, *, title: str, **overrides) -> HubItem:
    data = {
        "message_id": message_id,
        "secure_hash": f"hash{message_id}",
        "title": title,
        "year": 2024,
        "description": "",
        "tags": [],
        "duration": 7200,
        "file_size": 1024,
        "has_thumb": True,
        "quality": "720p",
        "file_name": f"{title}.mkv",
        "media_kind": "video",
    }
    data.update(overrides)
    return HubItem(**data)


class MediaIndexSearchTests(unittest.TestCase):
    def setUp(self):
        self._items = dict(media_index._items)
        media_index._items.clear()
        media_index._invalidate_search_index()

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._items)
        media_index._invalidate_search_index()

    def test_default_search_prefers_exact_title_over_newer_metadata_hit(self):
        exact = video_item(101, title="Castle")
        weak_newer = video_item(
            202,
            title="Unrelated Documentary",
            description="Behind the scenes at Castle studios",
        )
        media_index._items.update({
            exact.message_id: exact,
            weak_newer.message_id: weak_newer,
        })

        cards, total = media_index.query_grouped(q="Castle", sort="newest", limit=10)

        self.assertEqual(total, 2)
        self.assertEqual(cards[0].message_id, exact.message_id)

    def test_relevance_stays_primary_when_a_sort_is_selected(self):
        exact = video_item(101, title="Castle")
        weak_old = video_item(1, title="Unrelated", description="Castle archive")
        media_index._items.update({exact.message_id: exact, weak_old.message_id: weak_old})
        media_index._invalidate_search_index()

        cards, total = media_index.query_grouped(q="Castle", sort="oldest", limit=10)

        self.assertEqual(total, 2)
        self.assertEqual(cards[0].message_id, exact.message_id)

    def test_raw_query_prefers_exact_title_over_newer_metadata_hit(self):
        exact = video_item(101, title="Castle")
        weak_newer = video_item(
            202,
            title="Unrelated Documentary",
            description="Behind the scenes at Castle studios",
        )
        media_index._items.update({
            exact.message_id: exact,
            weak_newer.message_id: weak_newer,
        })

        items, next_cursor = media_index.query(q="Castle", sort="newest", limit=10)

        self.assertIsNone(next_cursor)
        self.assertEqual([item.message_id for item in items], [101, 202])

    def test_tmdb_keywords_expand_search_without_outranking_visible_metadata(self):
        synopsis_match = video_item(
            211,
            title="Garden Drama",
            description="A documentary set in a greenhouse.",
        )
        keyword_match = video_item(
            212,
            title="Quiet Film",
            tmdb_keywords=["greenhouse", "botany"],
        )
        media_index._items.update({
            synopsis_match.message_id: synopsis_match,
            keyword_match.message_id: keyword_match,
        })

        items, _ = media_index.query(q="greenhouse", sort="newest", limit=10)

        self.assertEqual([item.message_id for item in items], [211, 212])

    def test_series_title_search_does_not_bypass_quality_filter(self):
        castle_720 = video_item(
            301,
            title="Castle S01E01",
            series_key="castle",
            series_title="Castle",
            season=1,
            episode=1,
            quality="720p",
            movie_key="",
        )
        media_index._items[castle_720.message_id] = castle_720

        cards, total = media_index.query_grouped(
            q="Castle",
            quality="1080p",
            view="series",
            limit=10,
        )

        self.assertEqual(total, 0)
        self.assertEqual(cards, [])

    def test_search_matches_structured_quality_year_and_episode_label(self):
        episode = video_item(
            351,
            title="Castle",
            year=2009,
            quality="1080p",
            series_key="castle",
            series_title="Castle",
            season=6,
            episode=14,
            movie_key="",
        )
        media_index._items[episode.message_id] = episode

        quality_cards, quality_total = media_index.query_grouped(q="1080p", limit=10)
        year_cards, year_total = media_index.query_grouped(q="2009", limit=10)
        episode_cards, episode_total = media_index.query_grouped(q="S06E14", limit=10)

        self.assertEqual(quality_total, 1)
        self.assertEqual(year_total, 1)
        self.assertEqual(episode_total, 1)
        self.assertEqual(quality_cards[0].series_key, "castle")
        self.assertEqual(year_cards[0].series_key, "castle")
        self.assertEqual(episode_cards[0].series_key, "castle")

    def test_multiterm_search_matches_across_title_cast_and_genre(self):
        target = video_item(
            360,
            title="Edge of Tomorrow",
            cast=["Tom Cruise"],
            tmdb_genres=["Science Fiction", "Action"],
        )
        unrelated = video_item(361, title="Tom and Jerry", tmdb_genres=["Comedy"])
        media_index._items.update({target.message_id: target, unrelated.message_id: unrelated})
        media_index._invalidate_search_index()

        cards, total = media_index.query_grouped(q="Tom sci-fi", limit=10)

        self.assertEqual(total, 1)
        self.assertEqual(cards[0].message_id, target.message_id)

    def test_episode_aliases_and_diacritics_are_normalized(self):
        episode = video_item(
            370,
            title="Pokémon Horizons",
            series_key="pokemon-horizons",
            series_title="Pokémon Horizons",
            season=2,
            episode=3,
        )
        media_index._items[episode.message_id] = episode
        media_index._invalidate_search_index()

        for query in ("pokemon", "2x03", "Season 2 Episode 3"):
            cards, total = media_index.query_grouped(q=query, limit=10)
            self.assertEqual(total, 1, query)
            self.assertEqual(cards[0].series_key, "pokemon-horizons", query)

    def test_suggestions_borrow_group_tmdb_poster(self):
        plain_newer = video_item(
            402,
            title="Kalki",
            movie_key="kalki::2024",
            poster_path="",
        )
        enriched_older = video_item(
            401,
            title="Kalki",
            movie_key="kalki::2024",
            tmdb_id=123,
            tmdb_kind="movie",
            poster_path="/tmdb-kalki.jpg",
        )
        media_index._items.update({
            plain_newer.message_id: plain_newer,
            enriched_older.message_id: enriched_older,
        })

        suggestions = media_index.suggest("kalki", limit=5)

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["poster_path"], "/tmdb-kalki.jpg")
        self.assertEqual(suggestions[0]["message_id"], plain_newer.message_id)

    def test_find_exact_upload_uses_hash_and_size(self):
        original = video_item(501, title="Original", secure_hash="same", file_size=1000)
        collision = video_item(502, title="Collision", secure_hash="same", file_size=2000)
        media_index._items.update({
            original.message_id: original,
            collision.message_id: collision,
        })

        self.assertEqual(
            media_index.find_exact_upload("same", 1000).message_id,
            501,
        )
        self.assertEqual(
            media_index.find_exact_upload("same", 2000).message_id,
            502,
        )
        self.assertIsNone(media_index.find_exact_upload("same", 3000))


if __name__ == "__main__":
    unittest.main()

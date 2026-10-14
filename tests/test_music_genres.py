"""Self-checks for genre normalization and music genre facets.
Pure — no Mongo, no network."""

import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("MONGO_URI", "")

from main.utils import media_index  # noqa: E402


class NormalizeGenreTests(unittest.TestCase):
    def test_first_component_wins(self):
        self.assertEqual(media_index.normalize_genre("Pop/Rock"), "Pop")
        self.assertEqual(media_index.normalize_genre("Hip Hop, Rap"), "Hip Hop")

    def test_numeric_code_dropped(self):
        self.assertEqual(media_index.normalize_genre("17"), "")
        self.assertEqual(media_index.normalize_genre("0"), "")

    def test_junk_dropped(self):
        self.assertEqual(media_index.normalize_genre("music"), "")
        self.assertEqual(media_index.normalize_genre("unknown"), "")

    def test_title_case_normalization(self):
        self.assertEqual(media_index.normalize_genre("pop"), "Pop")
        self.assertEqual(media_index.normalize_genre("HIP HOP"), "Hip Hop")
        self.assertEqual(media_index.normalize_genre("hip-hop"), "Hip-Hop")

    def test_empty(self):
        self.assertEqual(media_index.normalize_genre(""), "")
        self.assertEqual(media_index.normalize_genre(None), "")


class MusicGenreFacetTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(media_index._items)

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._saved)

    def _audio(self, mid, tags, *, hidden=False, kind="audio"):
        from main.utils.hub_query import HubItem
        item = HubItem(
            message_id=mid, secure_hash=f"hash{mid}", title=f"t{mid}",
            year=None, description="", tags=list(tags), duration=180,
            file_size=1024, has_thumb=False, media_kind=kind, hidden=hidden,
            probed_at=1000.0,
        )
        media_index._items[mid] = item
        return item

    def test_counts_audio_only_normalized(self):
        self._audio(1, ["pop", "Rock"])
        self._audio(2, ["pop/rock"])           # normalizes to "Pop"
        self._audio(3, ["Pop"], kind="video")  # video — excluded
        self._audio(4, ["pop"], hidden=True)   # hidden — excluded
        facets = media_index.music_genre_facets()
        by_genre = {f["genre"]: f["count"] for f in facets}
        self.assertEqual(by_genre.get("Pop"), 2)
        self.assertEqual(by_genre.get("Rock"), 1)
        self.assertNotIn("pop", by_genre)  # canonical casing only

    def test_limit_respected(self):
        for i in range(30):
            self._audio(100 + i, [f"Genre{i}"])
        self.assertEqual(len(media_index.music_genre_facets(limit=10)), 10)

    def test_missing_genre_items_detected_for_backfill(self):
        self._audio(201, ["Pop"])
        self._audio(202, ["Not A Genre", ""] )  # "Not A Genre" normalizes fine
        self._audio(203, ["17"])                 # numeric-only = missing
        missing_ids = {it.message_id for it in media_index.audio_items_missing_genre()}
        self.assertNotIn(201, missing_ids)
        self.assertIn(203, missing_ids)


if __name__ == "__main__":
    unittest.main()

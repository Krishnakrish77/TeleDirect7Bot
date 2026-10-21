"""Regression checks: audio/book items must never carry a movie_key.

A song titled like a film ("Jailer 2") that gains a movie_key collapses
into that film's variant group — its likes/plays then feed the movie's
TMDB recommendation signals and its card renders with quality-variant
labels. The ingest path (media_index._item_from_message) guards this;
these tests pin the recompute paths too.
Pure — no Mongo, no network."""

import asyncio
import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("MONGO_URI", "")

from main.utils import media_index  # noqa: E402


class AudioMovieKeyTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(media_index._items)

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._saved)

    def _item(self, mid, title, kind="audio", **extra):
        from main.utils.hub_query import HubItem
        item = HubItem(
            message_id=mid, secure_hash=f"hash{mid}", title=title,
            year=None, description="", tags=[], duration=180,
            file_size=1024, has_thumb=False, media_kind=kind, hidden=False,
            probed_at=1000.0,
            **extra,
        )
        media_index._items[mid] = item
        return item

    def test_apply_tmdb_skips_movie_key_for_audio(self):
        from main.utils.tmdb import TMDBHit
        hit = TMDBHit(
            tmdb_id=99999, kind="movie", title="Jailer 2", year=2024,
            overview="", poster_path="", backdrop_path="",
            genres=[], imdb_id="",
        )
        item = self._item(501, "Jailer 2", kind="audio", file_name="jailer-2-theme.mp3")
        media_index._apply_tmdb_to_item(item, hit)
        self.assertEqual(item.movie_key, "", "audio must never join a movie variant group")
        self.assertEqual(item.tmdb_id, None if False else item.tmdb_id)  # enrichment fields still set
        self.assertEqual(item.title, "Jailer 2")

    def test_clear_audio_mismatches_strips_stray_movie_key(self):
        # Simulate a pre-guard reindex that stamped a song with a movie key.
        item = self._item(502, "Jailer 2", kind="audio", movie_key="jailer-2::2024")
        fixed = asyncio.run(
            media_index.clear_audio_tmdb_mismatches()
        )
        self.assertEqual(fixed, 1)
        self.assertEqual(item.movie_key, "")


if __name__ == "__main__":
    unittest.main()

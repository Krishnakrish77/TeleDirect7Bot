import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import media_index as mi


def _item(mid, artist, genres=(), tags=(), hidden=False, kind="audio"):
    return SimpleNamespace(
        message_id=mid, artist=artist, tmdb_genres=list(genres),
        tags=list(tags), hidden=hidden, media_kind=kind,
        album_title="", track_number=0, title=f"t{mid}",
    )


class RadioTracksTest(unittest.TestCase):
    def setUp(self):
        self._orig_items = mi._items
        self.seed = _item(1, "A", ["rock"], ["90s"])
        mi._items = {
            1: self.seed,
            2: _item(2, "A", ["rock"]),          # same artist -> 4
            3: _item(3, "A", ["rock"]),          # same artist -> 4
            4: _item(4, "A"),                    # same artist -> 4
            5: _item(5, "A"),                    # same artist -> 4
            6: _item(6, "B", ["rock"], ["90s"]),  # genre+tag  -> 3
            7: _item(7, "C", ["rock"]),          # genre      -> 2
            8: _item(8, "D", ["pop"]),           # filler
            9: _item(9, "E", ["jazz"]),          # filler
            10: _item(10, "F", ["metal"], hidden=True),   # hidden -> skipped
            11: _item(11, "G", ["rock"], kind="video"),   # not audio -> skipped
        }

    def tearDown(self):
        mi._items = self._orig_items

    def test_track_radio_caps_and_grounds(self):
        out = mi.radio_tracks(self.seed, None, {1}, limit=5)
        ids = [it.message_id for it in out]
        self.assertEqual(len(out), 5)
        self.assertNotIn(1, ids)  # seed excluded
        self.assertEqual(sum(1 for it in out if it.artist == "A"), 3)  # artist cap
        self.assertIn(6, ids)
        self.assertIn(7, ids)
        self.assertNotIn(10, ids)  # hidden
        self.assertNotIn(11, ids)  # non-audio

    def test_explicit_exclude(self):
        ids = [it.message_id for it in mi.radio_tracks(self.seed, None, {1, 2, 3}, limit=5)]
        self.assertNotIn(2, ids)
        self.assertNotIn(3, ids)

    def test_thin_pool_padded_with_filler(self):
        out = mi.radio_tracks(self.seed, None, {1}, limit=8)
        self.assertEqual(len(out), 8)

    def test_artist_radio(self):
        self.assertTrue(mi.radio_tracks(None, "a", set(), limit=5))


if __name__ == "__main__":
    unittest.main()

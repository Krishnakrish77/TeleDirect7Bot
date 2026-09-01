"""Watchlist route item_id validation.

Guards the allowlist that every /api/watchlist mutation runs through:
the SPA emits ``movie:{movie_key}`` ids where movie keys carry a
``slug::year`` suffix (``dada::2023``), so the validator must accept the
year run or every movie save 400s with "invalid item_id".
"""
import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.watchlist_routes import _is_valid_iid


class WatchlistItemIdValidationTest(unittest.TestCase):
    def test_accepts_movie_key_with_year_suffix(self):
        self.assertTrue(_is_valid_iid("movie:dada::2023"))

    def test_accepts_keys_without_year_suffix(self):
        self.assertTrue(_is_valid_iid("movie:kalki"))
        self.assertTrue(_is_valid_iid("series:the-crown-2020"))
        self.assertTrue(_is_valid_iid("album:eminem-the-slim-shady-lp"))
        self.assertTrue(_is_valid_iid("1234567890"))

    def test_rejects_bad_formats(self):
        self.assertFalse(_is_valid_iid(""))
        self.assertFalse(_is_valid_iid("movie:"))
        self.assertFalse(_is_valid_iid("movie:da da::2023"))
        self.assertFalse(_is_valid_iid("movie::2023"))
        self.assertFalse(_is_valid_iid("movie:dada::"))
        self.assertFalse(_is_valid_iid("movie:dada::20x3"))
        self.assertFalse(_is_valid_iid("movie:dada::2023::extra"))
        self.assertFalse(_is_valid_iid("tv:dada::2023"))
        self.assertFalse(_is_valid_iid("dada::2023"))
        self.assertFalse(_is_valid_iid("10" * 20))


if __name__ == "__main__":
    unittest.main()

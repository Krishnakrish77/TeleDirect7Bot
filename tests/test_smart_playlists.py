"""Self-checks for smart-playlist config validation and evaluation.
Pure — no Mongo, no network; play stats are injected directly."""

import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("MONGO_URI", "")

from main.utils import smart_playlists  # noqa: E402


class ValidateConfigTests(unittest.TestCase):
    def test_valid_config(self):
        config = smart_playlists.validate_config(
            {"all": [{"field": "genre", "op": "eq", "value": "Rock"}]},
            "recentlyAdded", 50,
        )
        self.assertEqual(config["sort"], "recentlyAdded")
        self.assertEqual(config["limit"], 50)

    def test_unknown_sort_rejected(self):
        with self.assertRaises(smart_playlists.RulesError):
            smart_playlists.validate_config(
                {"all": [{"field": "genre", "op": "eq", "value": "Rock"}]},
                "loudest", 50,
            )

    def test_limit_clamped_to_store_cap(self):
        config = smart_playlists.validate_config(
            {"all": [{"field": "genre", "op": "eq", "value": "Rock"}]},
            "title", 10_000,
        )
        self.assertEqual(config["limit"], getattr(smart_playlists.playlist_store, "_MAX_TRACKS", 500))

    def test_invalid_rules_rejected(self):
        with self.assertRaises(smart_playlists.RulesError):
            smart_playlists.validate_config(
                {"all": [{"field": "playCount", "op": "gt", "value": "many"}]},
                "title", 50,
            )


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.server.stats_routes import _binge_stats, _rewatched_titles


class RewatchedTitlesTest(unittest.TestCase):
    def test_counts_replayed_titles_only(self):
        # movie:a watched once, movie:b replayed (2 plays, 1 part),
        # series:c fully watched once (3 distinct episodes, 3 plays -> not a rewatch),
        # series:d one episode replayed (4 plays across 3 episodes).
        title_counts = {"movie:a": 1, "movie:b": 2, "series:c": 3, "series:d": 4}
        group_items = {
            "movie:a": {"a1"},
            "movie:b": {"b1"},
            "series:c": {"c1", "c2", "c3"},
            "series:d": {"d1", "d2", "d3"},
        }
        self.assertEqual(_rewatched_titles(title_counts, group_items), 2)  # b and d

    def test_empty(self):
        self.assertEqual(_rewatched_titles({}, {}), 0)


class BingeStatsTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_binge_stats([]), (0, 0))
        self.assertEqual(_binge_stats([None, None]), (0, 0))

    def test_single_evening_binge(self):
        base = datetime(2026, 7, 19, 20, 0)
        stamps = [base, base + timedelta(hours=1), base + timedelta(hours=2), base + timedelta(hours=3)]
        # 4 completions each within 3h -> one binge of 4
        self.assertEqual(_binge_stats(stamps), (4, 1))

    def test_gap_splits_sessions_and_below_min_is_not_a_binge(self):
        base = datetime(2026, 7, 1, 20, 0)
        stamps = [
            base, base + timedelta(hours=1), base + timedelta(hours=2),   # binge of 3
            base + timedelta(days=1),                                     # lone (next day)
            base + timedelta(days=2), base + timedelta(days=2, hours=1),  # pair (<min)
        ]
        longest, sessions = _binge_stats(stamps)
        self.assertEqual(longest, 3)
        self.assertEqual(sessions, 1)

    def test_unsorted_input(self):
        base = datetime(2026, 7, 19, 20, 0)
        stamps = [base + timedelta(hours=2), base, base + timedelta(hours=1)]
        self.assertEqual(_binge_stats(stamps), (3, 1))


if __name__ == "__main__":
    unittest.main()

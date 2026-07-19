import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import cw_store


class CwStoreAcceptWriteTest(unittest.TestCase):
    def test_accept_write_rules(self):
        accept = cw_store._accept_write

        # first write always wins
        self.assertTrue(accept(None, {"t": 100, "pos": 10, "started_at": 100}))

        cur = {"t": 200, "pos": 100, "started_at": 150}
        # older-or-equal timestamp loses (recency wins)
        self.assertFalse(accept(cur, {"t": 150, "pos": 120, "started_at": 150}))
        self.assertFalse(accept(cur, {"t": 200, "pos": 120, "started_at": 150}))
        # forward progress from a newer write is accepted
        self.assertTrue(accept(cur, {"t": 300, "pos": 120, "started_at": 150}))
        # STALE rewind: newer t (clock skew) but lower pos from an OLDER session -> reject
        self.assertFalse(accept(cur, {"t": 300, "pos": 40, "started_at": 120}))
        # intentional rewind on the SAME session -> accepted
        self.assertTrue(accept(cur, {"t": 300, "pos": 40, "started_at": 150}))
        # intentional rewind on a NEWER session -> accepted
        self.assertTrue(accept(cur, {"t": 300, "pos": 40, "started_at": 260}))
        # small backward jitter within grace -> accepted even from an older session
        self.assertTrue(accept(cur, {"t": 300, "pos": 97, "started_at": 120}))


if __name__ == "__main__":
    unittest.main()

"""Photos memories (on-this-day) + trash purge/expiry — pure-logic tests.

No Mongo connection: exercises the pure helpers (taken_md, trash cutoff,
memories payload, query scoping) following test_photo_geo.py conventions.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import photo_store
from main.utils.photo_store import (
    backfill_taken_md,
    memories_payload,
    taken_md,
    trash_expiry_cutoff,
)


def _dt(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


class TakenMdTest(unittest.TestCase):
    def test_zero_padded_month_day(self):
        self.assertEqual(taken_md(_dt(2023, 3, 5)), "03-05")
        self.assertEqual(taken_md(_dt(2024, 12, 31)), "12-31")

    def test_feb_29_skipped(self):
        # Leap-day photos never match — the store leaves them without taken_md
        # (backfill writes the "02-30" sentinel instead).
        self.assertIsNone(taken_md(_dt(2024, 2, 29)))

    def test_missing_taken_at(self):
        self.assertIsNone(taken_md(None))
        self.assertIsNone(taken_md("2024-03-05"))


class TrashExpiryCutoffTest(unittest.TestCase):
    def test_zero_ttl_disables(self):
        self.assertIsNone(trash_expiry_cutoff(_dt(2026, 10, 14), 0))
        self.assertIsNone(trash_expiry_cutoff(_dt(2026, 10, 14), -5))

    def test_cutoff_arithmetic(self):
        now = _dt(2026, 10, 14, 12)
        self.assertEqual(trash_expiry_cutoff(now, 30), now - timedelta(days=30))
        self.assertEqual(trash_expiry_cutoff(now, 1), now - timedelta(days=1))


class MemoriesQueryTest(unittest.TestCase):
    def test_memories_scopes_by_owner_and_day(self):
        captured = {}

        class FakeCollection:
            def find(self, query, *a, **kw):
                captured["query"] = query
                return self

            def sort(self, *a, **kw):
                return self

            async def to_list(self, length):
                return []

        class FakeDb:
            def __getitem__(self, name):
                assert name == "photos"
                return FakeCollection()

        with patch.object(photo_store, "_get_db", return_value=FakeDb()):
            import asyncio
            asyncio.run(photo_store.memories(42, "10-14"))
        q = captured["query"]
        self.assertEqual(q["owner_user_id"], 42)
        self.assertEqual(q["taken_md"], "10-14")
        self.assertFalse(q["deleted"])


class MemoriesPayloadTest(unittest.TestCase):
    def test_years_ago_computed(self):
        doc = {"_id": "x", "taken_at": _dt(2020, 10, 14), "message_id": 5}
        payload = memories_payload(doc)
        self.assertEqual(payload["yearsAgo"], datetime.now(timezone.utc).year - 2020)
        self.assertEqual(payload["messageId"], 5)

    def test_no_taken_at_no_years_ago(self):
        payload = memories_payload({"_id": "x", "message_id": 5})
        self.assertNotIn("yearsAgo", payload)


class BackfillTakenMdTest(unittest.TestCase):
    def test_backfill_writes_md_and_feb29_sentinel(self):
        updated = []

        class FakePhotos:
            def find(self, query, projection=None):
                assert query["owner_user_id"] == 7
                assert "taken_md" in query
                return self

            async def to_list(self, length):
                docs = [
                    {"_id": "a", "taken_at": _dt(2021, 3, 5)},
                    {"_id": "b", "taken_at": _dt(2024, 2, 29)},
                ]
                # Return once, then empty — loop terminates.
                if getattr(self, "_done", False):
                    return []
                self._done = True
                return docs

            async def update_one(self, match, update):
                updated.append((match["_id"], update["$set"]["taken_md"]))

        class FakeDb:
            def __getitem__(self, name):
                return FakePhotos()

        import asyncio
        with patch.object(photo_store, "_get_db", return_value=FakeDb()):
            count = asyncio.run(backfill_taken_md(7))
        self.assertEqual(count, 2)
        self.assertEqual(sorted(updated), [("a", "03-05"), ("b", "02-30")])


if __name__ == "__main__":
    unittest.main()

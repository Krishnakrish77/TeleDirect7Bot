"""Self-checks for the Mongo migration registry (main/utils/migrations.py).

No Mongo: the ledger and migration functions are faked, asserting the
run-once semantics, failure retry, and ordering contract.
"""

from __future__ import annotations

import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

import asyncio
import unittest
from unittest import mock

from main.utils import migrations


class _FakeLedger:
    def __init__(self, existing: set[str] | None = None):
        self.existing = set(existing or [])
        self.written: list[str] = []

    def find(self, query, projection=None):
        return self

    async def to_list(self, length):
        return [{"_id": i} for i in self.existing]

    async def update_one(self, query, update, upsert=False):
        self.written.append(str(query["_id"]))
        self.existing.add(str(query["_id"]))


class _FakeDB(dict):
    def __init__(self):
        super().__init__()
        self.ledger = _FakeLedger()
        self["schema_migrations"] = self.ledger


def _patch_db(monkeypatch, db):
    return mock.patch.object(migrations, "_get_db", return_value=db)


class RunPendingTest(unittest.TestCase):
    def test_runs_unrecorded_migrations_and_records_them(self):
        async def run():
            calls = []

            async def m1() -> str | None:
                calls.append("m1")
                return "did m1"

            async def m2() -> str | None:
                calls.append("m2")
                return None

            db = _FakeDB()
            with _patch_db(mock, db), \
                 mock.patch.object(migrations, "MIGRATIONS", [
                     migrations.Migration("a", "first", m1),
                     migrations.Migration("b", "second", m2),
                 ]):
                ran = await migrations.run_pending_migrations()
            self.assertEqual(ran, 2)
            self.assertEqual(calls, ["m1", "m2"])
            self.assertEqual(db.ledger.written, ["a", "b"])
        asyncio.run(run())

    def test_skips_migrations_already_in_ledger(self):
        async def run():
            calls = []

            async def m1() -> str | None:
                calls.append("m1")
                return None

            db = _FakeDB()
            db.ledger.existing.add("a")
            with _patch_db(mock, db), \
                 mock.patch.object(migrations, "MIGRATIONS", [
                     migrations.Migration("a", "done before", m1),
                 ]):
                ran = await migrations.run_pending_migrations()
            self.assertEqual(ran, 0)
            self.assertEqual(calls, [])
        asyncio.run(run())

    def test_failed_migration_is_not_recorded_and_does_not_block_rest(self):
        async def run():
            calls = []

            async def boom() -> str | None:
                raise RuntimeError("mongo hiccup")

            async def ok() -> str | None:
                calls.append("ok")
                return None

            db = _FakeDB()
            with _patch_db(mock, db), \
                 mock.patch.object(migrations, "MIGRATIONS", [
                     migrations.Migration("bad", "will fail", boom),
                     migrations.Migration("good", "will pass", ok),
                 ]):
                ran = await migrations.run_pending_migrations()
            self.assertEqual(ran, 1)
            self.assertEqual(calls, ["ok"])
            self.assertEqual(db.ledger.written, ["good"])  # 'bad' retries next boot
        asyncio.run(run())

    def test_no_db_runs_nothing(self):
        async def run():
            with mock.patch.object(migrations, "_get_db", return_value=None):
                self.assertEqual(await migrations.run_pending_migrations(), 0)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import buddy_store


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    async def to_list(self, length=None):
        return self._docs[:length] if length is not None else list(self._docs)


class _FakeCollection:
    """In-memory stand-in for the Motor collection API buddy_store uses."""

    def __init__(self):
        self.docs = []
        self.indexes = []  # (args, kwargs) per create_index call

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))
        return None

    def _match(self, doc, flt):
        for key, value in flt.items():
            if isinstance(value, dict) and "$in" in value:
                if doc.get(key) not in value["$in"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True

    async def find_one(self, flt, projection=None):
        for doc in self.docs:
            if self._match(doc, flt):
                return dict(doc)
        return None

    def find(self, flt, projection=None, sort=None):
        docs = [dict(d) for d in self.docs if self._match(d, flt)]
        for key, direction in reversed(sort or []):
            docs.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return _FakeCursor(docs)

    async def update_one(self, flt, update, upsert=False):
        doc = next((d for d in self.docs if self._match(d, flt)), None)
        if doc is None:
            if not upsert:
                return
            doc = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            doc.update(update.get("$setOnInsert", {}))
            self.docs.append(doc)
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, value in update.get("$inc", {}).items():
            doc[key] = int(doc.get(key, 0) or 0) + value
        for key, value in update.get("$push", {}).items():
            items = doc.get(key, []) + list(value.get("$each", []))
            sl = value.get("$slice")
            if sl is not None:
                items = items[sl:] if sl < 0 else items[:sl]
            doc[key] = items

    async def delete_one(self, flt):
        for i, doc in enumerate(self.docs):
            if self._match(doc, flt):
                del self.docs[i]
                return

    async def delete_many(self, flt):
        self.docs = [d for d in self.docs if not self._match(d, flt)]


class _FakeDb:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _FakeCollection())


class BuddyStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = _FakeDb()
        patcher = patch.object(buddy_store, "_get_db", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        indexed = patch.object(buddy_store, "_indexed", False)
        indexed.start()
        self.addCleanup(indexed.stop)

    @property
    def sessions(self):
        return self.db["buddy_sessions"].docs

    async def test_flag_defaults_off_and_round_trips(self):
        self.assertFalse(await buddy_store.get_enabled(7))
        self.assertTrue(await buddy_store.set_enabled(7, True))
        self.assertTrue(await buddy_store.get_enabled(7))
        self.assertTrue(await buddy_store.set_enabled(7, False))
        self.assertFalse(await buddy_store.get_enabled(7))

    async def test_store_unavailable_degrades_safely(self):
        with patch.object(buddy_store, "_get_db", return_value=None):
            self.assertFalse(buddy_store.is_available())
            self.assertFalse(await buddy_store.get_enabled(7))
            self.assertFalse(await buddy_store.set_enabled(7, True))
            self.assertEqual(await buddy_store.get_history(7, "general"), [])
            self.assertFalse(await buddy_store.append_exchange(7, "general", "hi", "yo"))

    async def test_history_pairs_and_cap_at_40(self):
        for i in range(25):
            self.assertTrue(
                await buddy_store.append_exchange(7, "m:1", f"u{i}", f"b{i}")
            )
        history = await buddy_store.get_history(7, "m:1")
        # 25 exchanges = 50 messages, capped to the newest 40.
        self.assertEqual(len(history), 40)
        self.assertEqual(history[0], {"role": "user", "text": "u5", "t": history[0]["t"]})
        self.assertEqual(history[-1]["text"], "b24")
        self.assertEqual(history[-1]["role"], "buddy")
        self.assertEqual(
            [m["role"] for m in history[:4]], ["user", "buddy", "user", "buddy"],
        )

    async def test_sessions_evicted_oldest_touched_beyond_cap(self):
        for i in range(25):
            self.assertTrue(
                await buddy_store.append_exchange(7, f"i:item-{i}", f"u{i}", f"b{i}")
            )
        self.assertEqual(len(self.sessions), 20)
        keys = {d["session_key"] for d in self.sessions}
        self.assertIn("i:item-24", keys)
        # The five oldest-touched sessions are gone.
        for i in range(5):
            self.assertNotIn(f"i:item-{i}", keys)
        self.assertEqual(await buddy_store.get_history(7, "i:item-0"), [])
        self.assertEqual(len(await buddy_store.get_history(7, "i:item-24")), 2)

    async def test_users_are_isolated(self):
        await buddy_store.set_enabled(7, True)
        await buddy_store.append_exchange(7, "general", "hi", "yo")
        self.assertFalse(await buddy_store.get_enabled(8))
        self.assertEqual(await buddy_store.get_history(8, "general"), [])

    async def test_ttl_scoped_to_sessions_only(self):
        # The prefs flag is a durable setting — a TTL would silently switch an
        # active user's buddy off while sessions outlive it.
        await buddy_store.get_enabled(7)  # triggers _ensure_indexes
        prefs_indexes = self.db["buddy_prefs"].indexes
        self.assertTrue(any(a == ("user_id",) for a, _ in prefs_indexes))
        self.assertFalse(any("expireAfterSeconds" in kw for _, kw in prefs_indexes))
        session_indexes = self.db["buddy_sessions"].indexes
        self.assertTrue(any("expireAfterSeconds" in kw for _, kw in session_indexes))

    async def test_daily_quota_under_at_over_and_reset(self):
        # under the limit
        self.assertTrue(await buddy_store.consume_daily(7, 3))
        self.assertTrue(await buddy_store.consume_daily(7, 3))
        self.assertTrue(await buddy_store.consume_daily(7, 3))
        # at the limit -> denied
        self.assertFalse(await buddy_store.consume_daily(7, 3))
        self.assertFalse(await buddy_store.consume_daily(7, 3))
        doc = self.db["buddy_prefs"].docs[0]
        self.assertEqual(doc["day_count"], 3)
        # date rollover resets the counter
        doc["day"] = "1999-01-01"
        self.assertTrue(await buddy_store.consume_daily(7, 3))
        self.assertNotEqual(doc["day"], "1999-01-01")
        self.assertEqual(doc["day_count"], 1)

    async def test_daily_quota_fail_closed_when_store_down(self):
        with patch.object(buddy_store, "_get_db", return_value=None):
            self.assertFalse(await buddy_store.consume_daily(7, 100))

    async def test_delete_history_single_session_and_all(self):
        for key in ("general", "m:1", "i:movie:x"):
            await buddy_store.append_exchange(7, key, "u", "b")
        await buddy_store.append_exchange(8, "general", "u", "b")

        self.assertTrue(await buddy_store.delete_history(7, "m:1"))
        keys = {d["session_key"] for d in self.sessions if d["user_id"] == 7}
        self.assertEqual(keys, {"general", "i:movie:x"})

        self.assertTrue(await buddy_store.delete_history(7))
        self.assertEqual([d for d in self.sessions if d["user_id"] == 7], [])
        # other users untouched
        self.assertEqual(len([d for d in self.sessions if d["user_id"] == 8]), 1)

    async def test_delete_history_fail_closed_when_store_down(self):
        with patch.object(buddy_store, "_get_db", return_value=None):
            self.assertFalse(await buddy_store.delete_history(7))


class SessionKeyTest(unittest.TestCase):
    def test_derivation(self):
        self.assertEqual(buddy_store.session_key_for(), "general")
        self.assertEqual(buddy_store.session_key_for(None, None), "general")
        self.assertEqual(buddy_store.session_key_for("movie:heat-1995"), "i:movie:heat-1995")
        self.assertEqual(buddy_store.session_key_for("movie:x", 42), "m:42")
        self.assertEqual(buddy_store.session_key_for(None, 42), "m:42")
        self.assertEqual(buddy_store.session_key_for("movie:x", "junk"), "i:movie:x")


if __name__ == "__main__":
    unittest.main()

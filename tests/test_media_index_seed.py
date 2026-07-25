import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils import media_index


class _Store:
    def __init__(self):
        self.set_meta = AsyncMock()


class _Bot:
    def __init__(self, latest_id: int, *, fail_fetch: bool = False):
        self.latest_id = latest_id
        self.fail_fetch = fail_fetch
        self.requested_ids: list[list[int]] = []

    async def send_message(self, _channel_id, _text):
        return SimpleNamespace(id=self.latest_id)

    async def delete_messages(self, _channel_id, _message_id):
        return 1

    async def get_messages(self, _channel_id, ids):
        self.requested_ids.append(list(ids))
        if self.fail_fetch:
            raise RuntimeError("Telegram unavailable")
        return [SimpleNamespace(id=message_id, empty=True) for message_id in ids]


class MediaIndexSeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.items = dict(media_index._items)
        self.hash_map = dict(media_index._hash_map)
        self.seeded = media_index._seeded
        self.latest = media_index._latest_seen_id
        self.reconcile_cursor = media_index._reconcile_cursor
        self.catalogue_ready = media_index._catalogue_ready
        self.pending_deletions = set(media_index._pending_bin_deletions)
        self.reconcile_state = dict(media_index._reconcile_state)
        self.seed_state = dict(media_index._seed_state)
        self.store = media_index._store
        media_index._items.clear()
        media_index._hash_map.clear()
        media_index._seeded = False
        media_index._seed_state.update({
            "running": False, "scanned": 0, "total": 0, "indexed": 0,
            "started_at": 0.0, "finished_at": 0.0, "mode": "idle", "failed": False,
        })

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self.items)
        media_index._hash_map.clear()
        media_index._hash_map.update(self.hash_map)
        media_index._seeded = self.seeded
        media_index._latest_seen_id = self.latest
        media_index._reconcile_cursor = self.reconcile_cursor
        media_index._catalogue_ready = self.catalogue_ready
        media_index._pending_bin_deletions.clear()
        media_index._pending_bin_deletions.update(self.pending_deletions)
        media_index._reconcile_state.clear()
        media_index._reconcile_state.update(self.reconcile_state)
        media_index._seed_state.clear()
        media_index._seed_state.update(self.seed_state)
        media_index._store = self.store

    async def test_warm_seed_scans_only_delta_and_overlap_then_persists_cursor(self):
        store = _Store()
        media_index._store = store
        media_index._items[1000] = SimpleNamespace(message_id=1000, secure_hash="existing")
        media_index._latest_seen_id = 1000
        bot = _Bot(1001)

        with (
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_item_from_message", return_value=None),
        ):
            await media_index.seed(bot, -100)

        requested = [message_id for batch in bot.requested_ids for message_id in batch]
        self.assertEqual(media_index._seed_state["mode"], "delta")
        self.assertEqual(len(requested), media_index._SEED_OVERLAP + 2)
        self.assertEqual(min(requested), 1000 - media_index._SEED_OVERLAP)
        self.assertEqual(media_index._latest_seen_id, 1001)
        store.set_meta.assert_awaited_once_with("latest_seen_id", 1001)

    async def test_cold_seed_retains_the_bounded_full_recovery_window(self):
        media_index._store = None
        media_index._latest_seen_id = 0
        bot = _Bot(1001)

        with (
            patch.object(media_index, "_load"),
            patch.object(media_index, "restore_from_telegram", new=AsyncMock(return_value=False)),
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_item_from_message", return_value=None),
        ):
            await media_index.seed(bot, -100)

        requested = [message_id for batch in bot.requested_ids for message_id in batch]
        self.assertEqual(media_index._seed_state["mode"], "full")
        self.assertEqual(len(requested), media_index._SEED_DEPTH + 1)
        self.assertEqual(media_index._latest_seen_id, 1001)

    async def test_failed_seed_does_not_advance_the_durable_cursor(self):
        store = _Store()
        media_index._store = store
        media_index._items[1000] = SimpleNamespace(message_id=1000, secure_hash="existing")
        media_index._latest_seen_id = 1000
        bot = _Bot(1001, fail_fetch=True)

        with patch.object(media_index, "_persist_unlocked"):
            await media_index.seed(bot, -100)

        self.assertTrue(media_index._seed_state["failed"])
        self.assertEqual(media_index._latest_seen_id, 1000)
        store.set_meta.assert_not_awaited()

    async def test_deletions_received_during_recovery_are_drained_after_ready(self):
        media_index._items[99] = SimpleNamespace(message_id=99, secure_hash="deleted")
        media_index._hash_map["deleted"] = 99
        media_index._catalogue_ready = False

        with patch.object(media_index, "_persist_unlocked"), patch.object(media_index, "schedule_snapshot"):
            removed_early = await media_index.record_bin_deletions([99])
            await media_index._mark_catalogue_ready()

        self.assertEqual(removed_early, 0)
        self.assertNotIn(99, media_index._items)
        self.assertNotIn("deleted", media_index._hash_map)

    async def test_incremental_reconcile_removes_only_empty_messages_and_advances_cursor(self):
        media_index._items[10] = SimpleNamespace(message_id=10, secure_hash="live")
        media_index._items[11] = SimpleNamespace(message_id=11, secure_hash="gone")
        media_index._hash_map.update({"live": 10, "gone": 11})
        media_index._catalogue_ready = True

        class ReconcileBot:
            async def get_messages(self, _channel_id, ids):
                return [
                    SimpleNamespace(id=ids[0], empty=False),
                    SimpleNamespace(id=ids[1], empty=True),
                ]

        with patch.object(media_index, "_persist_unlocked"), patch.object(media_index, "schedule_snapshot"):
            removed = await media_index.reconcile_next_batch(ReconcileBot(), -100)

        self.assertEqual(removed, 1)
        self.assertIn(10, media_index._items)
        self.assertNotIn(11, media_index._items)
        self.assertEqual(media_index._reconcile_cursor, 11)

    async def test_failed_reconcile_does_not_advance_cursor(self):
        media_index._items[20] = SimpleNamespace(message_id=20, secure_hash="live")
        media_index._reconcile_cursor = 19

        class FailingBot:
            async def get_messages(self, _channel_id, _ids):
                raise RuntimeError("Telegram unavailable")

        with patch.object(media_index, "_persist_unlocked"):
            removed = await media_index.reconcile_next_batch(FailingBot(), -100)

        self.assertEqual(removed, 0)
        self.assertEqual(media_index._reconcile_cursor, 19)
        self.assertEqual(media_index.reconciliation_state()["last_error"], "RuntimeError")

    async def test_stream_miss_is_removed_only_after_a_confirmation_fetch(self):
        media_index._items[30] = SimpleNamespace(message_id=30, secure_hash="missing")
        media_index._hash_map["missing"] = 30
        media_index._catalogue_ready = True

        requested_ids: list[int] = []

        class MissingBot:
            async def get_messages(self, _channel_id, message_id):
                requested_ids.append(message_id)
                return SimpleNamespace(id=30, empty=True)

        with patch.object(media_index, "_persist_unlocked"), patch.object(media_index, "schedule_snapshot"):
            removed = await media_index.confirm_and_remove_missing(MissingBot(), -100, 30)

        self.assertTrue(removed)
        self.assertEqual(requested_ids, [30])
        self.assertNotIn(30, media_index._items)

    async def test_stream_miss_is_retained_when_confirmation_is_unavailable(self):
        media_index._items[31] = SimpleNamespace(message_id=31, secure_hash="uncertain")
        media_index._hash_map["uncertain"] = 31
        media_index._catalogue_ready = True

        class UnavailableBot:
            async def get_messages(self, _channel_id, _message_id):
                raise RuntimeError("Telegram unavailable")

        retained = await media_index.confirm_and_remove_missing(UnavailableBot(), -100, 31)

        self.assertFalse(retained)
        self.assertIn(31, media_index._items)

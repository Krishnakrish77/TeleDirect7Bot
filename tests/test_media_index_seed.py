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

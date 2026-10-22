import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import media_index


class _Store:
    def __init__(self):
        self.set_meta = AsyncMock()

    async def load_all(self):
        return []

    async def upsert_many(self, docs):
        pass

    async def upsert(self, doc):
        pass

    async def remove(self, message_id):
        pass


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

    async def test_seed_persists_discovered_rows_to_the_durable_store(self):
        """Seed-scan discoveries must reach Mongo. The in-memory insert dies
        with the process, and /tmp JSON is disabled when Mongo is active —
        without the bulk upsert, rescan-recovered rows are lost on restart
        while the delta seed's high-water mark keeps skipping them forever."""
        class UpsertSpyStore(_Store):
            def __init__(self):
                super().__init__()
                self.upserted: dict[int, dict] = {}

            async def load_all(self):
                return []

            async def upsert_many(self, docs):
                for d in docs:
                    self.upserted[d["message_id"]] = d

        store = UpsertSpyStore()
        media_index._store = store

        # BIN holds two media messages inside the scan window; the catalogue
        # holds neither (the exact post-crash state a rescan must repair).
        def _media_msg(mid):
            return SimpleNamespace(
                id=mid, empty=False, video=None, document=SimpleNamespace(
                    file_unique_id=f"AgADbulk{mid}", file_size=1_000 + mid,
                    file_name=f"Ted Lasso S01E{mid}.mkv",
                    mime_type="video/x-matroska", duration=1800, thumbs=[],
                ),
                audio=None, animation=None, caption="",
                from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=-100),
            )

        class BulkBot(_Bot):
            async def get_messages(self, _channel_id, ids):
                self.requested_ids.append(list(ids))
                return [_media_msg(i) for i in ids]

        bot = BulkBot(1050)
        with (
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_item_from_message",
                         side_effect=media_index._item_from_message),
        ):
            await media_index.seed(bot, -100, full_reconcile=True)

        # Every discovery in the deep window is durable, not just in-memory.
        # Window = [latest-800, latest]; all covered ids got upserted.
        self.assertTrue(store.upserted)
        self.assertTrue(set(store.upserted) >= {1049, 1050})
        self.assertEqual(store.upserted[1050]["title"], "Ted Lasso S01E 1050")

    async def test_seed_indexes_files_whose_note_is_channel_attribution(self):
        """Group/Channel notes record the media's source, not the sender's
        identity — an admin forwarding from their own channel produces one.
        They must not mark the file non-admin, or every seed scan deletes
        the row and refuses to re-index it (bulk uploads showing 1 of N)."""
        class UpsertSpyStore(_Store):
            def __init__(self):
                super().__init__()
                self.upserted: dict[int, dict] = {}

            async def load_all(self):
                return []

            async def upsert_many(self, docs):
                for d in docs:
                    self.upserted[d["message_id"]] = d

        store = UpsertSpyStore()
        media_index._store = store

        file_msg = SimpleNamespace(
            id=1050, empty=False, video=None, document=SimpleNamespace(
                file_unique_id="AgADchan1050", file_size=145892810,
                file_name="Ted.Lasso.S01E08.[@TVseriesLand]720p.MKV",
                mime_type="video/x-matroska", duration=1928, thumbs=[],
            ),
            audio=None, animation=None, caption="",
            from_user=None, chat=SimpleNamespace(id=-100),
        )
        channel_note = SimpleNamespace(
            id=1100, empty=False, video=None, document=None, audio=None,
            animation=None, caption=None,
            text="**Channel Name:** `X`\n**Channel ID:** `-100123`\n**Request URL:** t.me/x",
            reply_to_message_id=1050, chat=SimpleNamespace(id=-100),
        )

        class ChannelNoteBot(_Bot):
            async def get_messages(self, _channel_id, ids):
                self.requested_ids.append(list(ids))
                out = []
                for i in ids:
                    if i == 1050:
                        out.append(file_msg)
                    elif i == 1100:
                        out.append(channel_note)
                    else:
                        out.append(SimpleNamespace(id=i, empty=True))
                return out

        bot = ChannelNoteBot(1100)
        with (
            patch.object(media_index, "_persist_unlocked"),
            patch.object(media_index, "_item_from_message",
                         side_effect=media_index._item_from_message),
        ):
            await media_index.seed(bot, -100, full_reconcile=True)

        item = media_index._items.get(1050)
        self.assertIsNotNone(item)
        self.assertEqual(item.series_key, "ted-lasso")
        self.assertEqual(item.episode, 8)
        # …and it is durable, so the recovery survives restart.
        self.assertIn(1050, store.upserted)

    async def test_deletions_received_during_recovery_are_drained_after_ready(self):
        media_index._items[99] = SimpleNamespace(message_id=99, secure_hash="deleted")
        media_index._hash_map["deleted"] = 99
        media_index._catalogue_ready = False

        with patch.object(media_index, "_persist_unlocked"):
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

        with patch.object(media_index, "_persist_unlocked"):
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

        with patch.object(media_index, "_persist_unlocked"):
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

import os
import asyncio
import unittest
from types import SimpleNamespace


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.bot.plugins import stream
from main.utils import media_index


class FakeMedia:
    file_unique_id = "AgADduplicateFile"
    file_size = 4096
    file_name = "duplicate.mp4"
    mime_type = "video/mp4"


class FakeMessage:
    def __init__(self, message_id: int = 10):
        self.id = message_id
        self.document = FakeMedia()
        self.video = None
        self.audio = None
        self.animation = None
        self.voice = None
        self.video_note = None
        self.photo = None
        self.sticker = None
        self.caption = ""
        self.empty = False
        self.from_user = SimpleNamespace(id=1)  # OWNER_ID → admin
        self.copy_count = 0

    async def copy(self, chat_id: int):
        self.copy_count += 1
        copied = FakeMessage(900)
        copied.copy_count = self.copy_count
        copied.chat = SimpleNamespace(id=chat_id)
        return copied


class FakeBot:
    def __init__(self, store=None):
        self._store = store or {}

    async def get_messages(self, chat_id: int, message_id: int):
        return self._store.get(message_id) or SimpleNamespace(empty=True)

    async def edit_message_caption(self, **kwargs):
        return True


class StreamDedupeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._items = dict(media_index._items)
        self._pending = dict(stream._pending_bin_copies)
        media_index._items.clear()
        stream._pending_bin_copies.clear()

    async def asyncTearDown(self):
        media_index._items.clear()
        media_index._items.update(self._items)
        stream._pending_bin_copies.clear()
        stream._pending_bin_copies.update(self._pending)

    async def test_reuses_successful_copy_reservation_before_index_updates(self):
        source = FakeMessage()
        bot = FakeBot()

        first_msg, first_reused = await stream._copy_or_reuse_bin_message(bot, source)
        second_msg, second_reused = await stream._copy_or_reuse_bin_message(bot, source)

        self.assertFalse(first_reused)
        self.assertTrue(second_reused)
        self.assertEqual(source.copy_count, 1)
        self.assertEqual(first_msg.id, 900)
        self.assertEqual(second_msg.id, 900)

    async def test_reused_bin_without_catalogue_entry_self_heals_index(self):
        """A dedupe-reused bin whose creator index task died must still get
        indexed: _schedule_index_if_admin re-schedules instead of skipping."""
        source = FakeMessage()
        source.from_user = SimpleNamespace(id=1)  # OWNER_ID → admin
        bin_msg, _reused = await stream._copy_or_reuse_bin_message(FakeBot(), source)
        bin_msg.chat = SimpleNamespace(id=-1001)
        bot = FakeBot({bin_msg.id: bin_msg})
        self.assertIsNone(media_index.get_item(bin_msg.id))

        # Creator's index never landed (dropped task); the reuse caller now
        # invokes the scheduler on the same bin message.
        stream._schedule_index_if_admin(bot, source, bin_msg)
        # index_bin_message runs in a fire-and-forget task; give it a tick.
        for _ in range(50):
            if media_index.get_item(bin_msg.id) is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(media_index.get_item(bin_msg.id))

        # Idempotent: a second call must not duplicate or corrupt state.
        stream._schedule_index_if_admin(bot, source, bin_msg)
        self.assertEqual(media_index.size(), 1)

    async def test_non_admin_reused_bin_stays_out_of_catalogue(self):
        source = FakeMessage()
        source.from_user = SimpleNamespace(id=1)  # admin so the scheduler runs
        bin_msg, _reused = await stream._copy_or_reuse_bin_message(FakeBot(), source)

        # Non-admin caller: same bin message, but not the owner.
        non_admin = FakeMessage()
        non_admin.from_user = SimpleNamespace(id=42)
        stream._schedule_index_if_admin(FakeBot(), non_admin, bin_msg)
        await asyncio.sleep(0.05)
        self.assertIsNone(media_index.get_item(bin_msg.id))


if __name__ == "__main__":
    unittest.main()

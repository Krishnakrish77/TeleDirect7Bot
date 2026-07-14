import os
import unittest
from types import SimpleNamespace


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

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
        self.copy_count = 0

    async def copy(self, chat_id: int):
        self.copy_count += 1
        copied = FakeMessage(900)
        copied.copy_count = self.copy_count
        copied.chat = SimpleNamespace(id=chat_id)
        return copied


class FakeBot:
    async def get_messages(self, chat_id: int, message_id: int):
        return SimpleNamespace(empty=True)


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


if __name__ == "__main__":
    unittest.main()

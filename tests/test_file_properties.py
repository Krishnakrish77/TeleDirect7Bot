import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.file_properties import PROJECT_REPOSITORY_URL, gen_link
from pyrogram.enums import ButtonStyle


class _Media:
    file_name = "Example.Movie.2026.mkv"
    file_size = 1024
    file_unique_id = "AgADexampleFile"


class _Message:
    id = 42
    document = _Media()
    video = audio = animation = voice = video_note = photo = sticker = None


class LinkMarkupTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_link_message_includes_repository_button(self):
        markup, _, _ = await gen_link(_Message(), _Message(), from_channel=False)

        rows = markup.inline_keyboard
        self.assertEqual(rows[0][0].text, "▶ Watch")
        self.assertEqual(rows[0][0].style, ButtonStyle.PRIMARY)
        self.assertEqual(rows[1][0].text, "⌘ GitHub")
        self.assertEqual(rows[1][0].url, PROJECT_REPOSITORY_URL)
        self.assertEqual(rows[2][0].text, "🗑 Delete link")
        self.assertEqual(rows[2][0].style, ButtonStyle.DANGER)

    async def test_channel_link_message_includes_repository_button_without_delete(self):
        markup, _, _ = await gen_link(_Message(), _Message(), from_channel=True)

        rows = markup.inline_keyboard
        self.assertEqual([[button.text for button in row] for row in rows], [
            ["▶ Watch", "⬇ Download"],
            ["⌘ GitHub"],
        ])


if __name__ == "__main__":
    unittest.main()

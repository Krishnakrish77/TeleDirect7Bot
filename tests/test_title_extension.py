"""Title derivation must not leak file extensions into display names.

Books uploaded as ``Name.epub``/``Name.pdf`` (and audio ``.mp3``/``.flac``)
used to show the extension as the last title word — the noise-token list and
the trailing-extension strip were video-only."""
import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.dedup import clean_for_search
from main.utils.index_entry import title_from_filename


class TitleExtensionTest(unittest.TestCase):
    def test_book_extensions_stripped(self):
        for name in ("Atomic Habits.epub", "Atomic Habits.pdf", "The Hobbit.EPUB"):
            self.assertEqual(title_from_filename(name), "Atomic Habits" if "Atomic" in name else "The Hobbit")

    def test_audio_extensions_stripped(self):
        self.assertEqual(title_from_filename("Some Song.mp3"), "Some Song")
        self.assertEqual(title_from_filename("Some Song.flac"), "Some Song")

    def test_unknown_extension_stripped_from_title(self):
        self.assertEqual(title_from_filename("Book.zzz"), "Book")

    def test_search_string_also_clean(self):
        self.assertEqual(clean_for_search("Atomic Habits.epub"), "Atomic Habits")
        self.assertEqual(clean_for_search("Deep Work.pdf"), "Deep Work")

    def test_extension_word_inside_title_stripped_as_noise(self):
        # "epub" is in the noise-token list, so even a bare word gets treated
        # as release noise — same tradeoff as "ts" for video titles.
        self.assertEqual(title_from_filename("My epub adventures"), "My adventures")


if __name__ == "__main__":
    unittest.main()

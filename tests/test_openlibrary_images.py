import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.openlibrary_images import _content_type, _normalise_cover_id, cover_proxy_url


class OpenLibraryImageTests(unittest.TestCase):
    def test_cover_urls_use_a_same_origin_numeric_proxy(self):
        self.assertEqual(cover_proxy_url(42), "/api/openlibrary-cover/42")
        self.assertEqual(cover_proxy_url("00042"), "")
        self.assertEqual(cover_proxy_url("42/../../etc"), "")

    def test_cover_proxy_rejects_bad_ids_and_non_images(self):
        self.assertEqual(_normalise_cover_id("42"), 42)
        with self.assertRaises(ValueError):
            _normalise_cover_id("0")
        with self.assertRaises(ValueError):
            _content_type("text/html")
        self.assertEqual(_content_type("image/jpeg; charset=binary"), "image/jpeg")

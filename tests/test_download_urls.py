import unittest
import os


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.download_urls import as_download_url, is_download_query


class DownloadUrlsTest(unittest.TestCase):
    def test_as_download_url_adds_download_query(self):
        self.assertEqual(as_download_url("/abc123"), "/abc123?download=1")

    def test_as_download_url_preserves_existing_query_and_fragment(self):
        self.assertEqual(
            as_download_url("https://example.test/abc123?vt=token#top"),
            "https://example.test/abc123?vt=token&download=1#top",
        )

    def test_as_download_url_replaces_existing_download_value(self):
        self.assertEqual(
            as_download_url("/abc123?download=0&vt=token"),
            "/abc123?vt=token&download=1",
        )

    def test_is_download_query_accepts_explicit_truthy_values(self):
        self.assertTrue(is_download_query({"download": "1"}))
        self.assertTrue(is_download_query({"download": "true"}))
        self.assertTrue(is_download_query({"download": "yes"}))
        self.assertFalse(is_download_query({"download": "0"}))
        self.assertFalse(is_download_query({}))


if __name__ == "__main__":
    unittest.main()

import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.iptv_routes import _looks_like_m3u, _normalise_import_url


class IptvUrlImportTest(unittest.TestCase):
    def test_converts_github_blob_url_to_raw_url(self):
        self.assertEqual(
            _normalise_import_url("https://github.com/iptv-org/iptv/blob/master/streams/in.m3u"),
            "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/in.m3u",
        )

    def test_rejects_private_playlist_url(self):
        with self.assertRaises(ValueError):
            _normalise_import_url("http://127.0.0.1/private.m3u")

    def test_detects_m3u_content(self):
        self.assertTrue(_looks_like_m3u("#EXTM3U\n#EXTINF:-1,News\nhttps://example.test/stream.m3u8"))
        self.assertFalse(_looks_like_m3u("# Playlists\nhttps://iptv-org.github.io/iptv/categories/news.m3u"))


if __name__ == "__main__":
    unittest.main()

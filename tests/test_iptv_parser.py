import os
import sys
import types
import unittest
from pathlib import Path


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

ROOT = Path(__file__).resolve().parents[1]
main_pkg = types.ModuleType("main")
main_pkg.__path__ = [str(ROOT / "main")]
utils_pkg = types.ModuleType("main.utils")
utils_pkg.__path__ = [str(ROOT / "main" / "utils")]
main_pkg.utils = utils_pkg
sys.modules.setdefault("main", main_pkg)
sys.modules.setdefault("main.utils", utils_pkg)

from main.utils.iptv_parser import parse_m3u_text
from main.utils.iptv_store import parse_m3u


SAMPLE = """#EXTM3U x-tvg-url="https://example.test/guide.xml"
#EXTINF:-1 tvg-id="abc.us" tvg-name="ABC News" tvg-logo="https://example.test/logo.png" group-title="News",ABC News
#EXTVLCOPT:http-user-agent=TeleDirect Test
#EXTVLCOPT:http-referrer=https://ref.example/
https://example.test/abc.m3u8
#EXTINF:-1 group-title="Sports",Sports Live
https://example.test/sports.ts
"""


class IptvParserTest(unittest.TestCase):
    def test_parse_m3u_plus_metadata_and_extras(self):
        playlist = parse_m3u_text(SAMPLE)

        self.assertEqual(playlist.attrs["x-tvg-url"], "https://example.test/guide.xml")
        self.assertEqual(len(playlist.channels), 2)

        first = playlist.channels[0]
        self.assertEqual(first.name, "ABC News")
        self.assertEqual(first.stream_url, "https://example.test/abc.m3u8")
        self.assertEqual(first.logo_url, "https://example.test/logo.png")
        self.assertEqual(first.category, "News")
        self.assertEqual(first.tvg_id, "abc.us")
        self.assertEqual(first.tvg_name, "ABC News")
        self.assertIn("#EXTVLCOPT:http-user-agent=TeleDirect Test", first.extras)
        self.assertEqual(first.stream_headers["userAgent"], "TeleDirect Test")
        self.assertEqual(first.stream_headers["referrer"], "https://ref.example/")

        second = playlist.channels[1]
        self.assertEqual(second.name, "Sports Live")
        self.assertEqual(second.category, "Sports")
        self.assertEqual(second.stream_url, "https://example.test/sports.ts")

    def test_store_parse_preserves_richer_fields(self):
        channels = parse_m3u(SAMPLE)

        self.assertEqual(channels[0]["tvg_id"], "abc.us")
        self.assertEqual(channels[0]["tvg_name"], "ABC News")
        self.assertEqual(channels[0]["stream_headers"]["userAgent"], "TeleDirect Test")
        self.assertEqual(channels[0]["playlist_attrs"]["x-tvg-url"], "https://example.test/guide.xml")


if __name__ == "__main__":
    unittest.main()

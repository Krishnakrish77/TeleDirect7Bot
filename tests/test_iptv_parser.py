import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.iptv_parser import parse_m3u_text
from main.utils.iptv_store import _normalise_channel, parse_m3u


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

    def test_channel_update_preserves_imported_metadata_when_omitted(self):
        existing = _normalise_channel(parse_m3u(SAMPLE)[0])

        updated = _normalise_channel(
            {
                "channel_id": existing["channel_id"],
                "name": "ABC News HD",
                "stream_url": existing["stream_url"],
                "logo_url": existing["logo_url"],
                "category": "News",
                "enabled": False,
            },
            existing,
        )

        self.assertEqual(updated["name"], "ABC News HD")
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["tvg_id"], "abc.us")
        self.assertEqual(updated["stream_headers"]["userAgent"], "TeleDirect Test")
        self.assertIn("#EXTVLCOPT:http-referrer=https://ref.example/", updated["extras"])


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from ipaddress import ip_address


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.iptv_routes import (
    _LOGO_CACHE,
    _LOGO_PLACEHOLDER_SVG,
    _is_public_import_ip,
    _logo_content_type,
    _logo_cache_key,
    _looks_like_m3u,
    _normalise_logo_url,
    _normalise_import_url,
    _placeholder_logo_result,
    _rewrite_m3u_proxy_urls,
    _with_proxied_logo,
)


class IptvUrlImportTest(unittest.TestCase):
    def test_converts_github_blob_url_to_raw_url(self):
        self.assertEqual(
            _normalise_import_url("https://github.com/iptv-org/iptv/blob/master/streams/in.m3u"),
            "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/in.m3u",
        )

    def test_rejects_private_playlist_url(self):
        with self.assertRaises(ValueError):
            _normalise_import_url("http://127.0.0.1/private.m3u")

    def test_rejects_private_logo_url(self):
        with self.assertRaises(ValueError):
            _normalise_logo_url("http://127.0.0.1/logo.png")

    def test_rejects_non_public_resolved_addresses(self):
        self.assertFalse(_is_public_import_ip(ip_address("10.0.0.1")))
        self.assertFalse(_is_public_import_ip(ip_address("169.254.169.254")))
        self.assertTrue(_is_public_import_ip(ip_address("8.8.8.8")))

    def test_public_live_tv_logo_url_is_proxied(self):
        channel = {
            "id": "news",
            "name": "News",
            "logoUrl": "https://cdn.example.test/logos/news.png",
        }

        proxied = _with_proxied_logo(channel)

        self.assertEqual(channel["logoUrl"], "https://cdn.example.test/logos/news.png")
        self.assertRegex(proxied["logoUrl"], r"^/api/live-tv/logo/news\?v=[a-f0-9]{16}$")

    def test_logo_content_type_falls_back_by_image_extension(self):
        self.assertEqual(
            _logo_content_type("application/octet-stream", "https://cdn.example.test/logos/news.webp"),
            "image/webp",
        )
        self.assertEqual(
            _logo_content_type("text/plain", "https://cdn.example.test/logos/news.svg"),
            "image/svg+xml",
        )
        with self.assertRaises(ValueError):
            _logo_content_type("text/html", "https://cdn.example.test/not-a-logo")

    def test_placeholder_logo_result_is_negative_cached(self):
        _LOGO_CACHE.clear()
        logo_url = "https://cdn.example.test/missing-logo.png"

        content_type, body = _placeholder_logo_result("news", logo_url)

        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(body, _LOGO_PLACEHOLDER_SVG)
        self.assertIn(_logo_cache_key("news", logo_url), _LOGO_CACHE)

    def test_detects_m3u_content(self):
        self.assertTrue(_looks_like_m3u("#EXTM3U\n#EXTINF:-1,News\nhttps://example.test/stream.m3u8"))
        self.assertFalse(_looks_like_m3u("# Playlists\nhttps://iptv-org.github.io/iptv/categories/news.m3u"))

    def test_rewrites_same_origin_hls_subresources_to_proxy(self):
        text = (
            "#EXTM3U\n"
            '#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin"\n'
            "#EXTINF:4,\n"
            "segment1.ts\n"
            "#EXTINF:4,\n"
            "https://cdn.example.test/live/segment2.ts\n"
            "#EXTINF:4,\n"
            "https://other.example.test/live/segment3.ts\n"
        )

        rewritten = _rewrite_m3u_proxy_urls(
            text,
            channel_id="news",
            playlist_url="https://cdn.example.test/live/index.m3u8",
            source_url="https://cdn.example.test/live/index.m3u8",
        )

        self.assertIn("/api/live-tv/stream/news?url=https%3A%2F%2Fcdn.example.test%2Flive%2Fsegment1.ts", rewritten)
        self.assertIn('URI="/api/live-tv/stream/news?url=https%3A%2F%2Fcdn.example.test%2Flive%2Fkeys%2Fkey.bin"', rewritten)
        self.assertIn("https://other.example.test/live/segment3.ts", rewritten)


if __name__ == "__main__":
    unittest.main()

import os
import importlib
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.server.tmdb_images import (
    _TMDB_IMAGE_PLACEHOLDER_SVG,
    _cache_tmdb_image,
    _normalise_tmdb_image,
    _tmdb_image_cache,
    _tmdb_image_cache_key,
    _tmdb_image_content_type,
    _tmdb_placeholder_result,
    tmdb_image_url,
)
tmdb_images_module = importlib.import_module("main.server.tmdb_images")


class SpaTmdbImageTest(unittest.TestCase):
    def test_tmdb_image_uses_same_origin_proxy(self):
        self.assertEqual(
            tmdb_image_url("/abc_DEF-123.jpg", "w342"),
            "/api/tmdb-image/w342/abc_DEF-123.jpg",
        )
        self.assertEqual(tmdb_image_url("", "w342"), "")

    def test_rejects_invalid_tmdb_image_input(self):
        with self.assertRaises(ValueError):
            _normalise_tmdb_image("w999", "/poster.jpg")
        with self.assertRaises(ValueError):
            _normalise_tmdb_image("w342", "../poster.jpg")
        with self.assertRaises(ValueError):
            _normalise_tmdb_image("w342", "/poster.txt")

    def test_tmdb_image_content_type_uses_image_extension_fallback(self):
        self.assertEqual(
            _tmdb_image_content_type("application/octet-stream", "poster.webp"),
            "image/webp",
        )
        self.assertEqual(
            _tmdb_image_content_type("image/jpeg; charset=binary", "poster.jpg"),
            "image/jpeg",
        )
        with self.assertRaises(ValueError):
            _tmdb_image_content_type("text/html", "poster.jpg")

    def test_placeholder_result_is_cached(self):
        _tmdb_image_cache.clear()
        tmdb_images_module._tmdb_image_cache_bytes = 0

        content_type, body = _tmdb_placeholder_result("w342", "missing.jpg")

        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(body, _TMDB_IMAGE_PLACEHOLDER_SVG)
        self.assertIn(_tmdb_image_cache_key("w342", "missing.jpg"), _tmdb_image_cache)

    def test_placeholder_error_cache_ttl_is_short(self):
        # A transient TMDB failure must not pin the placeholder for hours:
        # both the in-memory TTL and the browser-facing Cache-Control use
        # the same short window so the next request retries the real image.
        self.assertLessEqual(tmdb_images_module._TMDB_IMAGE_ERROR_CACHE_TTL, 300)

    def test_proxy_serves_placeholder_with_short_cache_control(self):
        import asyncio
        from unittest.mock import patch

        from aiohttp.test_utils import make_mocked_request

        request = make_mocked_request("GET", "/api/tmdb-image/w342/missing.jpg", match_info={"size": "w342", "tail": "missing.jpg"})
        with patch.object(
            tmdb_images_module, "_fetch_tmdb_image", return_value=("image/svg+xml", _TMDB_IMAGE_PLACEHOLDER_SVG)
        ):
            response = asyncio.run(tmdb_images_module.tmdb_image_proxy(request))

        self.assertEqual(response.headers["Cache-Control"], f"public, max-age={tmdb_images_module._TMDB_IMAGE_ERROR_CACHE_TTL}")

    def test_proxy_serves_real_image_with_long_cache_control(self):
        import asyncio
        from unittest.mock import patch

        from aiohttp.test_utils import make_mocked_request

        request = make_mocked_request("GET", "/api/tmdb-image/w342/poster.jpg", match_info={"size": "w342", "tail": "poster.jpg"})
        with patch.object(
            tmdb_images_module, "_fetch_tmdb_image", return_value=("image/jpeg", b"fake-bytes")
        ):
            response = asyncio.run(tmdb_images_module.tmdb_image_proxy(request))

        self.assertEqual(
            response.headers["Cache-Control"],
            f"public, max-age={tmdb_images_module._TMDB_IMAGE_CACHE_TTL}",
        )

    def test_tmdb_image_cache_respects_total_byte_cap(self):
        old_max = tmdb_images_module._TMDB_IMAGE_CACHE_MAX_BYTES
        try:
            _tmdb_image_cache.clear()
            tmdb_images_module._tmdb_image_cache_bytes = 0
            tmdb_images_module._TMDB_IMAGE_CACHE_MAX_BYTES = 10

            _cache_tmdb_image("w342", "one.jpg", "image/jpeg", b"123456", 60)
            _cache_tmdb_image("w342", "two.jpg", "image/jpeg", b"abcdef", 60)

            self.assertLessEqual(tmdb_images_module._tmdb_image_cache_bytes, 10)
            self.assertEqual(len(_tmdb_image_cache), 1)
        finally:
            tmdb_images_module._TMDB_IMAGE_CACHE_MAX_BYTES = old_max
            _tmdb_image_cache.clear()
            tmdb_images_module._tmdb_image_cache_bytes = 0


if __name__ == "__main__":
    unittest.main()

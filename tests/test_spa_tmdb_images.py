import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server.spa_routes import (
    _TMDB_IMAGE_PLACEHOLDER_SVG,
    _normalise_tmdb_image,
    _tmdb_image,
    _tmdb_image_cache,
    _tmdb_image_cache_key,
    _tmdb_image_content_type,
    _tmdb_placeholder_result,
)


class SpaTmdbImageTest(unittest.TestCase):
    def test_tmdb_image_uses_same_origin_proxy(self):
        self.assertEqual(
            _tmdb_image("/abc_DEF-123.jpg", "w342"),
            "/api/tmdb-image/w342/abc_DEF-123.jpg",
        )
        self.assertEqual(_tmdb_image("", "w342"), "")

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

        content_type, body = _tmdb_placeholder_result("w342", "missing.jpg")

        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(body, _TMDB_IMAGE_PLACEHOLDER_SVG)
        self.assertIn(_tmdb_image_cache_key("w342", "missing.jpg"), _tmdb_image_cache)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp.test_utils import make_mocked_request


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server import openlibrary_images
from main.server.openlibrary_images import _FALLBACK_COVER, _content_type, _normalise_cover_id, cover_proxy_url


class _UnavailableSession:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        raise aiohttp.ClientError("upstream unavailable")


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


class OpenLibraryImageProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_upstream_artwork_has_an_image_fallback(self):
        openlibrary_images._cache.clear()
        request = make_mocked_request("GET", "/api/openlibrary-cover/42", match_info={"cover_id": "42"})
        with patch.object(openlibrary_images.aiohttp, "ClientSession", _UnavailableSession):
            response = await openlibrary_images.openlibrary_cover_proxy(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/svg+xml")
        self.assertIn("max-age=600", response.headers["Cache-Control"])
        self.assertTrue(_FALLBACK_COVER.startswith(b"<svg"))
        self.assertIn(b"TELEDIRECT", _FALLBACK_COVER)

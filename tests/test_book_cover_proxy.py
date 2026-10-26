import os
import unittest
from unittest.mock import patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from aiohttp.test_utils import make_mocked_request

from main.server import google_books_images, openlibrary_images


class _FakeContent:
    """Mirrors aiohttp's StreamReader: iter_chunked yields until EOF, while
    read(n) may return early with a partial buffer (the original bug)."""

    def __init__(self, body: bytes, chunk_size: int = 1000):
        self._body = body
        self._chunk_size = chunk_size

    async def iter_chunked(self, _size: int):
        for start in range(0, len(self._body), self._chunk_size):
            yield self._body[start:start + self._chunk_size]

    async def read(self, _n: int = -1):
        return self._body[:self._chunk_size]


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "image/jpeg", declared: int | None = None, status: int = 200):
        self.status = status
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body) if declared is None else declared),
        }
        self.content = _FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    response: _FakeResponse

    def __init__(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        return type(self).response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _request(match_info: dict):
    return make_mocked_request("GET", "/cover", match_info=match_info)


class CoverProxyStreamingTest(unittest.IsolatedAsyncioTestCase):
    async def test_openlibrary_returns_every_byte(self):
        # 53 KB cover: the old `content.read(n)` path served only the first
        # buffered chunk, so browsers painted a half image.
        body = bytes(range(256)) * 207  # 52,992 bytes, several chunks
        with patch.object(openlibrary_images.aiohttp, "ClientSession", type("S", (_FakeSession,), {"response": _FakeResponse(body)})):
            response = await openlibrary_images.openlibrary_cover_proxy(_request({"cover_id": "7436634"}))
        self.assertEqual(response.content_type, "image/jpeg")
        self.assertEqual(len(response.body), len(body))
        self.assertEqual(response.body, body)

    async def test_google_returns_every_byte(self):
        body = b"\xff\xd8" + b"x" * 40_000 + b"\xff\xd9"
        with patch.object(google_books_images.aiohttp, "ClientSession", type("S", (_FakeSession,), {"response": _FakeResponse(body)})):
            response = await google_books_images.google_books_cover_proxy(_request({"volume_id": "uSUmEQAAQBAJ"}))
        self.assertEqual(len(response.body), len(body))
        self.assertEqual(response.body, body)

    async def test_truncated_upstream_falls_back_to_placeholder(self):
        body = b"jpeg-bytes" * 100
        truncated = _FakeResponse(body, declared=len(body) * 3)
        with patch.object(openlibrary_images.aiohttp, "ClientSession", type("S", (_FakeSession,), {"response": truncated})):
            response = await openlibrary_images.openlibrary_cover_proxy(_request({"cover_id": "7436634"}))
        self.assertEqual(response.content_type, "image/svg+xml")
        self.assertEqual(response.body, openlibrary_images._FALLBACK)

    async def test_large_cover_falls_back_to_placeholder(self):
        oversized = _FakeResponse(b"x" * (openlibrary_images._MAX_BYTES + 10))
        with patch.object(openlibrary_images.aiohttp, "ClientSession", type("S", (_FakeSession,), {"response": oversized})):
            response = await openlibrary_images.openlibrary_cover_proxy(_request({"cover_id": "7436634"}))
        self.assertEqual(response.content_type, "image/svg+xml")

    async def test_invalid_cover_id_is_404(self):
        from aiohttp import web
        with self.assertRaises(web.HTTPNotFound):
            await openlibrary_images.openlibrary_cover_proxy(_request({"cover_id": "not-numeric"}))


class CoverUrlVersionTest(unittest.TestCase):
    def test_cover_urls_carry_cache_busting_version(self):
        from main.server.spa_routes import _book_cover_proxy_url
        from main.utils.hub_query import HubItem

        base = dict(message_id=1, secure_hash="h", title="T", year=None, description="",
                    tags=[], duration=0, file_size=0, has_thumb=False, media_kind="book")
        self.assertEqual(
            _book_cover_proxy_url(HubItem(**base, book_cover_url="openlibrary-cover:7436634")),
            "/api/openlibrary-cover/7436634?v=2",
        )
        self.assertEqual(
            _book_cover_proxy_url(HubItem(**base, book_cover_url="google-books:uSUmEQAAQBAJ")),
            "/api/google-books-cover/uSUmEQAAQBAJ?v=2",
        )
        self.assertEqual(_book_cover_proxy_url(HubItem(**base, book_cover_url="")), "")


if __name__ == "__main__":
    unittest.main()

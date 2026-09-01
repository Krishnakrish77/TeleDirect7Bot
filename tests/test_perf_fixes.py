"""Regression tests for the performance/memory fixes.

Covers three contracts that ship with the audit fixes and previously had
no coverage:

  1. The gzip middleware never double-compresses a response that already
     carries ``Content-Encoding: gzip`` (pre-gzipped cached SPA payloads)
     and compresses large JSON off the event loop (executor).
  2. ``media_index._persist_unlocked`` defers and coalesces the full
     catalogue dump, while ``persist_now`` still flushes immediately.
  3. The derived art-bucket map used by /api/hub is rebuilt only after
     catalogue invalidation, never rescanned per request.
"""
import asyncio
import gzip as gzlib
import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.server import gzip_middleware
from main.utils import media_index
from main.utils.hub_query import HubItem


def video_item(message_id: int, *, title: str = "Title", **overrides) -> HubItem:
    data = {
        "message_id": message_id,
        "secure_hash": f"hash{message_id}",
        "title": title,
        "year": 2024,
        "description": "",
        "tags": [],
        "duration": 7200,
        "file_size": 1024,
        "has_thumb": True,
        "quality": "720p",
        "file_name": f"{title}.mkv",
        "media_kind": "video",
    }
    data.update(overrides)
    return HubItem(**data)


class GzipMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    def _request(self, accept_gzip: bool = True):
        headers = {"Accept-Encoding": "gzip"} if accept_gzip else {}
        return make_mocked_request("GET", "/api/hub", headers=headers)

    async def test_pre_gzipped_response_is_not_double_compressed(self):
        body = b"\x1f\x8b-synthetic-gzip-bytes"
        original = web.Response(body=body, headers={"Content-Encoding": "gzip"})
        response = await gzip_middleware(self._request(), AsyncMock(return_value=original))
        self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(response.body, body)  # untouched — no double gzip

    async def test_large_json_is_gzipped_and_round_trips(self):
        payload = '{"data": "' + "x" * 4096 + '"}'
        original = web.Response(text=payload, content_type="application/json")
        response = await gzip_middleware(self._request(), AsyncMock(return_value=original))
        self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(response.headers["Content-Length"], str(len(response.body)))
        self.assertEqual(gzlib.decompress(response.body).decode(), payload)

    async def test_small_bodies_are_passthrough(self):
        original = web.Response(text="{}", content_type="application/json")
        response = await gzip_middleware(self._request(), AsyncMock(return_value=original))
        self.assertNotIn("Content-Encoding", response.headers)

    async def test_non_gzip_client_is_passthrough(self):
        original = web.Response(text="x" * 4096, content_type="application/json")
        response = await gzip_middleware(self._request(accept_gzip=False), AsyncMock(return_value=original))
        self.assertEqual(response.body, original.body)
        self.assertNotIn("Content-Encoding", response.headers)


class MediaIndexDebounceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._items = dict(media_index._items)
        media_index._items.clear()
        self._old_debounce = media_index._PERSIST_DEBOUNCE
        self._old_task = media_index._persist_task
        media_index._PERSIST_DEBOUNCE = 0.05
        media_index._persist_dirty = False
        media_index._persist_task = None

    def tearDown(self):
        if media_index._persist_task is not None:
            media_index._persist_task.cancel()
            try:
                media_index._persist_task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                pass
        media_index._persist_task = self._old_task
        media_index._PERSIST_DEBOUNCE = self._old_debounce
        media_index._persist_dirty = False
        media_index._items.clear()
        media_index._items.update(self._items)

    async def test_mutations_coalesce_into_one_deferred_dump(self):
        with patch.object(media_index, "_persist_write_now") as write:
            media_index._persist_unlocked()
            media_index._persist_unlocked()
            media_index._persist_unlocked()
            write.assert_not_called()  # three mutations, zero immediate dumps
            await asyncio.sleep(0.15)
            write.assert_called_once_with()  # quiet window collapses them

    async def test_persist_now_flushes_immediately(self):
        with patch.object(media_index, "_persist_write_now") as write:
            media_index._persist_unlocked()
            write.assert_not_called()
            await media_index.persist_now()
            write.assert_called_once_with()


class ArtBucketCacheTest(unittest.TestCase):
    def setUp(self):
        self._items = dict(media_index._items)
        media_index._items.clear()
        media_index._invalidate_search_index()

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._items)
        media_index._invalidate_search_index()

    def test_bucket_cache_rebuilds_only_after_invalidation(self):
        first = video_item(1, series_key="castle", poster_path="/p.jpg")
        media_index._items[first.message_id] = first

        cache = media_index.group_art_cache_for([first])
        self.assertEqual(cache[("series", "castle")].message_id, first.message_id)

        better = video_item(2, series_key="castle", poster_path="/better.jpg")
        media_index._items[better.message_id] = better
        # Without invalidation the memoized map is retained (no rescan).
        stale = media_index.group_art_cache_for([first])
        self.assertEqual(stale[("series", "castle")].message_id, first.message_id)
        # A catalogue mutation invalidates: next call rescans once.
        media_index._invalidate_search_index()
        fresh = media_index.group_art_cache_for([first])
        self.assertEqual(fresh[("series", "castle")].message_id, better.message_id)


if __name__ == "__main__":
    unittest.main()

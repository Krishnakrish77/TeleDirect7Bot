"""Photos map/wipe/export — pure logic tests. No Mongo, no network:
query-shape scoping (owner filter), export serialization shape, and the
chunked-delete batching helper follow test_photo_geo.py conventions.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.server import photo_routes
from main.utils.photo_store import map_points, export_library


class _FakeCollection:
    """Captures the find() spec so the test asserts the query builder's
    owner scoping without a database. Honors the {_id: {$gt}} cursor so
    paginated callers terminate."""
    def __init__(self, docs):
        self._docs = list(docs)
        self.last_spec = None

    def find(self, spec, projection=None):
        self.last_spec = spec
        docs = self._docs
        gt = (spec.get("_id") or {}).get("$gt")
        if gt is not None:
            docs = [d for d in docs if str(d["_id"]) > str(gt)]

        class _Cursor:
            _docs = docs

            def sort(self, *_a, **_k):
                return self

            async def to_list(self, length):
                return self._docs

        return _Cursor()


class _FakeDb:
    def __init__(self, docs):
        self.photos = _FakeCollection(docs)

    def __getitem__(self, name):
        return {"photos": self.photos}[name]


def _point_doc(i=1, lat=38.7, lon=-9.1, deleted=False):
    return {
        "_id": f"oid{i}",
        "owner_user_id": 7,
        "message_id": 100 + i,
        "kind": "photo",
        "deleted": deleted,
        "gps": {"lat": lat, "lon": lon},
        "taken_at": None,
    }


class MapPointsScopingTest(unittest.IsolatedAsyncioTestCase):
    async def test_query_filters_owner_and_deleted(self):
        db = _FakeDb([_point_doc()])
        with patch("main.utils.photo_store._get_db", return_value=db), \
             patch("main.utils.photo_store._ensure_indexes", new=AsyncMock()):
            await map_points(7)
        spec = db.photos.last_spec
        self.assertEqual(spec["owner_user_id"], 7)
        self.assertFalse(spec["deleted"])
        self.assertIn("$ne", spec["gps"])

    async def test_taken_range_clauses(self):
        from datetime import datetime, timezone
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        before = datetime(2025, 1, 1, tzinfo=timezone.utc)
        db = _FakeDb([_point_doc()])
        with patch("main.utils.photo_store._get_db", return_value=db), \
             patch("main.utils.photo_store._ensure_indexes", new=AsyncMock()):
            await map_points(7, taken_after=after, taken_before=before)
        spec = db.photos.last_spec
        self.assertEqual(spec["taken_at"]["$gte"], after)
        self.assertEqual(spec["taken_at"]["$lt"], before)


class ExportShapeTest(unittest.IsolatedAsyncioTestCase):
    async def test_photo_then_album_lines_with_album_names(self):
        album = {
            "_id": "album1",
            "owner_user_id": 7,
            "name": "Trip",
            "created_at": None,
            "sort": 0,
        }
        photo = _point_doc()
        photo["album_ids"] = ["album1"]
        # export_library touches photos + photo_albums; hand back the fakes.
        class _Db2:
            def __init__(self):
                self.photo_docs = _FakeCollection([photo])
                self.album_docs = _FakeCollection([album])
            def __getitem__(self, name):
                return {"photos": self.photo_docs, "photo_albums": self.album_docs}[name]
        db2 = _Db2()
        with patch("main.utils.photo_store._get_db", return_value=db2), \
             patch("main.utils.photo_store._ensure_indexes", new=AsyncMock()):
            payloads = [p async for p in export_library(7)]
        photos = [p for p in payloads if "messageId" in p]
        albums = [p for p in payloads if "messageId" not in p]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["albumNames"], ["Trip"])
        self.assertEqual(photos[0]["id"], "oid1")
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["name"], "Trip")


class ChunkedDeleteTest(unittest.TestCase):
    def test_chunks_of_ninety(self):
        items = list(range(205))
        chunks = list(photo_routes._chunks(items, 90))
        self.assertEqual([len(c) for c in chunks], [90, 90, 25])

    def test_chunk_covers_all_items(self):
        items = list(range(95))
        flattened = [x for c in photo_routes._chunks(items, 90) for x in c]
        self.assertEqual(flattened, items)

    def test_chunked_helper_returns_doc_ids(self):
        """Regression: trash-empty once passed (channel_id, message_id)
        tuples into this helper (expects dicts) and tested membership
        against (channel_id, message_id) tuples (helper returns _ids) —
        the purge was dead code. Lock the contract: dict targets in,
        _id strings out, exactly what purge_photos consumes."""
        import asyncio

        docs = [
            {"_id": f"id{i}", "channel_id": -100, "message_id": 10 + i}
            for i in range(5)
        ]

        class FakeBot:
            def __init__(self):
                self.deleted = []

            async def delete_messages(self, channel_id, message_ids):
                self.deleted.extend((channel_id, mid) for mid in message_ids)

        ok, failed = asyncio.run(
            photo_routes._telegram_delete_chunked(FakeBot(), docs, "test")
        )
        self.assertEqual(ok, [f"id{i}" for i in range(5)])
        self.assertEqual(failed, [])
        # ok_ids feed purge_photos — it takes the same _id strings.
        self.assertTrue(all(isinstance(x, str) for x in ok))


if __name__ == "__main__":
    unittest.main()

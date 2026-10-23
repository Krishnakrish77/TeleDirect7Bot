"""Self-checks for the durable watched-badge store (wh_store badge layer).

No Mongo/network: badge identity is resolved against a mocked catalogue
item and the Mongo write shape is asserted against fake collections.
"""

from __future__ import annotations

import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

import asyncio
import unittest
from unittest import mock

from main.utils import wh_store


def _fake_item(series_key: str = "", message_id: int = 42):
    return type(
        "HubItem",
        (),
        {
            "message_id": message_id,
            "secure_hash": "h",
            "series_key": series_key,
            "title": "Kalki 2898 AD",
            "file_name": "k.mkv",
        },
    )()


class _FakeColl:
    def __init__(self, name: str):
        self.name = name
        self.upserts: list[tuple[dict, dict]] = []

    async def update_one(self, q, upd, upsert=False):
        self.upserts.append((dict(q), upd))

    async def insert_one(self, doc):
        pass

    class _Q:
        def skip(self, n):
            return self

        def limit(self, n):
            return self

        async def to_list(self, *args, **kwargs):
            return []

    def find(self, *args, **kwargs):
        return self._Q()


def _fake_db():
    return {name: _FakeColl(name) for name in
            ("watch_history", wh_store._EVENTS, wh_store._BADGES)}


class BadgeDocTest(unittest.TestCase):
    def test_movie_badge_carries_title_slug(self):
        import main.utils.media_index as mi
        item = _fake_item()
        with mock.patch.object(mi, "get_item", return_value=item):
            doc = wh_store._badge_doc(1, "h42", "Kalki 2898 AD (2024) 1080p WEB-DL")
        self.assertEqual(doc, {
            "user_id": 1, "cw_key": "h42",
            "title_slug": "kalki-2898-ad", "kind": "movie",
        })

    def test_series_badge_carries_series_slug(self):
        import main.utils.media_index as mi
        item = _fake_item(series_key="some-show")
        with mock.patch.object(mi, "get_item", return_value=item):
            doc = wh_store._badge_doc(1, "h42", "Some Show S01E01")
        self.assertEqual(doc["kind"], "series")
        self.assertEqual(doc["series_slug"], "some-show")

    def test_unknown_item_produces_no_badge(self):
        import main.utils.media_index as mi
        with mock.patch.object(mi, "get_item", return_value=None):
            self.assertIsNone(wh_store._badge_doc(1, "h99", "X"))


class BadgeWriteTest(unittest.TestCase):
    def test_record_triggers_badge_with_catalogue_title(self):
        async def run():
            captured = []
            with mock.patch.object(wh_store, "_get_db", return_value=_fake_db()), \
                 mock.patch.object(wh_store, "_ensure_indexes", new=mock.AsyncMock()), \
                 mock.patch.object(wh_store, "_badge_upsert_nowait",
                                   lambda uid, key, title: captured.append((uid, key, title))):
                await wh_store.record(7, "h42", "Kalki")
            self.assertEqual(captured, [(7, "h42", "Kalki")])
        asyncio.run(run())

    def test_badge_upsert_resolves_slug_and_writes(self):
        import main.utils.media_index as mi
        async def run():
            db = _fake_db()
            with mock.patch.object(wh_store, "_get_db", return_value=db), \
                 mock.patch.object(wh_store, "_ensure_indexes", new=mock.AsyncMock()), \
                 mock.patch.object(mi, "get_item", return_value=_fake_item()):
                wh_store._badge_upsert_nowait(7, "h42", "Kalki 2898 AD")
                await asyncio.sleep(0.05)
            (query, update), = db[wh_store._BADGES].upserts
            self.assertEqual(query, {"user_id": 7, "cw_key": "h42"})
            self.assertEqual(update["$set"]["title_slug"], "kalki-2898-ad")
        asyncio.run(run())

    def test_series_badge_upsert_shape(self):
        async def run():
            db = _fake_db()
            with mock.patch.object(wh_store, "_get_db", return_value=db), \
                 mock.patch.object(wh_store, "_ensure_indexes", new=mock.AsyncMock()), \
                 mock.patch("main.utils.rec_engine.normalize_title_key",
                            return_value="some-show"):
                await wh_store.record_series_badge(7, "some-show", "Some Show")
            (query, update), = db[wh_store._BADGES].upserts
            self.assertEqual(query, {"user_id": 7, "series_slug": "some-show"})
            self.assertEqual(update["$set"]["cw_key"], "series:some-show")
        asyncio.run(run())


class GetBadgesTest(unittest.TestCase):
    def test_get_badges_splits_keys_slugs_and_series(self):
        class _FindColl:
            def find(self, q, projection=None):
                return self

            async def to_list(self, *args, **kwargs):
                return [
                    {"cw_key": "h42", "title_slug": "kalki-2898-ad"},
                    {"cw_key": "series:some-show", "title_slug": "some-show",
                     "series_slug": "some-show"},
                ]

        async def run():
            with mock.patch.object(wh_store, "_get_db",
                                   return_value={wh_store._BADGES: _FindColl()}), \
                 mock.patch.object(wh_store, "_ensure_indexes", new=mock.AsyncMock()):
                keys, slugs, series = await wh_store.get_badges(7)
            self.assertEqual(keys, {"h42", "series:some-show"})
            self.assertEqual(slugs, {"kalki-2898-ad", "some-show"})
            self.assertEqual(series, {"some-show"})
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

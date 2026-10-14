"""Self-checks for the scrobbler: filter gating and enqueue semantics.
Pure — no Mongo, no network (submission is not exercised here)."""

import os
import unittest
from unittest import mock

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("MONGO_URI", "")

from main.utils import media_index, scrobbler  # noqa: E402


class FilterTests(unittest.TestCase):
    def _audio(self, mid, tags):
        from main.utils.hub_query import HubItem
        item = HubItem(
            message_id=mid, secure_hash=f"hash{mid}", title=f"Song {mid}",
            year=None, description="", tags=list(tags), duration=180,
            file_size=1024, has_thumb=False, media_kind="audio",
            artist="Artist", album_title="Album",
        )
        media_index._items[mid] = item
        return item

    def setUp(self):
        self._saved = dict(media_index._items)
        self._audio(1, ["Christmas"])
        self._audio(2, ["Rock"])

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._saved)

    def test_no_filter_allows_everything(self):
        meta = scrobbler._track_meta(1)
        self.assertTrue(scrobbler._filter_allows(None, meta))

    def test_genre_filter_blocks_matching_track(self):
        rules = {"all": [{"field": "genre", "op": "eq", "value": "christmas"}]}
        self.assertFalse(scrobbler._filter_allows(rules, scrobbler._track_meta(1)))
        self.assertTrue(scrobbler._filter_allows(rules, scrobbler._track_meta(2)))

    def test_track_meta_requires_artist_and_title(self):
        self.assertIsNone(scrobbler._track_meta(99999))


class EnqueueTests(unittest.TestCase):
    def test_enqueue_never_raises_on_full_queue(self):
        import asyncio
        q = scrobbler._queue_instance()
        # Fill the queue beyond capacity; enqueue must drop, not raise.
        with mock.patch.object(scrobbler, "_start_worker"):
            for i in range(scrobbler._MAX_QUEUE):
                q.put_nowait((1, i, 0.0))
            scrobbler.enqueue(1, 1)  # must not raise

    def test_enqueue_adds_to_queue(self):
        import asyncio
        q = scrobbler._queue_instance()
        while not q.empty():
            q.get_nowait()
        with mock.patch.object(scrobbler, "_start_worker"):
            scrobbler.enqueue(7, 42, played_at=123.0)
            uid, mid, ts = q.get_nowait()
        self.assertEqual((uid, mid, ts), (7, 42, 123.0))


class WorkerBatchTests(unittest.TestCase):
    """The worker must never submit one user's plays under another user's
    token when the queue interleaves users."""

    def setUp(self):
        from main.utils.hub_query import HubItem
        self._saved = dict(media_index._items)
        for mid in (1, 2):
            media_index._items[mid] = HubItem(
                message_id=mid, secure_hash=f"hash{mid}", title=f"Song {mid}",
                year=None, description="", tags=[], duration=180,
                file_size=1024, has_thumb=False, media_kind="audio",
                artist="Artist", album_title="Album",
            )
        # Reset queue and worker state between tests.
        scrobbler._queue = None
        scrobbler._worker_task = None
        self._saved_settings = dict(scrobbler._settings_cache)
        scrobbler._settings_cache.clear()

    def tearDown(self):
        media_index._items.clear()
        media_index._items.update(self._saved)
        scrobbler._settings_cache.clear()
        scrobbler._settings_cache.update(self._saved_settings)
        # Cancel the worker started by enqueue so it doesn't leak into the
        # next test holding a reference to the discarded queue.
        if scrobbler._worker_task is not None:
            scrobbler._worker_task.cancel()
        scrobbler._queue = None
        scrobbler._worker_task = None

    def test_interleaved_users_submitted_under_own_token(self):
        import asyncio
        from unittest import mock

        submitted = []

        async def run():
            async def fake_submit(uid, tok, listens):
                submitted.append((uid, [l["track_metadata"]["track"] for l in listens]))
                return True

            # Both users have valid settings; tokens differ.
            async def fake_settings(uid):
                return {"provider": "listenbrainz",
                        "token": f"token-{uid}", "filter": None}

            scrobbler.enqueue(1, 1, played_at=100.0)
            scrobbler.enqueue(2, 2, played_at=100.0)
            with mock.patch.object(scrobbler, "_submit_batch", fake_submit), \
                 mock.patch.object(scrobbler, "get_settings", fake_settings):
                await asyncio.wait_for(scrobbler._queue_instance().join(), timeout=10)

        asyncio.run(run())
        self.assertEqual(len(submitted), 2)
        by_user = {uid: tracks for uid, tracks in submitted}
        self.assertEqual(by_user[1], ["Song 1"])
        self.assertEqual(by_user[2], ["Song 2"])


class SettingsPayloadTests(unittest.TestCase):
    def test_token_never_in_payload(self):
        payload = scrobbler.settings_payload({
            "provider": "listenbrainz", "token": "super-secret",
            "filter": None,
        })
        self.assertNotIn("super-secret", str(payload))
        self.assertTrue(payload["hasToken"])
        self.assertTrue(payload["enabled"])

    def test_empty_payload(self):
        payload = scrobbler.settings_payload(None)
        self.assertFalse(payload["hasToken"])
        self.assertFalse(payload["enabled"])


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from pyrogram import raw
from pyrogram.errors import AuthBytesInvalid, FloodWait

from main.utils import custom_dl
from main.utils.custom_dl import ByteStreamer, MediaSessionUnavailable


class _FakeSession:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakeClient:
    def __init__(self):
        self.calls = 0
        self.media_session = _FakeSession()
        self.normal_session = _FakeSession()
        self.media_sessions = {4: self.media_session}
        self.sessions = {4: self.normal_session}

    async def get_session(self, dc_id, is_media=False):
        self.calls += 1
        raise AuthBytesInvalid()


class ByteStreamerMediaSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_auth_bytes_invalid_clears_cached_sessions_before_retrying(self):
        client = _FakeClient()
        streamer = ByteStreamer.__new__(ByteStreamer)

        with patch("asyncio.sleep", return_value=None):
            with self.assertRaises(MediaSessionUnavailable):
                await streamer.generate_media_session(
                    client,
                    SimpleNamespace(dc_id=4, media_id=99),
                )

        self.assertEqual(client.calls, 2)
        self.assertEqual(client.media_sessions, {})
        self.assertEqual(client.sessions, {})
        self.assertTrue(client.media_session.stopped)
        self.assertTrue(client.normal_session.stopped)

    async def test_get_file_flood_wait_retries_without_escaping_response_body(self):
        class MediaSession:
            def __init__(self):
                self.calls = 0

            async def send(self, _request):
                self.calls += 1
                if self.calls == 1:
                    raise FloodWait(1)
                return raw.types.upload.File(
                    type=raw.types.storage.FileUnknown(), mtime=0, bytes=b"payload",
                )

        streamer = ByteStreamer.__new__(ByteStreamer)
        streamer.client = object()
        media_session = MediaSession()
        file_id = SimpleNamespace(media_id=99, dc_id=4, file_type=None)
        sleep = AsyncMock()

        with (
            patch.object(streamer, "generate_media_session", return_value=media_session),
            patch.object(streamer, "get_location", return_value=object()),
            patch.object(custom_dl, "work_loads", {0: 0}),
            patch.object(custom_dl.asyncio, "sleep", sleep),
        ):
            chunks = [
                chunk async for chunk in streamer.yield_file(
                    file_id, 0, 0, 0, len(b"payload"), 1, len(b"payload"),
                )
            ]

        self.assertEqual(chunks, [b"payload"])
        self.assertEqual(media_session.calls, 2)
        sleep.assert_awaited_once_with(1.0)


if __name__ == "__main__":
    unittest.main()

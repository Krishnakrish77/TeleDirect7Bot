"""TeleDirect Photos backend self-checks.

Pure helpers — no Mongo, no network, no bot client. Covers channel input
parsing (connect endpoint), serialization shapes, and the pipeline's
EXIF/hash/thumb math on synthetic bytes.
"""
import os
import importlib
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from main.server import photo_routes
from main.server.photo_routes import (
    _parse_channel_input,
    _inline_disposition,
    _status_name,
    _bot_admin_link,
)
from main.utils.custom_dl import MediaSessionUnavailable
from main.utils.photo_store import thumb_key, _iso_utc, _serialize_photo
from main.utils.photo_pipeline import _parse_exif_datetime, _dms_to_deg


class StatusNameTest(unittest.TestCase):
    """Regression: pyrogram ChatMemberStatus is a PLAIN enum —
    ``ChatMemberStatus.ADMINISTRATOR == "administrator"`` is False, so the
    connect/reverify checks must compare through .value. This test fails
    if anyone reverts to raw string comparison against enum members."""

    def test_enum_members_normalize(self):
        from pyrogram import enums
        self.assertEqual(_status_name(enums.ChatMemberStatus.ADMINISTRATOR), "administrator")
        # pyrogram 2.x names the creator value "owner" (Telegram's raw API
        # says "creator") — the production check accepts both.
        self.assertEqual(_status_name(enums.ChatMemberStatus.OWNER), "owner")
        self.assertEqual(_status_name(enums.ChatMemberStatus.MEMBER), "member")

    def test_enum_members_match_admin_check(self):
        from pyrogram import enums
        # The exact predicate _verify_channel_access applies.
        for good in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
            self.assertIn(_status_name(good), ("administrator", "creator", "owner"))
        self.assertNotIn(_status_name(enums.ChatMemberStatus.MEMBER), ("administrator", "creator", "owner"))

    def test_plain_strings_pass_through(self):
        self.assertEqual(_status_name("administrator"), "administrator")
        self.assertEqual(_status_name(""), "")

    def test_chat_type_normalizes(self):
        from pyrogram import enums
        self.assertEqual(_status_name(enums.ChatType.CHANNEL).upper(), "CHANNEL")



class ChannelInputParseTest(unittest.TestCase):
    def test_accepts_usernames(self):
        self.assertEqual(_parse_channel_input("@myvault"), "myvault")
        self.assertEqual(_parse_channel_input("myvault"), "myvault")
        self.assertEqual(_parse_channel_input("https://t.me/myvault"), "myvault")

    def test_accepts_channel_ids(self):
        self.assertEqual(_parse_channel_input("-1001234567890"), -1001234567890)

    def test_rejects_garbage(self):
        self.assertIsNone(_parse_channel_input(""))
        self.assertIsNone(_parse_channel_input("  "))
        self.assertIsNone(_parse_channel_input("12345"))          # positive raw id
        self.assertIsNone(_parse_channel_input("-10012"))          # too short
        self.assertIsNone(_parse_channel_input("@ab"))             # <4 chars
        self.assertIsNone(_parse_channel_input("@has space"))
        self.assertIsNone(_parse_channel_input("not a channel!"))


class ChannelMessageLinkTest(unittest.TestCase):
    """A channel post's "Copy Link" URL is the friendliest way to hand us a
    private channel — the internal id is the channel id without -100."""

    def test_private_message_link_becomes_channel_id(self):
        self.assertEqual(_parse_channel_input("https://t.me/c/1234567890/12"), -1001234567890)
        self.assertEqual(_parse_channel_input("t.me/c/1234567890/12"), -1001234567890)
        self.assertEqual(_parse_channel_input("telegram.me/c/1234567890"), -1001234567890)
        self.assertEqual(
            _parse_channel_input("https://t.me/c/1234567890/12?thread=5"), -1001234567890
        )

    def test_public_message_link_yields_username(self):
        # Resolved by Telegram, then rejected with the privacy message.
        self.assertEqual(_parse_channel_input("https://t.me/mychannel/42"), "mychannel")

    def test_malformed_links_stay_rejected(self):
        self.assertIsNone(_parse_channel_input("https://t.me/c/abc/12"))
        self.assertIsNone(_parse_channel_input("https://t.me/c/12"))


class BotAdminLinkTest(unittest.TestCase):
    def test_deep_link_requests_post_rights(self):
        self.assertEqual(
            _bot_admin_link("tdbot"),
            "https://t.me/tdbot?startchannel=true&admin=post_messages",
        )

    def test_missing_username_has_no_link(self):
        self.assertIsNone(_bot_admin_link(""))


class InlineDispositionTest(unittest.TestCase):
    def test_ascii_name_inline(self):
        self.assertIn('inline; filename="IMG_2041.jpg"', _inline_disposition("IMG_2041.jpg"))

    def test_unicode_name_uses_star_form(self):
        disp = _inline_disposition("фото.jpg")
        self.assertIn("filename*=UTF-8''", disp)
        self.assertIn('filename="____.jpg"', disp)


class ThumbKeyTest(unittest.TestCase):
    def test_key_shape(self):
        self.assertEqual(thumb_key(-100123, 456, "grid"), "-100123:456:grid")
        self.assertEqual(thumb_key(-100123, 456, "preview"), "-100123:456:preview")


class SerializePhotoTest(unittest.TestCase):
    def test_shape(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        doc = {
            "_id": "507f1f77bcf86cd799439011",
            "message_id": 42,
            "kind": "image",
            "file_name": "a.jpg",
            "mime": "image/jpeg",
            "size": 10,
            "width": 100,
            "height": 50,
            "duration": None,
            "taken_at": datetime(2026, 9, 21, 14, 3, 11, tzinfo=timezone.utc),
            "camera": "X",
            "gps": {"lat": 1.0, "lon": 2.0},
            "favorite": True,
            "album_ids": ["a1"],
            "deleted": False,
            "thumb": {"grid": True, "preview": False},
            "uploaded_at": datetime(2026, 9, 22, tzinfo=timezone.utc),
        }
        out = _serialize_photo(doc)
        self.assertEqual(out["messageId"], 42)
        self.assertTrue(out["thumbsReady"])
        self.assertTrue(out["favorite"])
        self.assertEqual(out["albumIds"], ["a1"])
        self.assertIn("2026-09-21", out["takenAt"])
        # Internal fields must not leak.
        self.assertNotIn("file_id", out)
        self.assertNotIn("owner_user_id", out)
        self.assertNotIn("sha256", out)


class ExifDatetimeTest(unittest.TestCase):
    def test_parses_exif_format(self):
        dt = _parse_exif_datetime("2026:09:21 14:03:11")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 9)
        self.assertEqual(dt.hour, 14)

    def test_rejects_junk(self):
        self.assertIsNone(_parse_exif_datetime(""))
        self.assertIsNone(_parse_exif_datetime(None))
        self.assertIsNone(_parse_exif_datetime("not a date"))


class DmsToDegTest(unittest.TestCase):
    def test_north_positive(self):
        self.assertAlmostEqual(_dms_to_deg((40, 26, 46.3), "N"), 40.446, places=2)

    def test_south_negative(self):
        self.assertAlmostEqual(_dms_to_deg((33, 52, 4.0), "S"), -33.868, places=2)

    def test_missing(self):
        self.assertIsNone(_dms_to_deg(None, "N"))
        self.assertIsNone(_dms_to_deg((1, 2), "N"))


class PipelineEndToEndTest(unittest.TestCase):
    """process() on real synthetic bytes — image path with Pillow."""

    def test_png_image_produces_hash_kind_thumbs(self):
        import io
        from PIL import Image
        from main.utils.photo_pipeline import process_sync

        buf = io.BytesIO()
        Image.new("RGB", (2000, 1200), (120, 40, 90)).save(buf, format="PNG")
        result = process_sync(buf.getvalue(), "image/png", "test.png")

        self.assertEqual(result["kind"], "image")
        self.assertEqual(result["width"], 2000)
        self.assertEqual(result["height"], 1200)
        self.assertEqual(len(result["sha256"]), 64)
        self.assertIsNotNone(result.get("thumb_grid"))
        self.assertIsNotNone(result.get("thumb_preview"))
        # Grid thumb must be smaller than the preview thumb.
        self.assertLess(len(result["thumb_grid"]), len(result["thumb_preview"]))

    def test_video_kind_detection(self):
        from main.utils.photo_pipeline import process_sync
        result = process_sync(b"not really a video", "video/mp4", "x.mp4")
        self.assertEqual(result["kind"], "video")
        self.assertEqual(len(result["sha256"]), 64)
        self.assertIsNone(result.get("thumb_grid"))  # probe fails gracefully


class ExifSubIfdTest(unittest.TestCase):
    """Regression: PIL.Image.ExifID does not exist, so the sub-IFD read
    raised AttributeError and silently dropped capture time + GPS. Dates and
    coordinates therefore have to come from the Exif/GPS IFDs (0x8769/0x8825)."""

    @staticmethod
    def _jpeg_with_exif() -> bytes:
        import piexif
        from PIL import Image

        exif = {
            "0th": {piexif.ImageIFD.Make: b"TestMake", piexif.ImageIFD.Model: b"TestModel"},
            "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2019:01:02 03:04:05"},
            "GPS": {
                piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: ((40, 1), (26, 1), (4630, 100)),
                piexif.GPSIFD.GPSLongitudeRef: b"W",
                piexif.GPSIFD.GPSLongitude: ((74, 1), (0, 1), (2130, 100)),
            },
        }
        buf = io.BytesIO()
        Image.new("RGB", (120, 80), (10, 20, 30)).save(
            buf, format="JPEG", exif=piexif.dump(exif)
        )
        return buf.getvalue()

    def test_capture_time_and_gps_are_parsed(self):
        from main.utils.photo_pipeline import process_sync

        result = process_sync(self._jpeg_with_exif(), "image/jpeg", "shot.jpg")

        # DateTimeOriginal lives in the Exif IFD — not IFD0's DateTime.
        self.assertEqual(
            result.get("taken_at"), datetime(2019, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        )
        self.assertEqual(result.get("camera"), "TestMake TestModel")
        self.assertAlmostEqual(result["gps"]["lat"], 40.4462, places=3)
        self.assertAlmostEqual(result["gps"]["lon"], -74.0059, places=3)


class IsoUtcTest(unittest.TestCase):
    def test_naive_and_aware_utc_render_identically(self):
        naive = datetime(2026, 9, 21, 14, 3, 11)
        aware = datetime(2026, 9, 21, 14, 3, 11, tzinfo=timezone.utc)
        self.assertEqual(_iso_utc(naive), "2026-09-21T14:03:11Z")
        self.assertEqual(_iso_utc(aware), "2026-09-21T14:03:11Z")
        self.assertIsNone(_iso_utc(None))


class _FakeStreamer:
    """Minimal ByteStreamer stand-in: records how yield_file was called."""

    def __init__(self):
        self.client = object()
        self.yield_args = None

    async def generate_media_session(self, *_args, **_kwargs):
        return None

    async def yield_file(self, file_id, index, offset, first_cut, last_cut, part_count, chunk):
        self.yield_args = (index, offset, first_cut, last_cut, part_count, chunk)
        yield b"abc"


class PhotoFileStreamTest(unittest.IsolatedAsyncioTestCase):
    """Byte-route plumbing: HEAD, client selection, stream-slot release."""

    async def test_chooser_skips_clients_without_a_media_session(self):
        stream_routes = importlib.import_module("main.server.stream_routes")
        good, bad = _FakeStreamer(), _FakeStreamer()
        bad.generate_media_session = AsyncMock(
            side_effect=MediaSessionUnavailable("no session")
        )
        cooled: list[int] = []
        with patch.object(stream_routes, "_client_indexes", lambda preferred=None: [0, 1]), \
                patch.object(
                    stream_routes,
                    "_streamer_for_index",
                    lambda index: (object(), bad if index == 0 else good),
                ), \
                patch.object(
                    stream_routes,
                    "_mark_client_cooldown",
                    lambda index, reason: cooled.append(index),
                ):
            index, streamer = await photo_routes._choose_streamer(object())
        self.assertEqual((index, streamer), (1, good))
        self.assertEqual(cooled, [0])

    async def test_chooser_reports_503_when_every_client_fails(self):
        stream_routes = importlib.import_module("main.server.stream_routes")
        dead = _FakeStreamer()
        dead.generate_media_session = AsyncMock(
            side_effect=MediaSessionUnavailable("no session")
        )
        with patch.object(stream_routes, "_client_indexes", lambda preferred=None: [0]), \
                patch.object(stream_routes, "_streamer_for_index", lambda index: (object(), dead)), \
                patch.object(stream_routes, "_mark_client_cooldown", lambda *args: None):
            with self.assertRaises(web.HTTPServiceUnavailable):
                await photo_routes._choose_streamer(object())

    async def test_head_answers_from_headers_only(self):
        response = await photo_routes._stream_bytes(
            make_mocked_request("HEAD", "/api/photos/file/x", headers={"Range": "bytes=0-499"}),
            object(),
            1000,
            slice(0, 500),
            True,
            mime="image/jpeg",
            file_name="a.jpg",
        )
        self.assertEqual(response.status, 206)
        self.assertIsNone(response.body)
        self.assertEqual(response.headers["Content-Range"], "bytes 0-499/1000")
        self.assertEqual(response.headers["Content-Length"], "500")

    async def test_get_streams_on_the_selected_client_and_releases_slot(self):
        stream_routes = importlib.import_module("main.server.stream_routes")
        streamer = _FakeStreamer()
        with patch.object(photo_routes, "_choose_streamer", AsyncMock(return_value=(2, streamer))), \
                patch.object(stream_routes, "_total_active", 0), \
                patch.object(stream_routes, "_ip_active", {}), \
                patch.object(stream_routes, "_real_ip", lambda request: "203.0.113.7"), \
                patch.object(web.StreamResponse, "prepare", AsyncMock()), \
                patch.object(web.StreamResponse, "write", AsyncMock()), \
                patch.object(web.StreamResponse, "write_eof", AsyncMock()):
            response = await photo_routes._stream_bytes(
                make_mocked_request("GET", "/api/photos/file/x"),
                object(),
                3,
                slice(0, None),
                False,
                mime="image/jpeg",
                file_name="a.jpg",
            )
        self.assertEqual(response.status, 200)
        # The index the selector chose must reach yield_file (it used to be
        # hardcoded to 0, bypassing multi-client load balancing).
        self.assertEqual(streamer.yield_args[0], 2)
        self.assertEqual(stream_routes._total_active, 0)
        self.assertEqual(stream_routes._ip_active, {})

    async def test_slot_released_when_prepare_fails(self):
        stream_routes = importlib.import_module("main.server.stream_routes")
        with patch.object(photo_routes, "_choose_streamer", AsyncMock(return_value=(0, _FakeStreamer()))), \
                patch.object(stream_routes, "_total_active", 0), \
                patch.object(stream_routes, "_ip_active", {}), \
                patch.object(stream_routes, "_real_ip", lambda request: "203.0.113.7"), \
                patch.object(
                    web.StreamResponse,
                    "prepare",
                    AsyncMock(side_effect=ConnectionResetError("client gone")),
                ):
            with self.assertRaises(ConnectionResetError):
                await photo_routes._stream_bytes(
                    make_mocked_request("GET", "/api/photos/file/x"),
                    object(),
                    3,
                    slice(0, None),
                    False,
                    mime="image/jpeg",
                    file_name="a.jpg",
                )
        # A failed prepare must not consume a global stream slot.
        self.assertEqual(stream_routes._total_active, 0)


if __name__ == "__main__":
    unittest.main()

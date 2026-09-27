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
from main.utils.photo_store import (
    thumb_key, iso_utc, scan_payload, _serialize_photo, _serialize_album,
)
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


class SerializeAlbumTest(unittest.TestCase):
    def test_count_and_cover_pass_through(self):
        doc = {
            "_id": "507f1f77bcf86cd799439022",
            "name": "Trips",
            "cover_message_id": 42,
            "created_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "sort": 1,
        }
        out = _serialize_album(doc, count=7, cover_id="507f1f77bcf86cd799439011")
        self.assertEqual(out["photoCount"], 7)
        self.assertEqual(out["coverPhotoId"], "507f1f77bcf86cd799439011")

    def test_defaults_empty_album(self):
        doc = {
            "_id": "507f1f77bcf86cd799439022",
            "name": "Empty",
            "cover_message_id": None,
            "created_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "sort": 0,
        }
        out = _serialize_album(doc)
        self.assertEqual(out["photoCount"], 0)
        self.assertIsNone(out["coverPhotoId"])


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
        self.assertEqual(iso_utc(naive), "2026-09-21T14:03:11Z")
        self.assertEqual(iso_utc(aware), "2026-09-21T14:03:11Z")
        self.assertIsNone(iso_utc(None))


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



class PendingChannelHandshakeTest(unittest.TestCase):
    """The "add the bot, press Continue" handshake: Telegram reports the bot's
    own membership change, so the wizard needs neither an id nor a link."""

    def setUp(self):
        from main.bot.plugins import photos as plugin
        self.plugin = plugin
        plugin._PENDING_LINKS.clear()

    def test_remember_lookup_and_expiry(self):
        self.plugin.remember_pending_link(7, -100123, "Vault", now=1000.0)
        entry = self.plugin.pending_link_for(7, now=1000.0)
        self.assertEqual(entry["channel_id"], -100123)
        self.assertEqual(entry["title"], "Vault")
        # A stale handshake must not silently link an old channel.
        self.assertIsNone(
            self.plugin.pending_link_for(7, now=1000.0 + self.plugin._PENDING_TTL_SECONDS + 1)
        )
        self.assertIsNone(self.plugin.pending_link_for(7, now=9999.0))

    def test_remember_prunes_stale_entries(self):
        # A linking attempt that is never polled must not leave the map
        # growing one entry per user forever.
        self.plugin.remember_pending_link(1, -1001, "Old", now=0.0)
        self.plugin.remember_pending_link(
            2, -1002, "New", now=self.plugin._PENDING_TTL_SECONDS + 10
        )
        self.assertNotIn(1, self.plugin._PENDING_LINKS)
        self.assertIn(2, self.plugin._PENDING_LINKS)

    def test_clear_forgets_the_entry(self):
        self.plugin.remember_pending_link(7, -100123, "Vault")
        self.plugin.clear_pending_link(7)
        self.assertIsNone(self.plugin.pending_link_for(7))


class BotMembershipChangeTest(unittest.TestCase):
    BOT_ID = 5

    @staticmethod
    def _update(*, chat_type="channel", member_id=None, status="administrator", member=True):
        from types import SimpleNamespace
        chat = SimpleNamespace(id=-100123, type=chat_type, title="Vault")
        new_member = (
            SimpleNamespace(user=SimpleNamespace(id=member_id), status=status)
            if member else None
        )
        return SimpleNamespace(
            chat=chat, new_chat_member=new_member, from_user=SimpleNamespace(id=7)
        )

    def test_accepts_the_bot_becoming_a_channel_admin(self):
        from main.bot.plugins.photos import _bot_membership_change
        relevant, reason = _bot_membership_change(
            self._update(member_id=self.BOT_ID), self.BOT_ID
        )
        self.assertTrue(relevant, reason)

    def test_accepts_enum_valued_chat_type_and_status(self):
        from pyrogram import enums
        from main.bot.plugins.photos import _bot_membership_change
        relevant, reason = _bot_membership_change(
            self._update(member_id=self.BOT_ID, chat_type=enums.ChatType.CHANNEL,
                         status=enums.ChatMemberStatus.OWNER),
            self.BOT_ID,
        )
        self.assertTrue(relevant, reason)

    def test_ignores_updates_that_are_not_the_bot_becoming_channel_admin(self):
        from main.bot.plugins.photos import _bot_membership_change
        cases = {
            "another member": self._update(member_id=99),
            "a group": self._update(member_id=self.BOT_ID, chat_type="supergroup"),
            "bot added as plain member": self._update(member_id=self.BOT_ID, status="member"),
            "no member payload": self._update(member=False),
        }
        for label, update in cases.items():
            with self.subTest(label):
                relevant, reason = _bot_membership_change(update, self.BOT_ID)
                self.assertFalse(relevant, label)
                self.assertTrue(reason)


class BotAddedHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_a_pending_link_for_the_actor(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from main.bot.plugins import photos as plugin
        from main.server import photo_routes

        plugin._PENDING_LINKS.clear()
        update = SimpleNamespace(
            chat=SimpleNamespace(id=-100123, type="channel", title="Vault"),
            new_chat_member=SimpleNamespace(
                user=SimpleNamespace(id=5), status="administrator"
            ),
            from_user=SimpleNamespace(id=7),
        )
        verified = ({"chat_id": -100123, "creator_id": 7, "title": "Vault"}, "", True)
        with patch.object(
            photo_routes, "_verify_channel_access", AsyncMock(return_value=verified)
        ):
            await plugin.photo_bot_added_to_channel(
                SimpleNamespace(me=SimpleNamespace(id=5)), update
            )
        entry = plugin.pending_link_for(7)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["channel_id"], -100123)
        plugin._PENDING_LINKS.clear()



class RescanProbeTest(unittest.IsolatedAsyncioTestCase):
    """Bots may not call messages.GetHistory (BOT_METHOD_INVALID — this used to
    crash every scan), so the backfill probes ids through get_messages."""

    CHANNEL = -1004429033256

    def setUp(self):
        from main.bot.plugins import photos as plugin
        self.plugin = plugin
        plugin._CHANNEL_QUEUES.clear()
        self.calls: list[list[int]] = []
        self.cursor_writes: list[int] = []
        self.queued: list[int] = []

    def _media(self, message_id):
        from types import SimpleNamespace
        return SimpleNamespace(id=message_id, empty=False, document=object(), video=None, photo=None)

    def _empty(self, message_id):
        from types import SimpleNamespace
        return SimpleNamespace(id=message_id, empty=True, document=None, video=None, photo=None)

    def _text(self, message_id):
        from types import SimpleNamespace
        return SimpleNamespace(id=message_id, empty=False, document=None, video=None, photo=None)

    async def _rescan(self, batches, *, cursor=0, indexed=(), binding="bound", checks=25, explicit=False):
        from unittest.mock import AsyncMock, MagicMock, patch
        from main.utils import photo_store

        async def fake_get_messages(chat_id, ids):
            self.assertIsInstance(ids, list)
            self.calls.append(list(ids))
            # Beyond the scripted batches the id space is empty, like Telegram.
            return batches[len(self.calls) - 1] if len(self.calls) <= len(batches) else []

        async def fake_set_cursor(channel_id, next_id):
            self.cursor_writes.append(next_id)

        from types import SimpleNamespace
        queue = self.plugin._queue_for(self.CHANNEL)
        original_put = queue.put_nowait

        def spy_put(item):
            self.queued.append(item[1].id)
            original_put(item)

        queue.put_nowait = spy_put
        bot = SimpleNamespace(get_messages=fake_get_messages)
        with patch("main.bot.multi_clients", {0: bot}), \
                patch.object(photo_store, "list_indexed_message_ids", AsyncMock(return_value=set(indexed))), \
                patch.object(photo_store, "get_scan_cursor", AsyncMock(return_value=cursor)), \
                patch.object(photo_store, "set_scan_cursor", AsyncMock(side_effect=fake_set_cursor)), \
                patch.object(
                    photo_store,
                    "get_channel",
                    AsyncMock(
                        return_value=(
                            {"channel_id": self.CHANNEL, "owner_user_id": 7}
                            if binding == "bound"
                            else binding
                        )
                    ),
                ), \
                patch.object(self.plugin, "_RESCAN_BINDING_CHECK_BATCHES", checks), \
                patch.object(self.plugin, "_ensure_worker", MagicMock()):
            enqueued = await self.plugin.rescan_channel(7, self.CHANNEL, explicit=explicit)
        return enqueued

    async def test_probes_ids_in_batches_and_skips_empty_and_indexed(self):
        batch = [self._empty(i) for i in range(1, 6)] + [self._media(6), self._text(7), self._media(8)]
        enqueued = await self._rescan([batch + [self._empty(9)]], indexed={6})

        self.assertEqual(enqueued, 1)
        self.assertEqual(self.queued, [8])          # indexed + text + empty skipped
        self.assertEqual(len(self.calls[0]), self.plugin._RESCAN_BATCH)
        self.assertEqual(self.calls[0][0], 1)
        # The cursor advances past the batch it finished; the fake returns
        # empty space after the scripted batch, so the walk then stops.
        self.assertEqual(self.cursor_writes[0], 1 + self.plugin._RESCAN_BATCH)
        self.assertEqual(len(self.cursor_writes), self.plugin._RESCAN_EMPTY_BATCHES_STOP + 1)

    async def test_stops_after_run_of_empty_batches(self):
        batches = [[self._empty(i) for i in range(1, 101)] for _ in range(10)]
        await self._rescan(batches)
        # Five consecutive empty batches means the id space is exhausted.
        self.assertEqual(len(self.calls), self.plugin._RESCAN_EMPTY_BATCHES_STOP)

    async def test_resumes_from_the_saved_cursor_with_overlap(self):
        await self._rescan([[self._empty(i) for i in range(1, 101)]], cursor=1000)
        self.assertEqual(self.calls[0][0], 1000 - self.plugin._RESCAN_OVERLAP)

    async def test_stops_when_the_vault_is_no_longer_bound(self):
        from unittest.mock import AsyncMock, patch
        from main.utils import photo_store

        # Media in every batch, so the empty-run stop cannot end the scan first.
        batches = [[self._media(i) for i in range(1, 101)] for _ in range(4)]
        enqueued = await self._rescan(batches, binding=None, checks=2)

        # Two batches queued, then the binding check stopped the walk instead of
        # indexing a channel the user had just detached.
        self.assertEqual(len(self.calls), 2)
        self.assertGreater(enqueued, 0)

    async def test_stops_when_the_channel_now_belongs_to_someone_else(self):
        from unittest.mock import AsyncMock, patch
        from main.utils import photo_store

        batches = [[self._media(i) for i in range(1, 101)] for _ in range(4)]
        await self._rescan(
            batches, binding={"channel_id": self.CHANNEL, "owner_user_id": 999}, checks=2
        )
        self.assertEqual(len(self.calls), 2)

    async def test_full_queue_keeps_the_cursor_on_the_unfinished_batch(self):
        import asyncio
        from unittest.mock import patch

        class FullQueue:
            def put_nowait(self, item):
                raise asyncio.QueueFull

        with patch.object(self.plugin, "_queue_for", lambda channel_id: FullQueue()):
            enqueued = await self._rescan([[self._media(5)]])

        self.assertEqual(enqueued, 0)
        self.assertEqual(self.cursor_writes, [])  # this batch is retried next pass

    async def test_explicit_rescan_starts_from_id_1(self):
        # A parked cursor (2601 from connect-time empty scans) must not make
        # the explicit walk blind to uploads at low ids.
        await self._rescan([[self._empty(i) for i in range(1, 101)]], cursor=2601,
                           explicit=True)
        self.assertEqual(self.calls[0][0], 1)

    async def test_explicit_rescan_does_not_regress_the_cursor(self):
        # Walk from 1 stops at 5 empty batches (cursor would be 501) — the
        # persisted cursor must stay at 2601 so automatic passes keep
        # resuming from the real tail.
        batches = [[self._empty(i) for i in range(1, 101)] for _ in range(10)]
        await self._rescan(batches, cursor=2601, explicit=True)
        self.assertEqual(self.cursor_writes, [])

    async def test_explicit_rescan_finds_pics_below_parked_cursor(self):
        # The user's exact failure: uploads at ids 3-4, cursor parked at 2601.
        first = [self._media(i) if i in (3, 4) else self._empty(i) for i in range(1, 101)]
        rest = [[self._empty(i) for i in range(101 + 100 * n, 201 + 100 * n)] for n in range(6)]
        enqueued = await self._rescan([first, *rest], cursor=2601, explicit=True, indexed={4})
        self.assertEqual(self.queued, [3])  # 4 already indexed, 3 rescued
        self.assertEqual(self.cursor_writes, [])



class IngestBytesTest(unittest.IsolatedAsyncioTestCase):
    """The web upload path indexes its own bytes — Telegram never echoes the
    bot's own channel_post, which is why uploads used to vanish."""

    @staticmethod
    def _png() -> bytes:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (10, 20, 30)).save(buf, format="PNG")
        return buf.getvalue()

    async def test_persists_metadata_thumbs_and_flags(self):
        from unittest.mock import AsyncMock, patch
        from main.utils import photo_pipeline, photo_store

        upsert = AsyncMock(return_value=None)
        put_thumb = AsyncMock()
        flags = AsyncMock()
        with patch.object(photo_store, "upsert_photo", upsert), \
                patch.object(photo_store, "put_thumb", put_thumb), \
                patch.object(photo_store, "set_thumb_flags", flags):
            error = await photo_pipeline.ingest_bytes(
                7, -100123, 42,
                file_id="AgAC-file-id", data=self._png(),
                mime="image/png", file_name="shot.png",
            )

        self.assertIsNone(error)
        doc = upsert.await_args.args[0]
        self.assertEqual(doc["owner_user_id"], 7)
        self.assertEqual(doc["channel_id"], -100123)
        self.assertEqual(doc["message_id"], 42)
        self.assertEqual(doc["file_id"], "AgAC-file-id")
        self.assertEqual(doc["size"], len(self._png()))
        self.assertEqual(doc["kind"], "image")
        self.assertEqual(len(doc["sha256"]), 64)
        # put_thumb(owner, key, data) — key is "{channel}:{message}:{size}".
        self.assertEqual({call.args[1].rsplit(":", 1)[-1] for call in put_thumb.await_args_list},
                         {"grid", "preview"})
        self.assertTrue(flags.await_args.kwargs["grid"])

    async def test_returns_the_store_error_without_writing_thumbs(self):
        from unittest.mock import AsyncMock, patch
        from main.utils import photo_pipeline, photo_store

        put_thumb = AsyncMock()
        with patch.object(photo_store, "upsert_photo", AsyncMock(return_value="Could not save photo metadata")), \
                patch.object(photo_store, "put_thumb", put_thumb), \
                patch.object(photo_store, "set_thumb_flags", AsyncMock()):
            error = await photo_pipeline.ingest_bytes(
                7, -100123, 43, file_id="x", data=self._png(),
                mime="image/png", file_name="shot.png",
            )
        self.assertEqual(error, "Could not save photo metadata")
        put_thumb.assert_not_called()



class PhotosStatusSerializationTest(unittest.TestCase):
    """A raw scan record 500'd /api/photos/status: Mongo hands back a datetime."""

    def test_scan_payload_is_json_serializable(self):
        import json
        from datetime import datetime, timezone

        payload = scan_payload({
            "state": "done",
            "enqueued": 3,
            "scanned_to": 400,
            "error": "",
            "at": datetime(2026, 9, 27, 12, 3, 4, tzinfo=timezone.utc),
        })
        self.assertEqual(payload["state"], "done")
        self.assertEqual(payload["at"], "2026-09-27T12:03:04Z")
        json.dumps(payload)  # must not raise

    def test_missing_scan_is_none(self):
        self.assertIsNone(scan_payload(None))
        self.assertIsNone(scan_payload({}))

    async def test_status_route_serializes_the_scan_record(self):
        # The shipped bug: the raw scan subdoc contains a datetime, so the
        # status endpoint raised TypeError and 500'd for connected users.
        import json
        from aiohttp.test_utils import make_mocked_request
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, patch
        from main.server import photo_routes
        from main.utils import photo_store

        scan = {
            "state": "done", "enqueued": 3, "scanned_to": 400, "error": "",
            "at": datetime(2026, 9, 27, 12, 3, 4, tzinfo=timezone.utc),
        }
        request = make_mocked_request("GET", "/api/photos/status")
        with patch.object(photo_routes, "get_user", lambda _request: {"sub": "7"}), \
                patch.object(photo_routes, "_photos_disabled", lambda: None), \
                patch.object(
                    photo_routes,
                    "_channel_doc",
                    AsyncMock(return_value={"channel_id": -100123, "status": "active", "scan": scan}),
                ), \
                patch.object(photo_store, "count_photos", AsyncMock(return_value=5)):
            response = await photo_routes.photos_status(request)

        self.assertEqual(response.status, 200)
        body = json.loads(response.body.decode())
        self.assertEqual(body["scan"]["at"], "2026-09-27T12:03:04Z")
        self.assertEqual(body["scan"]["enqueued"], 3)
        self.assertEqual(body["photoCount"], 5)

    def test_json_encoder_tolerates_mongo_values(self):
        import json
        from datetime import datetime, timezone
        from main.server.photo_routes import _json_default

        encoded = json.dumps(
            {"at": datetime(2026, 9, 27, 12, 3, 4, tzinfo=timezone.utc)},
            default=_json_default,
        )
        self.assertIn("2026-09-27T12:03:04Z", encoded)


class RewindScanCursorTest(unittest.TestCase):
    """Uploads to a fresh channel land at ids BELOW a cursor parked there by
    connect-time empty scans. Without a rewind, a failed upload self-index
    leaves the pic invisible to every future rescan (forward-only)."""

    def test_rewinds_when_cursor_parked_above(self):
        from unittest.mock import AsyncMock, patch
        from main.utils import photo_store

        updates = []

        async def find_one(*_a, **_k):
            return {"scan_cursor": 2601}

        class _Coll:
            async def find_one(self, *a, **k):
                return await find_one(*a, **k)

            def update_one(self, *a, **k):
                updates.append((a, k))

        class _DB:
            def __getitem__(self, key):
                if key not in vars(self):
                    vars(self)[key] = _Coll()
                return vars(self)[key]

        db = _DB()

        with patch.object(photo_store, "_get_db", lambda: db), \
                patch.object(photo_store, "_ensure_indexes", AsyncMock()):
            import asyncio
            asyncio.run(photo_store.rewind_scan_cursor(-100123, 4))

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][0][1]["$set"]["scan_cursor"], 1)  # 4 - 200, floored

    def test_noop_when_cursor_below(self):
        from unittest.mock import AsyncMock, patch
        from main.utils import photo_store

        async def find_one(*_a, **_k):
            return {"scan_cursor": 50}

        class _Coll:
            async def find_one(self, *a, **k):
                return await find_one(*a, **k)

            def update_one(self, *a, **k):
                raise AssertionError("should not update when cursor already covers the id")

        class _DB:
            def __getitem__(self, key):
                if key not in vars(self):
                    vars(self)[key] = _Coll()
                return vars(self)[key]

        db = _DB()

        with patch.object(photo_store, "_get_db", lambda: db), \
                patch.object(photo_store, "_ensure_indexes", AsyncMock()):
            import asyncio
            asyncio.run(photo_store.rewind_scan_cursor(-100123, 3000))

        # No assertion error raised == no update attempted


if __name__ == "__main__":
    unittest.main()

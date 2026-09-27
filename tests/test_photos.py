"""TeleDirect Photos backend self-checks.

Pure helpers — no Mongo, no network, no bot client. Covers channel input
parsing (connect endpoint), serialization shapes, and the pipeline's
EXIF/hash/thumb math on synthetic bytes.
"""
import os
import unittest

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.server.photo_routes import _parse_channel_input, _inline_disposition
from main.utils.photo_store import thumb_key, _serialize_photo
from main.utils.photo_pipeline import _parse_exif_datetime, _dms_to_deg


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


if __name__ == "__main__":
    unittest.main()

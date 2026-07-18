import os
import unittest
from types import SimpleNamespace


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.codec_probe import _apply_probed_duration


class CodecProbeDurationTests(unittest.TestCase):
    def test_audio_probe_can_replace_bad_nonzero_telegram_duration(self):
        item = SimpleNamespace(duration=3)

        changed = _apply_probed_duration(item, {"format": {"duration": "253.82"}}, overwrite=True)

        self.assertTrue(changed)
        self.assertEqual(item.duration, 253)

    def test_video_probe_keeps_an_existing_telegram_duration(self):
        item = SimpleNamespace(duration=180)

        changed = _apply_probed_duration(item, {"format": {"duration": "253.82"}})

        self.assertFalse(changed)
        self.assertEqual(item.duration, 180)

    def test_invalid_probe_duration_does_not_overwrite_metadata(self):
        item = SimpleNamespace(duration=3)

        changed = _apply_probed_duration(item, {"format": {"duration": "N/A"}}, overwrite=True)

        self.assertFalse(changed)
        self.assertEqual(item.duration, 3)


if __name__ == "__main__":
    unittest.main()

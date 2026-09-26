import os
import unittest
from types import SimpleNamespace


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.codec_probe import (
    _apply_probed_duration,
    is_browser_audio_ok,
    source_needs_hls_for_audio,
)


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


class BrowserAudioCodecTests(unittest.TestCase):
    def test_chromium_decodable_codecs_are_ok(self):
        for codec in ("aac", "mp3", "mp2", "opus", "vorbis", "flac", "pcm_s16le", "aac_latm"):
            self.assertTrue(is_browser_audio_ok(codec), codec)

    def test_licensed_decoder_codecs_fail(self):
        for codec in ("eac3", "ac3", "dts", "dca", "truehd", "eac3_joc"):
            self.assertFalse(is_browser_audio_ok(codec), codec)

    def test_unknown_or_blank_codec_does_not_gate(self):
        self.assertTrue(is_browser_audio_ok(""))
        self.assertTrue(is_browser_audio_ok(None))


class SourceNeedsHlsForAudioTests(unittest.TestCase):
    def _item(self, **kw):
        defaults = dict(probed_at=123.0, source_audio_codec="eac3")
        defaults.update(kw)
        return SimpleNamespace(**defaults)

    def test_eac3_source_prefers_hls(self):
        self.assertTrue(source_needs_hls_for_audio(self._item()))

    def test_aac_source_stays_direct(self):
        self.assertFalse(source_needs_hls_for_audio(self._item(source_audio_codec="aac")))

    def test_unprobed_item_does_not_gate(self):
        self.assertFalse(source_needs_hls_for_audio(self._item(probed_at=0)))
        self.assertFalse(source_needs_hls_for_audio(None))

    def test_missing_audio_codec_does_not_gate(self):
        self.assertFalse(source_needs_hls_for_audio(self._item(source_audio_codec="")))


class ProbeItemSourceAudioCodecTests(unittest.IsolatedAsyncioTestCase):
    """probe_item must record the first audio stream's codec on video
    files — the watch payload starts EAC3/AC3 sources in HLS because
    Chromium/Edge can't decode them directly."""

    _FFPROBE_JSON = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "pix_fmt": "yuv420p", "height": 1080},
            {"codec_type": "audio", "codec_name": "eac3"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "6000"},
    }

    def _mk_item(self):
        return SimpleNamespace(
            message_id=1, secure_hash="h", file_size=0,
            media_kind="video", duration=0, file_name="", quality="",
            embedded_subtitle_count=0, subtitles_probed_at=0.0,
            video_codec="", pix_fmt="", probed_at=0.0,
            source_audio_codec="", subtitles=[],
            album_title="", artist="", album_artist="",
        )

    async def test_video_probe_records_first_audio_codec(self):
        import json as _json
        from unittest.mock import AsyncMock, patch

        from main.utils import codec_probe

        item = self._mk_item()

        data = _json.dumps(self._FFPROBE_JSON).encode()

        class FakeProc:
            returncode = 0
            stderr = b""

            async def communicate(self):
                return data, b""

        async def fake_exec(*_a, **_k):
            return FakeProc()

        with (
            patch.object(codec_probe.asyncio, "create_subprocess_exec", fake_exec),
            patch.object(codec_probe.media_index, "persist_soon", AsyncMock()),
            patch.object(codec_probe.media_index, "_store_upsert", AsyncMock()),
        ):
            ok = await codec_probe.probe_item(item)

        self.assertTrue(ok)
        self.assertEqual(item.source_audio_codec, "eac3")
        self.assertEqual(item.video_codec, "h264")


if __name__ == "__main__":
    unittest.main()

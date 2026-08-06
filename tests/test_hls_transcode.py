import os
import unittest
from unittest.mock import patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.hls import AudioTrack, ProbeResult, selected_audio_codec
from main.utils import hls_session
from main.utils.hls_session import HlsSession


class HlsTranscodeTest(unittest.TestCase):
    def test_h264_eight_bit_is_remuxed(self):
        probe = ProbeResult(60, "h264", "aac", pix_fmt="yuv420p")

        self.assertTrue(probe.hls_compatible)
        self.assertFalse(probe.needs_video_transcode)

    def test_non_browser_video_codecs_are_hls_transcoded(self):
        for codec in ("hevc", "av1", "vp9", "vc1", "mpeg2video"):
            with self.subTest(codec=codec):
                probe = ProbeResult(60, codec, "dts", pix_fmt="yuv420p10le")
                self.assertTrue(probe.hls_compatible)
                self.assertTrue(probe.needs_video_transcode)

    def test_selected_audio_uses_the_requested_track_codec(self):
        probe = ProbeResult(
            60,
            "h264",
            "aac",
            audio_tracks=(
                AudioTrack(0, "aac", "en", "English"),
                AudioTrack(1, "ac3", "ta", "Tamil"),
            ),
        )

        self.assertEqual(selected_audio_codec(probe, 0), "aac")
        self.assertEqual(selected_audio_codec(probe, 1), "ac3")

    def test_old_process_cannot_release_a_replacement_transcode_slot(self):
        session = HlsSession(123, "http://127.0.0.1/input", 60, "aac", transcode_video=True)
        old_slot = object()
        new_slot = object()
        session._transcode_slot_token = new_slot
        try:
            with patch.object(hls_session, "_transcode_sem") as semaphore:
                session._release_transcode_slot(old_slot)
                semaphore.return_value.release.assert_not_called()

                session._release_transcode_slot(new_slot)
                semaphore.return_value.release.assert_called_once()
        finally:
            session.cleanup_disk()

    def test_transcode_session_outputs_portable_avc_aac(self):
        session = HlsSession(
            123, "http://127.0.0.1/input", 60, "dts", transcode_video=True,
        )
        try:
            args = session._ffmpeg_args(0)

            self.assertIn("libx264", args)
            self.assertIn("yuv420p", args)
            self.assertIn("aac", args)
            self.assertNotIn("-c:v copy", " ".join(args))
        finally:
            session.cleanup_disk()

    def test_session_keeps_continuous_timestamps_when_restarted_from_seek(self):
        session = HlsSession(
            123, "http://127.0.0.1/input", 60, "aac", transcode_video=False,
        )
        try:
            args = session._ffmpeg_args(3)

            self.assertNotIn("-reset_timestamps", args)
            self.assertIn("-output_ts_offset", args)
            self.assertEqual(args[args.index("-output_ts_offset") + 1], "18.000")
            self.assertEqual(args[args.index("-avoid_negative_ts") + 1], "disabled")
        finally:
            session.cleanup_disk()


if __name__ == "__main__":
    unittest.main()

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
        self.assertTrue(probe.remux_fmp4)
        self.assertFalse(probe.needs_video_transcode)

    def test_vp9_av1_eight_bit_remux_to_fmp4_without_transcode(self):
        """MKV VP9/AV1 previously forced a full libx264 re-encode because the
        old policy only recognized H.264. The fMP4 copy path muxes them into
        fragmented MP4 directly — no encode cost."""
        for codec in ("vp9", "av1"):
            with self.subTest(codec=codec):
                probe = ProbeResult(60, codec, "aac", pix_fmt="yuv420p")
                self.assertTrue(probe.hls_compatible)
                self.assertTrue(probe.remux_fmp4)
                self.assertFalse(probe.needs_video_transcode)

    def test_ten_bit_copyable_codecs_still_transcode(self):
        """10-bit HEVC/VP9/H.264 has no browser MSE path — copy is not enough."""
        for codec in ("h264", "hevc", "vp9"):
            with self.subTest(codec=codec):
                probe = ProbeResult(60, codec, "aac", pix_fmt="yuv420p10le")
                self.assertFalse(probe.remux_fmp4)
                self.assertTrue(probe.needs_video_transcode)

    def test_hevc_and_legacy_codecs_transcode(self):
        for codec in ("hevc", "vc1", "mpeg2video", "mpeg4"):
            with self.subTest(codec=codec):
                probe = ProbeResult(60, codec, "dts", pix_fmt="yuv420p")
                self.assertTrue(probe.hls_compatible)
                self.assertFalse(probe.remux_fmp4)
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

    def test_fmp4_session_copies_into_fragmented_mp4(self):
        """The remux path must not encode video when the codec is browser-
        safe. Audio follows the existing BROWSER_AUDIO_OK policy (aac/mp2/mp3
        copy; anything else re-encodes to AAC)."""
        session = HlsSession(
            123, "http://127.0.0.1/input", 60, "aac", transcode_video=False,
            fmp4=True,
        )
        try:
            args = session._ffmpeg_args(0)

            self.assertEqual(args[args.index("-c:v") + 1], "copy")
            self.assertEqual(args[args.index("-c:a") + 1], "copy")
            self.assertEqual(args[args.index("-segment_format") + 1], "mp4")
            self.assertIn("frag_keyframe", " ".join(args))
            self.assertIn("empty_moov", " ".join(args))
            self.assertTrue(str(args[-1]).endswith("%05d.m4s"))
            self.assertEqual(session.segment_ext(), ".m4s")
        finally:
            session.cleanup_disk()

    def test_fmp4_session_transcodes_unsupported_audio(self):
        """Video copies but DTS audio still needs the AAC re-encode."""
        session = HlsSession(
            123, "http://127.0.0.1/input", 60, "dts", transcode_video=False,
            fmp4=True,
        )
        try:
            args = session._ffmpeg_args(0)

            self.assertEqual(args[args.index("-c:v") + 1], "copy")
            self.assertEqual(args[args.index("-c:a") + 1], "aac")
        finally:
            session.cleanup_disk()

    def test_ts_session_extension_unchanged(self):
        session = HlsSession(123, "http://127.0.0.1/input", 60, "aac")
        try:
            self.assertEqual(session.segment_ext(), ".ts")
            self.assertTrue(str(session._ffmpeg_args(0)[-1]).endswith("%05d.ts"))
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

    def test_known_audio_track_is_mapped_required(self):
        """With '?', a transient cold-read failure silently dropped the
        audio map and every later segment played muted."""
        session = HlsSession(
            123, "http://127.0.0.1/input", 60, "eac3", audio_index=2,
            transcode_video=True,
        )
        try:
            args = session._ffmpeg_args(0)
            i = args.index("-map", args.index("-map") + 1)  # second -map
            self.assertEqual(args[i + 1], "0:a:2")
        finally:
            session.cleanup_disk()

    def test_video_only_file_keeps_optional_audio_map(self):
        session = HlsSession(
            123, "http://127.0.0.1/input", 60, None, audio_index=0,
            transcode_video=True,
        )
        try:
            args = session._ffmpeg_args(0)
            i = args.index("-map", args.index("-map") + 1)
            self.assertEqual(args[i + 1], "0:a:0?")
        finally:
            session.cleanup_disk()


if __name__ == "__main__":
    unittest.main()

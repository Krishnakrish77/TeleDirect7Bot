import os
import unittest


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.hls import ProbeResult
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


if __name__ == "__main__":
    unittest.main()

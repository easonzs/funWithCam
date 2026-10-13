import subprocess
import unittest
from unittest.mock import patch

from funwithcam.media_check import probe_media


class MediaCheckTest(unittest.TestCase):
    @patch("funwithcam.media_check.subprocess.run")
    def test_reports_video_and_audio_tracks(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"streams": ['
                '{"codec_type": "video", "codec_name": "h264"},'
                '{"codec_type": "audio", "codec_name": "aac"}'
                "]}"
            ),
            stderr="",
        )

        result = probe_media("rtsp://go2rtc:8554/study_room", 15)

        self.assertEqual(("h264",), result.video_codecs)
        self.assertEqual(("aac",), result.audio_codecs)

    @patch("funwithcam.media_check.subprocess.run")
    def test_explains_probe_failure(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="connection refused",
        )

        with self.assertRaisesRegex(RuntimeError, "connection refused"):
            probe_media("rtsp://go2rtc:8554/study_room", 15)


if __name__ == "__main__":
    unittest.main()

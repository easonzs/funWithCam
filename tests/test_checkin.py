import signal
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from funwithcam.checkin import CheckinRecorder


class CheckinRecorderTest(unittest.TestCase):
    def test_reports_ffmpeg_startup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MagicMock()
            process = MagicMock()
            process.wait.return_value = 1
            process.stderr.read.return_value = b"stream unavailable"
            recorder = CheckinRecorder(
                "rtsp://127.0.0.1:8554/study_room",
                Path(directory),
                repository,
            )

            with patch("funwithcam.checkin.shutil.which", return_value="ffmpeg"):
                with patch(
                    "funwithcam.checkin.subprocess.Popen",
                    return_value=process,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "stream unavailable",
                    ):
                        recorder.start(
                            1,
                            "english_reading",
                            datetime.now(timezone.utc),
                        )

        repository.finish.assert_called_once()
        self.assertEqual(
            "failed",
            repository.finish.call_args.args[1],
        )

    def test_stop_uses_sigint_and_saves_completed_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = MagicMock()
            process = MagicMock()
            process.wait.side_effect = [
                subprocess.TimeoutExpired("ffmpeg", 0.3),
                None,
            ]
            process.returncode = 255
            recorder = CheckinRecorder(
                "rtsp://127.0.0.1:8554/study_room",
                Path(directory),
                repository,
            )
            now = datetime.now(timezone.utc)

            with patch("funwithcam.checkin.shutil.which", return_value="ffmpeg"):
                with patch(
                    "funwithcam.checkin.subprocess.Popen",
                    return_value=process,
                ):
                    recorder.start(1, "english_reading", now)
                    duration = recorder.stop(now, save=True)

        self.assertGreaterEqual(duration, 0)
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(
            "saved",
            repository.finish.call_args.args[1],
        )


if __name__ == "__main__":
    unittest.main()

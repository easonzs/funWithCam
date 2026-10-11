import threading
import time
import unittest
from unittest.mock import patch

from funwithcam.video import LatestFrame, LatestFrameCapture, _redact_url


class VideoTest(unittest.TestCase):
    def test_redacts_credentials(self):
        value = _redact_url("rtsp://admin:password@192.168.1.50:554/stream")
        self.assertEqual(
            "rtsp://<redacted>@192.168.1.50:554/stream",
            value,
        )

    def test_keeps_local_url(self):
        value = "rtsp://127.0.0.1:8554/study_room"
        self.assertEqual(value, _redact_url(value))

    def test_hides_frames_during_stream_warmup(self):
        capture = object.__new__(LatestFrameCapture)
        capture._lock = threading.Lock()
        capture._latest = LatestFrame(frame="frame", captured_monotonic_at=100)
        capture._opened_monotonic_at = 100
        capture._warmup_seconds = 1.5

        with patch("funwithcam.video.time.monotonic", return_value=101):
            self.assertIsNone(capture.get_latest(5))
        with patch("funwithcam.video.time.monotonic", return_value=102):
            self.assertEqual("frame", capture.get_latest(5).frame)

    def test_close_waits_for_reader_before_releasing_capture(self):
        read_started = threading.Event()
        allow_read_to_finish = threading.Event()
        release_called = threading.Event()

        class FakeCapture:
            def isOpened(self):
                return True

            def read(self):
                read_started.set()
                allow_read_to_finish.wait(2)
                return False, None

            def release(self):
                release_called.set()

        capture = LatestFrameCapture("rtsp://camera", 0.01, 0)
        capture._cv2 = type(
            "FakeCv2",
            (),
            {"VideoCapture": lambda _self, _url: FakeCapture()},
        )()
        capture.start()
        self.assertTrue(read_started.wait(1))

        close_thread = threading.Thread(target=capture.close)
        close_thread.start()
        time.sleep(0.05)
        self.assertFalse(release_called.is_set())

        allow_read_to_finish.set()
        close_thread.join(1)
        self.assertFalse(close_thread.is_alive())
        self.assertTrue(release_called.is_set())


if __name__ == "__main__":
    unittest.main()


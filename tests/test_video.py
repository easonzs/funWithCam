import unittest

from funwithcam.video import _redact_url


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


if __name__ == "__main__":
    unittest.main()


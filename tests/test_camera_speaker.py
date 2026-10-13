import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from funwithcam.camera_speaker import speak_to_camera


class CameraSpeakerTest(unittest.TestCase):
    def test_generates_shared_audio_and_posts_to_go2rtc(self):
        with tempfile.TemporaryDirectory() as directory:
            response = MagicMock()
            response.status = 200
            response.__enter__.return_value = response
            environment = {
                "FUNWITHCAM_AUDIO_DIR": directory,
                "FUNWITHCAM_GO2RTC_AUDIO_DIR": "/media",
                "FUNWITHCAM_GO2RTC_API": "http://go2rtc:1984",
                "FUNWITHCAM_GO2RTC_STREAM": "study_room",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch(
                    "funwithcam.camera_speaker.shutil.which",
                    return_value="/usr/bin/espeak-ng",
                ),
                patch(
                    "funwithcam.camera_speaker.subprocess.run"
                ) as run,
                patch(
                    "funwithcam.camera_speaker.urlopen",
                    return_value=response,
                ) as open_url,
            ):
                speak_to_camera("开始学习")

        command = run.call_args.args[0]
        generated_path = Path(command[command.index("-w") + 1])
        self.assertEqual(Path(directory).resolve(), generated_path.parent)
        self.assertEqual("开始学习", command[-1])
        request = open_url.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(["study_room"], query["dst"])
        self.assertEqual(
            ["/media/{}".format(generated_path.name)],
            query["file"],
        )
        self.assertEqual("POST", request.method)


if __name__ == "__main__":
    unittest.main()

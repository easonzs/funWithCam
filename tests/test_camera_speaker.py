import json
import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from funwithcam.audio import AudioOutput
from funwithcam.camera_speaker import (
    _remove_old_audio,
    inspect_camera_speaker,
    speak_to_camera,
)


class CameraSpeakerTest(unittest.TestCase):
    def test_retries_transient_missing_camera_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            response = MagicMock()
            response.status = 200
            response.__enter__.return_value = response
            missing_consumer = HTTPError(
                "http://go2rtc:1984/api/streams",
                500,
                "Internal Server Error",
                {},
                BytesIO(b"can't find consumer\n"),
            )
            with (
                patch(
                    "funwithcam.camera_speaker.shutil.which",
                    side_effect=lambda name: (
                        "/usr/bin/espeak-ng"
                        if name == "espeak-ng"
                        else None
                    ),
                ),
                patch("funwithcam.camera_speaker.subprocess.run"),
                patch(
                    "funwithcam.camera_speaker.urlopen",
                    side_effect=[missing_consumer, response],
                ) as open_url,
                patch("funwithcam.camera_speaker.time.sleep") as sleep,
            ):
                speak_to_camera(
                    "开始学习",
                    api_url="http://go2rtc:1984",
                    stream="study_room",
                    local_audio_dir=Path(directory),
                    go2rtc_audio_dir=Path("/media"),
                )

        self.assertEqual(2, open_url.call_count)
        sleep.assert_called_once_with(2)

    def test_removes_old_wav_and_aiff_speech_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_wav = root / "speech-old.wav"
            old_aiff = root / "speech-old.aiff"
            recent_aiff = root / "speech-recent.aiff"
            for path in (old_wav, old_aiff, recent_aiff):
                path.touch()
            old_time = time.time() - 3700
            os.utime(old_wav, (old_time, old_time))
            os.utime(old_aiff, (old_time, old_time))

            _remove_old_audio(root)

            self.assertFalse(old_wav.exists())
            self.assertFalse(old_aiff.exists())
            self.assertTrue(recent_aiff.exists())

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
                "FUNWITHCAM_TTS_ENGINE": "espeak",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch(
                    "funwithcam.camera_speaker.shutil.which",
                    side_effect=lambda name: (
                        "/usr/bin/espeak-ng"
                        if name == "espeak-ng"
                        else None
                    ),
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

        self.assertEqual(2, run.call_count)
        tts_command = run.call_args_list[0].args[0]
        generated_path = Path(tts_command[tts_command.index("-w") + 1])
        self.assertEqual(Path(directory).resolve(), generated_path.parent)
        self.assertEqual("开始学习", tts_command[-1])
        convert_command = run.call_args_list[1].args[0]
        playback_path = Path(convert_command[-1])
        self.assertEqual(Path(directory).resolve(), playback_path.parent)
        self.assertEqual(
            "48000",
            convert_command[convert_command.index("-ar") + 1],
        )
        self.assertEqual("2", convert_command[convert_command.index("-ac") + 1])
        self.assertEqual(
            "pcm_s16le",
            convert_command[convert_command.index("-c:a") + 1],
        )
        request = open_url.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual("/api/streams", urlsplit(request.full_url).path)
        self.assertEqual(["study_room"], query["dst"])
        self.assertEqual(
            [
                "ffmpeg:/media/{}#audio=opus#input=file".format(
                    playback_path.name
                )
            ],
            query["src"],
        )
        self.assertEqual("POST", request.method)

    def test_reports_xiaomi_backchannel(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "study_room": {
                    "producers": [
                        {
                            "medias": [
                                "video, recvonly, H265",
                                "audio, recvonly, OPUS/48000/2",
                                "audio, sendonly, OPUS/48000/2",
                            ]
                        }
                    ]
                }
            }
        ).encode("utf-8")
        with patch(
            "funwithcam.camera_speaker.urlopen",
            return_value=response,
        ):
            status = inspect_camera_speaker(
                "http://127.0.0.1:1984",
                "study_room",
            )

        self.assertTrue(status.available)
        self.assertEqual(
            "audio, sendonly, OPUS/48000/2",
            status.detail,
        )

    def test_audio_output_routes_camera_config_to_go2rtc(self):
        config = SimpleNamespace(
            output_source="camera",
            command=("say", "{text}"),
            camera_api_url="http://127.0.0.1:1984",
            camera_stream="study_room",
            camera_audio_dir=Path("/tmp/local-audio"),
            camera_go2rtc_audio_dir=Path("/media"),
        )
        with patch("funwithcam.audio.speak_to_camera") as speak:
            output = AudioOutput.from_config(config)
            output.speak("开始学习")

        self.assertEqual("camera_speaker", output.source)
        speak.assert_called_once_with(
            "开始学习",
            api_url="http://127.0.0.1:1984",
            stream="study_room",
            local_audio_dir=Path("/tmp/local-audio"),
            go2rtc_audio_dir=Path("/media"),
        )


if __name__ == "__main__":
    unittest.main()

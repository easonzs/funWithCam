import logging
import shutil
import subprocess
import threading
from typing import TYPE_CHECKING, Optional, Sequence

from .camera_speaker import speak_to_camera

if TYPE_CHECKING:
    from .config import AudioConfig


LOGGER = logging.getLogger(__name__)
_SPEAKING = threading.Event()
_SPEAK_LOCK = threading.Lock()


class AudioOutput:
    def __init__(
        self,
        command: Sequence[str],
        *,
        camera_config: Optional["AudioConfig"] = None,
    ):
        self._command = tuple(command)
        self._camera_config = camera_config
        if camera_config is None and shutil.which(self._command[0]) is None:
            raise RuntimeError(
                "audio command not found: {}".format(self._command[0])
            )

    @classmethod
    def from_config(cls, config: "AudioConfig") -> "AudioOutput":
        if config.output_source == "camera":
            return cls((), camera_config=config)
        return cls(config.command)

    @property
    def source(self) -> str:
        if self._camera_config is not None:
            return "camera_speaker"
        return "macbook_speaker"

    def speak(self, text: str) -> None:
        LOGGER.info("playing speech output")
        with _SPEAK_LOCK:
            _SPEAKING.set()
            try:
                if self._camera_config is not None:
                    config = self._camera_config
                    speak_to_camera(
                        text,
                        api_url=config.camera_api_url,
                        stream=config.camera_stream,
                        local_audio_dir=config.camera_audio_dir,
                        go2rtc_audio_dir=config.camera_go2rtc_audio_dir,
                    )
                    return
                command = [
                    text if item == "{text}" else item
                    for item in self._command
                ]
                completed = subprocess.run(command, check=False)
            finally:
                _SPEAKING.clear()
        if completed.returncode != 0:
            raise RuntimeError(
                "audio command exited with code {}".format(completed.returncode)
            )

    @staticmethod
    def is_speaking() -> bool:
        return _SPEAKING.is_set()


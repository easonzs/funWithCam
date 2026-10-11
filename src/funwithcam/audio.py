import logging
import shutil
import subprocess
from typing import Sequence


LOGGER = logging.getLogger(__name__)


class AudioOutput:
    def __init__(self, command: Sequence[str]):
        self._command = tuple(command)
        if shutil.which(self._command[0]) is None:
            raise RuntimeError(
                "audio command not found: {}".format(self._command[0])
            )

    def speak(self, text: str) -> None:
        command = [text if item == "{text}" else item for item in self._command]
        LOGGER.info("playing speech output")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                "audio command exited with code {}".format(completed.returncode)
            )


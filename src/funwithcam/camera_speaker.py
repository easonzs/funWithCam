import argparse
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate local speech and send it to a go2rtc camera stream"
    )
    parser.add_argument("text")
    args = parser.parse_args()
    speak_to_camera(args.text)


def speak_to_camera(text: str) -> None:
    if shutil.which("espeak-ng") is None:
        raise RuntimeError("espeak-ng is required for camera speech output")

    local_dir = Path(
        os.environ.get("FUNWITHCAM_AUDIO_DIR", "/app/audio")
    ).resolve()
    go2rtc_dir = Path(
        os.environ.get("FUNWITHCAM_GO2RTC_AUDIO_DIR", "/media")
    )
    api = os.environ.get(
        "FUNWITHCAM_GO2RTC_API",
        "http://go2rtc:1984",
    ).rstrip("/")
    stream = os.environ.get("FUNWITHCAM_GO2RTC_STREAM", "study_room")
    local_dir.mkdir(parents=True, exist_ok=True)
    _remove_old_audio(local_dir)

    filename = "speech-{}.wav".format(uuid.uuid4().hex)
    local_path = local_dir / filename
    subprocess.run(
        [
            "espeak-ng",
            "-v",
            os.environ.get("FUNWITHCAM_TTS_VOICE", "cmn"),
            "-s",
            os.environ.get("FUNWITHCAM_TTS_SPEED", "145"),
            "-w",
            str(local_path),
            text,
        ],
        check=True,
    )

    query = urlencode(
        {
            "dst": stream,
            "file": str(go2rtc_dir / filename),
        }
    )
    request = Request(
        "{}/api/streams?{}".format(api, query),
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        if response.status >= 400:
            raise RuntimeError(
                "go2rtc camera playback returned HTTP {}".format(
                    response.status
                )
            )


def _remove_old_audio(directory: Path) -> None:
    cutoff = time.time() - 3600
    for path in directory.glob("speech-*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


if __name__ == "__main__":
    main()

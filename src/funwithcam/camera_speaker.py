import argparse
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOGGER = logging.getLogger(__name__)
CAMERA_CONSUMER_RETRY_ATTEMPTS = 4
CAMERA_CONSUMER_RETRY_DELAY_SECONDS = 2


@dataclass(frozen=True)
class CameraSpeakerStatus:
    available: bool
    detail: str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate local speech and send it to a go2rtc camera stream"
    )
    parser.add_argument("text")
    args = parser.parse_args()
    speak_to_camera(args.text)


def speak_to_camera(
    text: str,
    *,
    api_url: Optional[str] = None,
    stream: Optional[str] = None,
    local_audio_dir: Optional[Path] = None,
    go2rtc_audio_dir: Optional[Path] = None,
) -> None:
    api = (
        api_url
        or os.environ.get("FUNWITHCAM_GO2RTC_API")
        or "http://go2rtc:1984"
    ).rstrip("/")
    stream_name = (
        stream
        or os.environ.get("FUNWITHCAM_GO2RTC_STREAM")
        or "study_room"
    )
    local_dir = (
        local_audio_dir
        or Path(os.environ.get("FUNWITHCAM_AUDIO_DIR", "/app/audio"))
    ).resolve()
    go2rtc_dir = (
        go2rtc_audio_dir
        or Path(
            os.environ.get(
                "FUNWITHCAM_GO2RTC_AUDIO_DIR",
                str(local_dir),
            )
        )
    )
    local_dir.mkdir(parents=True, exist_ok=True)
    _remove_old_audio(local_dir)

    tts_command, suffix = _tts_command()
    filename = "speech-{}{}".format(uuid.uuid4().hex, suffix)
    local_path = local_dir / filename
    subprocess.run(tts_command(local_path, text), check=True)

    query = urlencode(
        {
            "dst": stream_name,
            "file": str(go2rtc_dir / filename),
        }
    )
    request = Request(
        "{}/api/ffmpeg?{}".format(api, query),
        method="POST",
    )
    for attempt in range(1, CAMERA_CONSUMER_RETRY_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=45) as response:
                if response.status >= 400:
                    raise RuntimeError(
                        "go2rtc camera playback returned HTTP {}".format(
                            response.status
                        )
                    )
            return
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            retryable = (
                exc.code == 500
                and "can't find consumer" in detail.lower()
                and attempt < CAMERA_CONSUMER_RETRY_ATTEMPTS
            )
            if retryable:
                LOGGER.warning(
                    "go2rtc camera consumer is reconnecting; "
                    "retrying playback (%s/%s)",
                    attempt,
                    CAMERA_CONSUMER_RETRY_ATTEMPTS,
                )
                time.sleep(CAMERA_CONSUMER_RETRY_DELAY_SECONDS)
                continue
            raise RuntimeError(
                "摄像头播放失败，go2rtc 返回 HTTP {}{}。".format(
                    exc.code,
                    "：" + detail if detail else "",
                )
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                "无法连接 go2rtc 播放接口：{}。".format(exc.reason)
            ) from exc


def inspect_camera_speaker(
    api_url: str,
    stream: str,
) -> CameraSpeakerStatus:
    request = Request(
        "{}/api/streams".format(api_url.rstrip("/")),
        method="GET",
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return CameraSpeakerStatus(False, "无法读取 go2rtc：{}".format(exc))

    stream_info = payload.get(stream)
    if stream_info is None:
        return CameraSpeakerStatus(False, "未找到视频流 {}".format(stream))
    medias = [
        media
        for producer in stream_info.get("producers", [])
        for media in producer.get("medias", [])
    ]
    backchannels = [
        media for media in medias if media.startswith("audio, sendonly")
    ]
    if backchannels:
        return CameraSpeakerStatus(True, backchannels[0])
    if not stream_info.get("producers"):
        return CameraSpeakerStatus(
            False,
            "视频流尚未连接，测试播放时会再次尝试",
        )
    return CameraSpeakerStatus(False, "摄像头流没有反向音频通道")


def _tts_command():
    if shutil.which("say") is not None:
        return (
            lambda path, text: ["say", "-o", str(path), text],
            ".aiff",
        )
    if shutil.which("espeak-ng") is not None:
        return (
            lambda path, text: [
                "espeak-ng",
                "-v",
                os.environ.get("FUNWITHCAM_TTS_VOICE", "cmn"),
                "-s",
                os.environ.get("FUNWITHCAM_TTS_SPEED", "145"),
                "-w",
                str(path),
                text,
            ],
            ".wav",
        )
    raise RuntimeError(
        "摄像头播报需要 macOS say 或 espeak-ng 生成语音"
    )


def _remove_old_audio(directory: Path) -> None:
    cutoff = time.time() - 3600
    for pattern in ("speech-*.wav", "speech-*.aiff"):
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                continue


if __name__ == "__main__":
    main()

import json
import subprocess
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class MediaCheckResult:
    video_codecs: Tuple[str, ...]
    audio_codecs: Tuple[str, ...]


def probe_media(stream_url: str, timeout_seconds: float) -> MediaCheckResult:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-analyzeduration",
        "3000000",
        "-probesize",
        "5000000",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-of",
        "json",
        stream_url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "等待摄像头媒体流超时（{} 秒）".format(timeout_seconds)
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "ffprobe 无错误详情"
        raise RuntimeError("无法读取摄像头媒体流：{}".format(detail))

    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    video = tuple(
        str(item.get("codec_name", "unknown"))
        for item in streams
        if item.get("codec_type") == "video"
    )
    audio = tuple(
        str(item.get("codec_name", "unknown"))
        for item in streams
        if item.get("codec_type") == "audio"
    )
    return MediaCheckResult(video_codecs=video, audio_codecs=audio)

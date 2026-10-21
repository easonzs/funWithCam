import argparse
import json
import logging
import math
import os
import shutil
import subprocess
import time
import uuid
import wave
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

    stem = "speech-{}".format(uuid.uuid4().hex)
    source_path = local_dir / "{}_source.wav".format(stem)
    playback_path = local_dir / "{}-48k-stereo.wav".format(stem)
    _synthesize(source_path, text)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(playback_path),
        ],
        check=True,
    )

    query = urlencode(
        {
            "dst": stream_name,
            "src": "ffmpeg:{}#audio=opus#input=file".format(
                go2rtc_dir / playback_path.name
            ),
        }
    )
    request = Request(
        "{}/api/streams?{}".format(api, query),
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


def _synthesize(path: Path, text: str) -> None:
    """Write synthesized speech for `text` to `path` as mono 16-bit WAV.

    Backend is chosen by ``FUNWITHCAM_TTS_ENGINE``:

    * ``edge`` (default): edge-tts Microsoft neural voice
      ``zh-CN-XiaoxiaoNeural`` (晓晓), online; falls back to sherpa on failure
    * ``sherpa``: sherpa-onnx vits-icefall-zh-aishell3 (offline, multi-voice)
    * anything else: ``say`` on macOS or ``espeak-ng`` as subprocess
    """
    engine = os.environ.get("FUNWITHCAM_TTS_ENGINE", "edge").strip().lower()
    if engine == "edge":
        try:
            _edge_tts(path, text)
        except RuntimeError as exc:
            LOGGER.warning(
                "edge-tts 合成失败，回退到本地 sherpa：%s", exc
            )
            _sherpa_tts(path, text)
        return
    if engine == "sherpa":
        _sherpa_tts(path, text)
        return
    _command_tts(path, text)


def _edge_tts(path: Path, text: str) -> None:
    """Synthesize with Microsoft Edge neural TTS (online, default 晓晓).

    Voice is chosen by ``FUNWITHCAM_TTS_VOICE`` (default
    ``zh-CN-XiaoxiaoNeural``). The result is saved as MP3 then converted to
    mono 16-bit WAV, matching the pipeline's expectation.
    """
    import subprocess

    voice = os.environ.get(
        "FUNWITHCAM_TTS_VOICE", "zh-CN-XiaoxiaoNeural"
    )
    mp3_path = str(path).replace(".wav", ".mp3")
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError(
            "FUNWITHCAM_TTS_ENGINE=edge 需要 pip install edge-tts"
        ) from None

    async def _run() -> None:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(mp3_path)

    try:
        import asyncio

        asyncio.run(_run())
    except Exception as exc:
        mp3 = Path(mp3_path)
        if mp3.exists():
            mp3.unlink(missing_ok=True)
        raise RuntimeError(
            "edge-tts 合成失败（需要联网）：{}".format(exc)
        ) from exc
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            mp3_path,
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(path),
        ],
        check=True,
    )


def _command_tts(path: Path, text: str) -> None:
    if shutil.which("say") is not None:
        voice = os.environ.get("FUNWITHCAM_TTS_VOICE")
        rate = os.environ.get("FUNWITHCAM_TTS_RATE")

        def _say(path: str, text: str) -> list[str]:
            command = ["say", "-o", str(path)]
            if voice:
                command.extend(["-v", voice])
            if rate:
                command.extend(["-r", rate])
            command.append(text)
            return command

        subprocess.run(_say(str(path), text), check=True)
        return
    if shutil.which("espeak-ng") is not None:
        subprocess.run(
            [
                "espeak-ng",
                "-v",
                os.environ.get("FUNWITHCAM_TTS_VOICE", "cmn"),
                "-s",
                os.environ.get("FUNWITHCAM_TTS_SPEED", "145"),
                "-w",
                str(path),
                text,
            ],
            check=True,
        )
        return
    raise RuntimeError(
        "摄像头播报需要 macOS say、espeak-ng 或 FUNWITHCAM_TTS_ENGINE=sherpa 生成语音"
    )


_tts_model = None
_TTS_MODEL_DIR = "/app/models/vits-icefall-zh-aishell3"


def _sherpa_tts(path: Path, text: str) -> None:
    """Synthesize with sherpa-onnx vits-icefall-zh-aishell3.

    The model loads lazily once per process and is shared across calls.
    Speaker is chosen by ``FUNWITHCAM_TTS_VOICE`` (a speaker index or the
    SSB#### name from ``speakers.txt``; default 0).
    """
    global _tts_model
    if _tts_model is None:
        try:
            import sherpa_onnx
        except ImportError:
            raise RuntimeError(
                "FUNWITHCAM_TTS_ENGINE=sherpa 需要 pip install sherpa-onnx"
            ) from None
        model = Path(os.environ.get("FUNWITHCAM_AISHELL3_MODEL", _TTS_MODEL_DIR))
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            str(model / "model.onnx"),
            str(model / "lexicon.txt"),
            str(model / "tokens.txt"),
            "",
            "",
        )
        model_config = sherpa_onnx.OfflineTtsModelConfig()
        model_config.vits = vits
        model_config.provider = "cpu"
        model_config.num_threads = 4
        config = sherpa_onnx.OfflineTtsConfig()
        config.model = model_config
        config.rule_fsts = "{}/phone.fst,{}/date.fst,{}/number.fst".format(
            model, model, model
        )
        config.max_num_sentences = 1
        _tts_model = sherpa_onnx.OfflineTts(config)

    sid = _speaker_id(_tts_model)
    audio = _tts_model.generate(text, sid=sid, speed=1.0)
    if len(audio.samples) == 0:
        raise RuntimeError(
            "摄像头播报：sherpa-onnx 未能生成语音（文本可能含有无法合成的字符）"
        )
    samples = _normalize_level(list(audio.samples))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(audio.sample_rate)
        out.writeframes(
            b"".join(
                int(max(-32767.0, min(32767.0, sample * 32767.0))).to_bytes(
                    2, "little", signed=True
                )
                for sample in samples
            )
        )


def _normalize_level(samples: list[float], target_rms: float = 0.14) -> list[float]:
    """Scale sherpa float samples so语音清晰可闻.

    aishell3 模型输出电平很低（峰值约 0.2，均值约 -27dBFS），直接写 int16 会
    静音或极小声。按 RMS 归一化到 target_rms（约 -17dBFS），并限制峰值防止削波。
    """
    count = len(samples)
    if count == 0:
        return samples
    rms = math.sqrt(sum(sample * sample for sample in samples) / count)
    if rms < 1e-9:
        return samples
    gain = target_rms / rms
    peak = max(abs(sample) for sample in samples)
    if peak > 0:
        gain = min(gain, 0.95 / peak)
    if gain == 1.0:
        return samples
    return [sample * gain for sample in samples]


def _speaker_id(tts_model) -> int:
    """Resolve FUNWITHCAM_TTS_VOICE to a speaker id for a loaded model."""
    voice = os.environ.get("FUNWITHCAM_TTS_VOICE", "0").strip()
    if voice.isdigit():
        return min(int(voice), tts_model.num_speakers - 1)
    speakers_file = Path(os.environ.get("FUNWITHCAM_AISHELL3_MODEL", _TTS_MODEL_DIR)) / "speakers.txt"
    if speakers_file.exists():
        for idx, name in enumerate(
            line.strip() for line in speakers_file.read_text().splitlines() if line.strip()
        ):
            if name == voice:
                return min(idx, tts_model.num_speakers - 1)
    return 0


def _remove_old_audio(directory: Path) -> None:
    cutoff = time.time() - 3600
    for pattern in (
        "speech-*.wav",
        "speech-*.aiff",
        "speech-*_source.wav",
    ):
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                continue


if __name__ == "__main__":
    main()

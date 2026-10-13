import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .storage.interaction_repository import CheckinRepository


class CheckinRecorder:
    def __init__(
        self,
        stream_url: str,
        output_dir: Path,
        repository: CheckinRepository,
    ):
        self._stream_url = stream_url
        self._output_dir = output_dir
        self._repository = repository
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._recording_id: Optional[int] = None
        self._started_monotonic: Optional[float] = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._process is not None

    def start(
        self,
        recording_id: int,
        task_key: str,
        now: datetime,
    ) -> Path:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("视频打卡需要安装 ffmpeg")
        day_dir = self._output_dir / now.date().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "{}_{}_{}.mp4".format(
            task_key,
            recording_id,
            now.strftime("%H%M%S"),
        )
        with self._lock:
            if self._process is not None:
                raise ValueError("已有打卡视频正在录制")
            try:
                process = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-rtsp_transport",
                        "tcp",
                        "-i",
                        self._stream_url,
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        "-y",
                        str(path),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                self._repository.finish(
                    recording_id,
                    "failed",
                    now,
                    error_message=str(exc),
                )
                raise RuntimeError("无法启动 ffmpeg：{}".format(exc)) from exc
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                pass
            else:
                error = ""
                if process.stderr is not None:
                    error = process.stderr.read().decode(
                        "utf-8", errors="replace"
                    )[-500:]
                self._repository.finish(
                    recording_id,
                    "failed",
                    now,
                    error_message=error or None,
                )
                raise RuntimeError(
                    error or "ffmpeg 启动后立即退出，请检查摄像头流"
                )
            self._process = process
            self._recording_id = recording_id
            self._started_monotonic = time.monotonic()
        self._repository.mark_recording(recording_id, str(path), now)
        return path

    def stop(self, now: datetime, save: bool = True) -> float:
        with self._lock:
            process = self._process
            recording_id = self._recording_id
            started = self._started_monotonic
            self._process = None
            self._recording_id = None
            self._started_monotonic = None
        if process is None or recording_id is None or started is None:
            raise ValueError("当前没有正在录制的打卡视频")
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        duration = max(0.0, time.monotonic() - started)
        if save and process.returncode in (0, 255):
            self._repository.finish(
                recording_id,
                "saved",
                now,
                duration_seconds=duration,
            )
        else:
            error = ""
            if process.stderr is not None:
                error = process.stderr.read().decode(
                    "utf-8", errors="replace"
                )[-500:]
            self._repository.finish(
                recording_id,
                "cancelled" if not save else "failed",
                now,
                duration_seconds=duration,
                error_message=error or None,
            )
            if save:
                raise RuntimeError(error or "视频录制没有正常结束")
        return duration

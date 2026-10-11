import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatestFrame:
    frame: object
    captured_monotonic_at: float


class LatestFrameCapture:
    def __init__(self, stream_url: str, reconnect_seconds: float):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-contrib-python is required") from exc

        self._cv2 = cv2
        self._stream_url = stream_url
        self._reconnect_seconds = reconnect_seconds
        self._lock = threading.Lock()
        self._latest: Optional[LatestFrame] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._capture = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("video capture already started")
        self._thread = threading.Thread(
            target=self._run,
            name="video-capture",
            daemon=True,
        )
        self._thread.start()

    def get_latest(self, max_age_seconds: float) -> Optional[LatestFrame]:
        with self._lock:
            latest = self._latest
        if latest is None:
            return None
        if time.monotonic() - latest.captured_monotonic_at > max_age_seconds:
            return None
        return latest

    def close(self) -> None:
        self._stop.set()
        self._release_current()
        if self._thread is not None:
            self._thread.join(timeout=self._reconnect_seconds + 1)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._capture is None or not self._capture.isOpened():
                if not self._open():
                    continue

            ok, frame = self._capture.read()
            if not ok:
                LOGGER.warning("video stream read failed; reconnecting")
                self._release_current()
                self._stop.wait(self._reconnect_seconds)
                continue

            with self._lock:
                self._latest = LatestFrame(
                    frame=frame,
                    captured_monotonic_at=time.monotonic(),
                )

    def _open(self) -> bool:
        LOGGER.info(
            "connecting to video stream: %s",
            _redact_url(self._stream_url),
        )
        capture = self._cv2.VideoCapture(self._stream_url)
        if not capture.isOpened():
            capture.release()
            LOGGER.warning("unable to open video stream; retrying")
            self._stop.wait(self._reconnect_seconds)
            return False
        self._capture = capture
        return True

    def _release_current(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            capture.release()


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if parts.username is None:
        return value

    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = "[{}]".format(hostname)
    if parts.port is not None:
        hostname = "{}:{}".format(hostname, parts.port)
    redacted_netloc = "<redacted>@{}".format(hostname)
    return urlunsplit(
        (
            parts.scheme,
            redacted_netloc,
            parts.path,
            parts.query,
            parts.fragment,
        )
    )


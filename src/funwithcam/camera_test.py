import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .config import load_config
from .presence import PresenceMonitor
from .video import LatestFrameCapture
from .vision import PosePresenceDetector


LOGGER = logging.getLogger(__name__)


def run_camera_test(config_path: Path) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    timezone = ZoneInfo(config.timezone)
    capture = LatestFrameCapture(
        config.video.stream_url,
        config.video.reconnect_seconds,
        config.video.warmup_seconds,
    )
    detector = PosePresenceDetector(config.person.min_detection_confidence)
    monitor = PresenceMonitor(
        config.study_area,
        config.person,
        config.presence,
    )
    audio = AudioOutput(config.audio.command)
    interval = 1.0 / config.video.inference_fps
    last_inference_at = 0.0

    LOGGER.info("starting camera to speech test")
    capture.start()
    try:
        while True:
            now_monotonic = time.monotonic()
            if now_monotonic - last_inference_at < interval:
                time.sleep(min(0.02, interval / 2))
                continue
            last_inference_at = now_monotonic

            latest = capture.get_latest(config.video.max_frame_age_seconds)
            if latest is None:
                continue

            observation = detector.detect(
                latest.frame,
                datetime.now(timezone),
                latest.captured_monotonic_at,
            )
            result = monitor.update(observation)
            LOGGER.info(
                "presence eligible=%s confirmed=%s stable=%.1fs reason=%s "
                "confidence=%.2f area=%s center=(%s,%s)",
                result.eligible,
                result.confirmed,
                result.stable_seconds,
                result.reason,
                observation.confidence,
                _format_optional(observation.bbox_area_ratio),
                _format_optional(observation.center_x),
                _format_optional(observation.center_y),
            )
            if result.confirmed:
                audio.speak(config.audio.camera_test_text)
                LOGGER.info("camera to speech test completed")
                return
    except KeyboardInterrupt:
        LOGGER.info("camera test cancelled")
    finally:
        detector.close()
        capture.close()


def _format_optional(value):
    return "-" if value is None else "{:.3f}".format(value)


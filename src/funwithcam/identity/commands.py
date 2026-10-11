import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

from funwithcam.audio import AudioOutput
from funwithcam.config import AppConfig, load_config
from funwithcam.storage.database import Database
from funwithcam.video import LatestFrameCapture

from .debug_frames import FaceDebugFrames
from .matcher import (
    FrameMatch,
    IdentityMatcher,
    MultiFrameVerifier,
    VerificationResult,
    average_embeddings,
)
from .repository import FaceProfileRepository
from .vision import FaceDetector, FaceRecognizer


LOGGER = logging.getLogger(__name__)

ENROLLMENT_PHASES = (
    ("front", "请正对摄像头，保持自然表情。"),
    ("left", "请将脸稍微转向左侧。"),
    ("right", "请将脸稍微转向右侧。"),
)


def run_face_enrollment(
    config_path: Path,
    database_path: Path,
    role: str,
    name: str,
    profile_type: str,
) -> int:
    config = load_config(config_path)
    _configure_logging(config)
    database = Database(database_path)
    database.initialize()
    repository = FaceProfileRepository(database)
    detector, recognizer, capture = _build_runtime(config)
    audio = AudioOutput(config.audio.command)
    timezone = ZoneInfo(config.timezone)
    phase_targets = _phase_targets(config.identity.enrollment_samples)
    embeddings: List[Tuple[float, ...]] = []
    deadline = time.monotonic() + config.identity.command_timeout_seconds
    last_sample_at = 0.0
    debug = _start_debug(
        config,
        "face-enroll_{}_{}".format(profile_type, role),
        datetime.now(timezone),
    )
    debug_result = {"status": "running"}

    capture.start()
    try:
        for (phase, prompt), target in zip(ENROLLMENT_PHASES, phase_targets):
            audio.speak(prompt)
            accepted = 0
            while accepted < target:
                if time.monotonic() >= deadline:
                    raise TimeoutError("人脸录入超时，请调整位置和光线后重试")
                latest = capture.get_latest(config.video.max_frame_age_seconds)
                if latest is None:
                    time.sleep(0.02)
                    continue
                if latest.captured_monotonic_at - last_sample_at < (
                    config.identity.sample_interval_seconds
                ):
                    time.sleep(0.02)
                    continue

                detection = detector.detect_one(latest.frame)
                if detection.face is None:
                    debug.save_rejected(
                        latest.frame,
                        detection,
                        latest.captured_monotonic_at,
                        phase,
                    )
                    LOGGER.info("enrollment frame rejected: %s", detection.reason)
                    time.sleep(0.05)
                    continue

                aligned = recognizer.align(latest.frame, detection.face)
                embeddings.append(recognizer.extract_aligned(aligned))
                debug.save_face(
                    latest.frame,
                    detection,
                    aligned,
                    category="accepted",
                    context=phase,
                )
                accepted += 1
                last_sample_at = latest.captured_monotonic_at
                LOGGER.info(
                    "enrollment sample accepted: %s/%s confidence=%.3f size=%s",
                    len(embeddings),
                    config.identity.enrollment_samples,
                    detection.confidence,
                    detection.face_size,
                )

        profile_id = repository.replace(
            role,
            name,
            profile_type,
            average_embeddings(embeddings),
            datetime.now(timezone),
        )
        audio.speak("人脸录入完成。")
        debug_result = {
            "status": "completed",
            "profile_id": profile_id,
            "role": role,
            "name": name,
            "profile_type": profile_type,
            "accepted_samples": len(embeddings),
        }
        return profile_id
    except KeyboardInterrupt:
        debug_result = {
            "status": "cancelled",
            "accepted_samples": len(embeddings),
        }
        raise
    except Exception as exc:
        debug_result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "accepted_samples": len(embeddings),
        }
        raise
    finally:
        try:
            debug.finish(debug_result)
        finally:
            capture.close()


def run_face_verification(
    config_path: Path,
    database_path: Path,
    profile_type: str,
) -> VerificationResult:
    config = load_config(config_path)
    _configure_logging(config)
    database = Database(database_path)
    database.initialize()
    templates = FaceProfileRepository(database).list_active(profile_type)
    matcher = IdentityMatcher(
        templates,
        config.identity.match_threshold,
        config.identity.ambiguity_margin,
    )
    verifier = MultiFrameVerifier(
        config.identity.confirmation_frames,
        config.identity.confirmation_window_seconds,
    )
    detector, recognizer, capture = _build_runtime(config)
    deadline = time.monotonic() + config.identity.command_timeout_seconds
    interval = 1.0 / config.video.inference_fps
    last_inference_at = 0.0
    latest_result = VerificationResult(
        "unknown",
        None,
        None,
        False,
        "not_started",
        0,
    )
    debug = _start_debug(
        config,
        "face-verify_{}".format(profile_type),
        datetime.now(ZoneInfo(config.timezone)),
    )
    debug_result = {"status": "running"}

    capture.start()
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_inference_at < interval:
                time.sleep(min(0.02, interval / 2))
                continue
            last_inference_at = now
            latest = capture.get_latest(config.video.max_frame_age_seconds)
            if latest is None:
                continue

            detection = detector.detect_one(latest.frame)
            if detection.face is None:
                debug.save_rejected(
                    latest.frame,
                    detection,
                    latest.captured_monotonic_at,
                    profile_type,
                )
                frame_match = FrameMatch(
                    "unknown",
                    None,
                    detection.confidence,
                    detection.reason,
                )
            else:
                aligned = recognizer.align(latest.frame, detection.face)
                embedding = recognizer.extract_aligned(aligned)
                frame_match = matcher.match(embedding)
                debug.save_face(
                    latest.frame,
                    detection,
                    aligned,
                    category=(
                        "matched"
                        if frame_match.identity in ("child", "parent")
                        else "unknown"
                    ),
                    context=profile_type,
                    identity=frame_match.identity,
                    score=frame_match.score,
                    match_reason=frame_match.reason,
                )

            latest_result = verifier.update(
                frame_match,
                latest.captured_monotonic_at,
            )
            LOGGER.info(
                "identity=%s confirmed=%s frames=%s score=%s reason=%s",
                latest_result.identity,
                latest_result.confirmed,
                latest_result.matching_frames,
                _format_score(latest_result.score),
                latest_result.reason,
            )
            if latest_result.confirmed:
                debug_result = _verification_debug_result(latest_result)
                return latest_result
        latest_result = VerificationResult(
            "unknown",
            None,
            latest_result.score,
            False,
            "timeout",
            latest_result.matching_frames,
        )
        debug_result = _verification_debug_result(latest_result)
        return latest_result
    except KeyboardInterrupt:
        debug_result = {
            "status": "cancelled",
            "identity": latest_result.identity,
            "score": latest_result.score,
            "reason": latest_result.reason,
        }
        raise
    except Exception as exc:
        debug_result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        raise
    finally:
        try:
            debug.finish(debug_result)
        finally:
            capture.close()


def _build_runtime(config: AppConfig):
    detector = FaceDetector(
        config.identity.detector_model,
        config.identity.detection_threshold,
        config.identity.nms_threshold,
        config.identity.min_face_size_px,
    )
    recognizer = FaceRecognizer(config.identity.recognizer_model)
    capture = LatestFrameCapture(
        config.video.stream_url,
        config.video.reconnect_seconds,
        config.video.warmup_seconds,
    )
    return detector, recognizer, capture


def _phase_targets(total: int) -> Tuple[int, int, int]:
    if total < 3:
        raise ValueError("identity.enrollment_samples must be at least 3")
    base, remainder = divmod(total, 3)
    return tuple(
        base + (1 if index < remainder else 0)
        for index in range(3)
    )


def _configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _format_score(value):
    return "-" if value is None else "{:.3f}".format(value)


def _start_debug(
    config: AppConfig,
    run_name: str,
    started_at: datetime,
) -> FaceDebugFrames:
    debug = FaceDebugFrames(
        config.identity.save_debug_frames,
        config.identity.debug_dir,
        run_name,
        config.identity.debug_face_limit,
        config.identity.debug_rejected_limit,
        started_at,
    )
    if debug.run_dir is not None:
        LOGGER.info("face debug frames will be saved to: %s", debug.run_dir)
    return debug


def _verification_debug_result(result: VerificationResult) -> dict:
    return {
        "status": "completed",
        "identity": result.identity,
        "name": result.name,
        "confirmed": result.confirmed,
        "score": result.score,
        "reason": result.reason,
        "matching_frames": result.matching_frames,
    }

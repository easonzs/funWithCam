import hashlib
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .calibration_steps import CALIBRATION_STEP_BY_KEY
from .config import AppConfig
from .identity.matcher import IdentityMatcher, MultiFrameVerifier
from .identity.repository import FaceProfileRepository
from .identity.vision import FaceDetector, FaceRecognizer
from .precheck import calculate_brightness
from .runtime_state import (
    set_calibration_active,
    wait_for_assistant_pause,
)
from .storage.calibration_repository import CalibrationRepository
from .storage.database import Database
from .video import LatestFrameCapture


LOGGER = logging.getLogger(__name__)
FEATURE_KEYS = (
    "shoulder_center_x",
    "shoulder_center_y",
    "shoulder_width",
    "shoulder_slope",
    "head_to_shoulder_ratio",
    "torso_offset_ratio",
    "bbox_area_ratio",
    "brightness",
)


@dataclass
class CalibrationSample:
    frame: object
    features: dict
    points: dict
    face: Optional[object]
    pose_visibility: float


@dataclass(frozen=True)
class CalibrationCaptureResult:
    passed: bool
    reason: str
    original_filename: Optional[str] = None
    annotated_filename: Optional[str] = None
    features: Optional[dict] = None
    quality: Optional[dict] = None
    camera_fingerprint: Optional[str] = None

    @property
    def message(self) -> str:
        messages = {
            "video_unavailable": "没有稳定读取到摄像头画面，请检查视频流后重试",
            "multiple_people": "采集时检测到多人，请只让孩子留在书桌区域",
            "identity_changed": (
                "采集过程中识别到家长，请让孩子独自在画面中重试"
            ),
            "child_not_verified": (
                "没有确认到孩子，请先面向摄像头再保持当前动作"
            ),
            "outside_study_area": "请让孩子坐在已配置的书桌区域内",
            "insufficient_samples": "有效姿态画面不足，请保持动作稳定并重试",
            "cancelled": "采集已取消",
            "system_error": "采集发生错误，请查看服务日志后重试",
        }
        return messages.get(self.reason, "采集未完成，请重试")

    def as_repository_payload(self) -> dict:
        if not self.passed:
            raise ValueError("failed calibration result has no saved payload")
        return {
            "original_filename": self.original_filename,
            "annotated_filename": self.annotated_filename,
            "features": self.features,
            "quality": self.quality,
            "camera_fingerprint": self.camera_fingerprint,
        }


def summarize_features(samples: list[dict]) -> dict:
    if not samples:
        raise ValueError("at least one feature sample is required")
    result = {}
    for key in FEATURE_KEYS:
        values = sorted(
            float(sample[key]) for sample in samples if key in sample
        )
        if not values:
            continue
        result[key] = {
            "median": _percentile(values, 0.50),
            "p10": _percentile(values, 0.10),
            "p90": _percentile(values, 0.90),
        }
    return result


def representative_sample_index(
    samples: list[dict],
    summary: Optional[dict] = None,
) -> int:
    if not samples:
        raise ValueError("at least one feature sample is required")
    summary = summary or summarize_features(samples)
    best_index = 0
    best_distance = float("inf")
    for index, sample in enumerate(samples):
        distance = 0.0
        for key, statistics in summary.items():
            if key not in sample:
                distance += 1.0
                continue
            spread = max(statistics["p90"] - statistics["p10"], 0.001)
            distance += abs(float(sample[key]) - statistics["median"]) / spread
        if distance < best_distance:
            best_index = index
            best_distance = distance
    return best_index


class CameraCalibrationRunner:
    def __init__(self, config: AppConfig, database_path: Path):
        self._config = config
        self._database_path = database_path

    def __call__(
        self,
        draft_id: str,
        step: str,
        stop_event: threading.Event,
    ) -> CalibrationCaptureResult:
        if step not in CALIBRATION_STEP_BY_KEY:
            raise ValueError("unknown calibration step")
        config = self._config
        _speak_safely(
            config.audio.command,
            CALIBRATION_STEP_BY_KEY[step]["speech"],
        )
        capture = LatestFrameCapture(
            config.video.stream_url,
            config.video.reconnect_seconds,
            config.video.warmup_seconds,
        )
        faces = FaceDetector(
            config.identity.detector_model,
            config.identity.detection_threshold,
            config.identity.nms_threshold,
            config.identity.min_face_size_px,
        )
        recognizer = FaceRecognizer(config.identity.recognizer_model)
        templates = FaceProfileRepository(
            Database(self._database_path)
        ).list_active("formal")
        matcher = IdentityMatcher(
            templates,
            config.identity.match_threshold,
            config.identity.ambiguity_margin,
        )
        verifier = MultiFrameVerifier(
            config.identity.confirmation_frames,
            config.identity.confirmation_window_seconds,
        )
        pose = _PoseFeatureExtractor(config.person.min_detection_confidence)
        samples: list[CalibrationSample] = []
        attempts = 0
        multiple_people = False
        child_verified = False
        identity_changed = False
        child_scores: list[float] = []
        last_frame_at = None
        started_at = time.monotonic()
        connection_deadline = (
            started_at
            + config.calibration.timeout_seconds
            - config.calibration.duration_seconds
        )
        sample_deadline = None
        interval = 1.0 / config.video.inference_fps
        capture.start()
        try:
            while not stop_event.is_set():
                now = time.monotonic()
                if sample_deadline is None and now >= connection_deadline:
                    break
                if sample_deadline is not None and now >= sample_deadline:
                    break
                latest = capture.get_latest(config.video.max_frame_age_seconds)
                if latest is None or latest.captured_monotonic_at == last_frame_at:
                    stop_event.wait(interval)
                    continue
                last_frame_at = latest.captured_monotonic_at
                if sample_deadline is None:
                    sample_deadline = now + config.calibration.duration_seconds
                attempts += 1
                face_result = faces.detect_one(latest.frame)
                if len(face_result.faces) > 1:
                    multiple_people = True
                    stop_event.wait(interval)
                    continue
                if face_result.face is not None:
                    match = matcher.match(
                        recognizer.extract(latest.frame, face_result.face)
                    )
                    if match.identity == "child" and match.score is not None:
                        child_scores.append(match.score)
                    if child_verified:
                        if match.identity == "parent":
                            identity_changed = True
                            stop_event.wait(interval)
                            continue
                    else:
                        verification = verifier.update(
                            match,
                            latest.captured_monotonic_at,
                        )
                        child_verified = (
                            verification.confirmed
                            and verification.identity == "child"
                        )
                if not child_verified:
                    stop_event.wait(interval)
                    continue

                pose_result = pose.extract(latest.frame)
                if pose_result is None:
                    stop_event.wait(interval)
                    continue
                features, points, visibility = pose_result
                if not _inside_study_area(features, config):
                    stop_event.wait(interval)
                    continue
                features["brightness"] = calculate_brightness(
                    latest.frame,
                    config.study_area.rect,
                )
                samples.append(
                    CalibrationSample(
                        frame=latest.frame.copy(),
                        features=features,
                        points=points,
                        face=face_result.face,
                        pose_visibility=visibility,
                    )
                )
                stop_event.wait(interval)
        finally:
            pose.close()
            capture.close()

        if stop_event.is_set():
            return CalibrationCaptureResult(False, "cancelled")
        if sample_deadline is None:
            return CalibrationCaptureResult(False, "video_unavailable")
        if multiple_people:
            return CalibrationCaptureResult(False, "multiple_people")
        if identity_changed:
            return CalibrationCaptureResult(False, "identity_changed")
        if not child_verified:
            return CalibrationCaptureResult(False, "child_not_verified")
        if len(samples) < config.calibration.min_valid_samples:
            reason = "outside_study_area" if not samples else "insufficient_samples"
            return CalibrationCaptureResult(False, reason)

        feature_summary = summarize_features(
            [sample.features for sample in samples]
        )
        representative = samples[
            representative_sample_index(
                [sample.features for sample in samples],
                feature_summary,
            )
        ]
        draft_dir = config.calibration.image_dir / draft_id
        draft_dir.mkdir(parents=True, exist_ok=True)
        suffix = "{}_{}".format(
            datetime.now(ZoneInfo(config.timezone)).strftime("%Y%m%d_%H%M%S"),
            uuid.uuid4().hex[:8],
        )
        original_filename = "{}_{}_original.jpg".format(step, suffix)
        annotated_filename = "{}_{}_annotated.jpg".format(step, suffix)
        original_path = draft_dir / original_filename
        annotated_path = draft_dir / annotated_filename
        _save_images(
            representative,
            config.study_area.rect,
            original_path,
            annotated_path,
        )
        quality = {
            "attempted_frames": attempts,
            "valid_samples": len(samples),
            "valid_ratio": len(samples) / max(attempts, 1),
            "median_pose_visibility": _percentile(
                sorted(sample.pose_visibility for sample in samples),
                0.50,
            ),
            "median_child_match_score": (
                None
                if not child_scores
                else _percentile(sorted(child_scores), 0.50)
            ),
        }
        _speak_safely(
            config.audio.command,
            "这一段采集完成，请家长确认。",
        )
        return CalibrationCaptureResult(
            True,
            "passed",
            original_filename,
            annotated_filename,
            feature_summary,
            quality,
            hashlib.sha256(
                config.video.stream_url.encode("utf-8")
            ).hexdigest()[:16],
        )


class CalibrationCoordinator:
    def __init__(
        self,
        repository: CalibrationRepository,
        timezone: ZoneInfo,
        runner: Callable[
            [str, str, threading.Event],
            CalibrationCaptureResult,
        ],
    ):
        self._repository = repository
        self._timezone = timezone
        self._runner = runner
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def request(self, draft_id: str, step: str, now: datetime) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("另一个基准步骤正在采集")
            self._repository.mark_capturing(draft_id, step, now)
            set_calibration_active(True)
            if not wait_for_assistant_pause(3.0):
                set_calibration_active(False)
                self._repository.fail_capture(
                    draft_id,
                    step,
                    "日常视觉检测尚未暂停，请稍后重试",
                    now,
                )
                raise ValueError("日常视觉检测尚未暂停，请稍后重试")
            self._thread = threading.Thread(
                target=self._run,
                args=(draft_id, step),
                name="calibration-capture",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def _run(self, draft_id: str, step: str) -> None:
        try:
            try:
                result = self._runner(draft_id, step, self._stop_event)
            except Exception:
                LOGGER.exception("calibration capture failed unexpectedly")
                result = CalibrationCaptureResult(False, "system_error")
            now = datetime.now(self._timezone)
            try:
                if result.passed:
                    self._repository.save_capture(
                        draft_id,
                        step,
                        result.as_repository_payload(),
                        now,
                    )
                else:
                    self._repository.fail_capture(
                        draft_id,
                        step,
                        result.message,
                        now,
                    )
            except Exception:
                LOGGER.exception("unable to persist calibration result")
                try:
                    self._repository.fail_capture(
                        draft_id,
                        step,
                        CalibrationCaptureResult(
                            False,
                            "system_error",
                        ).message,
                        now,
                    )
                except Exception:
                    LOGGER.exception("unable to persist calibration failure")
        finally:
            set_calibration_active(False)


class _PoseFeatureExtractor:
    def __init__(self, min_detection_confidence: float):
        try:
            import cv2
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError(
                "mediapipe and opencv-contrib-python are required"
            ) from exc
        self._cv2 = cv2
        self._mp = mp
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            enable_segmentation=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_detection_confidence,
        )

    def extract(self, frame):
        rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        result = self._pose.process(rgb)
        if result.pose_landmarks is None:
            return None
        landmark_type = self._mp.solutions.pose.PoseLandmark
        landmarks = result.pose_landmarks.landmark
        names = {
            "nose": landmark_type.NOSE,
            "left_shoulder": landmark_type.LEFT_SHOULDER,
            "right_shoulder": landmark_type.RIGHT_SHOULDER,
            "left_hip": landmark_type.LEFT_HIP,
            "right_hip": landmark_type.RIGHT_HIP,
        }
        points = {
            name: landmarks[landmark.value] for name, landmark in names.items()
        }
        required = (
            points["nose"],
            points["left_shoulder"],
            points["right_shoulder"],
        )
        if min(point.visibility for point in required) < 0.45:
            return None
        left_shoulder = points["left_shoulder"]
        right_shoulder = points["right_shoulder"]
        shoulder_width = math.dist(
            (left_shoulder.x, left_shoulder.y),
            (right_shoulder.x, right_shoulder.y),
        )
        if shoulder_width < 0.02:
            return None
        shoulder_center = (
            (left_shoulder.x + right_shoulder.x) / 2,
            (left_shoulder.y + right_shoulder.y) / 2,
        )
        nose = points["nose"]
        visible = [point for point in landmarks if point.visibility >= 0.45]
        x_values = [min(1.0, max(0.0, point.x)) for point in visible]
        y_values = [min(1.0, max(0.0, point.y)) for point in visible]
        features = {
            "shoulder_center_x": shoulder_center[0],
            "shoulder_center_y": shoulder_center[1],
            "shoulder_width": shoulder_width,
            "shoulder_slope": (
                right_shoulder.y - left_shoulder.y
            ) / shoulder_width,
            "head_to_shoulder_ratio": math.dist(
                (nose.x, nose.y),
                shoulder_center,
            ) / shoulder_width,
            "bbox_area_ratio": (
                (max(x_values) - min(x_values))
                * (max(y_values) - min(y_values))
            ),
        }
        hips = (points["left_hip"], points["right_hip"])
        if min(point.visibility for point in hips) >= 0.40:
            hip_center_x = (hips[0].x + hips[1].x) / 2
            features["torso_offset_ratio"] = (
                shoulder_center[0] - hip_center_x
            ) / shoulder_width
        point_payload = {
            name: (float(point.x), float(point.y), float(point.visibility))
            for name, point in points.items()
        }
        visibility = sum(point.visibility for point in required) / len(required)
        return features, point_payload, visibility

    def close(self) -> None:
        self._pose.close()


def _inside_study_area(features: dict, config: AppConfig) -> bool:
    x_min, y_min, x_max, y_max = config.study_area.rect
    center_x = features["shoulder_center_x"]
    center_y = features["shoulder_center_y"]
    area = features["bbox_area_ratio"]
    return (
        x_min <= center_x <= x_max
        and y_min <= center_y <= y_max
        and config.person.min_bbox_area_ratio
        <= area
        <= config.person.max_bbox_area_ratio
    )


def _save_images(
    sample: CalibrationSample,
    study_rect,
    original_path: Path,
    annotated_path: Path,
) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-contrib-python is required") from exc
    annotated = sample.frame.copy()
    height, width = annotated.shape[:2]
    x_min, y_min, x_max, y_max = study_rect
    cv2.rectangle(
        annotated,
        (int(x_min * width), int(y_min * height)),
        (int(x_max * width), int(y_max * height)),
        (80, 210, 190),
        2,
    )
    if sample.face is not None:
        face = sample.face
        x, y, face_width, face_height = (
            int(face[0]),
            int(face[1]),
            int(face[2]),
            int(face[3]),
        )
        cv2.rectangle(
            annotated,
            (x, y),
            (x + face_width, y + face_height),
            (80, 190, 90),
            2,
        )
        for index in range(4, 14, 2):
            cv2.circle(
                annotated,
                (int(face[index]), int(face[index + 1])),
                3,
                (80, 190, 90),
                -1,
            )
    pixel_points = {}
    for name, (x, y, visibility) in sample.points.items():
        if visibility < 0.40:
            continue
        pixel_points[name] = (int(x * width), int(y * height))
        cv2.circle(annotated, pixel_points[name], 5, (40, 170, 240), -1)
    _draw_line(
        cv2,
        annotated,
        pixel_points,
        "left_shoulder",
        "right_shoulder",
    )
    _draw_line(
        cv2,
        annotated,
        pixel_points,
        "left_hip",
        "right_hip",
    )
    if "nose" in pixel_points and {
        "left_shoulder",
        "right_shoulder",
    }.issubset(pixel_points):
        left = pixel_points["left_shoulder"]
        right = pixel_points["right_shoulder"]
        shoulder_center = (
            int((left[0] + right[0]) / 2),
            int((left[1] + right[1]) / 2),
        )
        cv2.line(
            annotated,
            pixel_points["nose"],
            shoulder_center,
            (240, 180, 60),
            2,
        )
    if not cv2.imwrite(str(original_path), sample.frame):
        raise RuntimeError("unable to save calibration original image")
    if not cv2.imwrite(str(annotated_path), annotated):
        raise RuntimeError("unable to save calibration annotated image")


def _draw_line(cv2, frame, points, first: str, second: str) -> None:
    if first in points and second in points:
        cv2.line(frame, points[first], points[second], (40, 170, 240), 2)


def _speak_safely(command, text: str) -> None:
    try:
        AudioOutput(command).speak(text)
    except Exception:
        LOGGER.warning("unable to play calibration speech", exc_info=True)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    weight = position - lower
    return float(values[lower] * (1 - weight) + values[upper] * weight)

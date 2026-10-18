import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .audio import AudioOutput
from .calibration import _PoseFeatureExtractor, _inside_study_area
from .config import AppConfig
from .precheck import calculate_brightness
from .storage.activity_repository import ActivityRepository
from .storage.calibration_repository import CalibrationRepository
from .storage.observation_repository import ObservationRepository
from .storage.speech_template_repository import SpeechTemplateRepository
from .storage.study_session_repository import StudySessionRepository


LOGGER = logging.getLogger(__name__)
POSTURE_FEATURES = (
    "shoulder_slope",
    "head_to_shoulder_ratio",
    "torso_offset_ratio",
)
POSTURE_STEPS = ("sitting", "writing", "reading")
MIN_FEATURE_MARGIN = {
    "shoulder_slope": 0.08,
    "head_to_shoulder_ratio": 0.08,
    "torso_offset_ratio": 0.08,
}


@dataclass(frozen=True)
class PostureAssessment:
    decidable: bool
    abnormal: bool
    deviation: Optional[float] = None
    closest_step: Optional[str] = None
    head_too_low: bool = False
    measurements: tuple[dict, ...] = ()


@dataclass(frozen=True)
class LightAssessment:
    decidable: bool
    abnormal: bool
    brightness: float
    threshold: Optional[float] = None


@dataclass(frozen=True)
class EvidenceCandidate:
    captured_at: datetime
    image_bytes: bytes
    measurements: tuple[dict, ...]


def assess_posture(features: dict, calibration: Optional[dict]) -> PostureAssessment:
    steps = (calibration or {}).get("steps", {})
    candidates = []
    for step_key in POSTURE_STEPS:
        baseline = steps.get(step_key, {}).get("features", {})
        feature_results = []
        center_distances = []
        measurements = []
        head_too_low = False
        for key in POSTURE_FEATURES:
            statistics = baseline.get(key)
            if statistics is None or key not in features:
                continue
            value = float(features[key])
            p10 = float(statistics["p10"])
            p90 = float(statistics["p90"])
            spread = max(p90 - p10, MIN_FEATURE_MARGIN[key])
            margin = max(spread * 1.5, MIN_FEATURE_MARGIN[key])
            low = p10 - margin
            high = p90 + margin
            if value < low:
                deviation = (low - value) / margin
                if key == "head_to_shoulder_ratio":
                    head_too_low = True
            elif value > high:
                deviation = (value - high) / margin
            else:
                deviation = 0.0
            feature_results.append(deviation)
            center_distances.append(
                abs(value - float(statistics["median"])) / margin
            )
            measurements.append(
                {
                    "key": key,
                    "actual": value,
                    "minimum": low,
                    "maximum": high,
                }
            )
        if feature_results:
            candidates.append(
                (
                    max(feature_results),
                    sum(center_distances) / len(center_distances),
                    step_key,
                    head_too_low,
                    tuple(measurements),
                )
            )
    if not candidates:
        return PostureAssessment(False, False)
    (
        deviation,
        _center_distance,
        closest_step,
        head_too_low,
        measurements,
    ) = min(candidates, key=lambda item: (item[0], item[1]))
    return PostureAssessment(
        True,
        deviation > 0,
        deviation,
        closest_step,
        head_too_low,
        measurements,
    )


def assess_light(brightness: float, calibration: Optional[dict]) -> LightAssessment:
    statistics = (
        (calibration or {})
        .get("steps", {})
        .get("lighting", {})
        .get("features", {})
        .get("brightness")
    )
    if statistics is None:
        return LightAssessment(False, False, brightness)
    threshold = max(15.0, float(statistics["p10"]) * 0.8)
    return LightAssessment(
        True,
        brightness < threshold,
        brightness,
        threshold,
    )


class StudyMonitor:
    def __init__(
        self,
        config: AppConfig,
        activities: ActivityRepository,
        calibrations: CalibrationRepository,
        observations: ObservationRepository,
        sessions: StudySessionRepository,
        speech_templates: SpeechTemplateRepository,
        audio: AudioOutput,
        evidence_dir: Optional[Path] = None,
        pose_factory: Callable[[float], object] = _PoseFeatureExtractor,
    ):
        self._config = config
        self._activities = activities
        self._calibrations = calibrations
        self._observations = observations
        self._sessions = sessions
        self._speech_templates = speech_templates
        self._audio = audio
        self._evidence_dir = (
            None if evidence_dir is None else evidence_dir.resolve()
        )
        self._pose_factory = pose_factory
        self._pose = None
        self._session_id: Optional[int] = None
        self._calibration_version_id: Optional[int] = None
        self._calibration: Optional[dict] = None
        self._last_sample_at: Optional[float] = None
        self._absence_started_at: Optional[datetime] = None
        self._absence_samples = 0
        self._seat_leave_confirmed = False
        self._reminder_played_this_sample = False
        self._reported_skip_reasons: set[str] = set()
        self._evidence_candidates: dict[str, list[EvidenceCandidate]] = {}

    def is_due(self, now_monotonic: float, interval_seconds: int) -> bool:
        return (
            self._last_sample_at is None
            or now_monotonic - self._last_sample_at >= interval_seconds
        )

    def observe(
        self,
        frame,
        now: datetime,
        now_monotonic: float,
        session: dict,
        settings: dict,
        posture_reminders_enabled: bool,
    ) -> None:
        self._activate(session)
        interval_seconds = int(settings["observation_interval_seconds"])
        if not self.is_due(now_monotonic, interval_seconds):
            return
        self._last_sample_at = now_monotonic
        self._reminder_played_this_sample = False
        pose_result = self._pose.extract(frame)
        if pose_result is None:
            self._observe_absence(
                frame,
                now,
                session,
                settings,
                "pose_not_detected",
            )
            return
        features, points, visibility = pose_result
        if not _inside_study_area(features, self._config):
            self._observe_absence(
                frame,
                now,
                session,
                settings,
                "outside_study_area",
                points,
            )
            return

        self._observe_return(now)
        if self._calibration is None:
            self._record_skip_once(
                now,
                "calibration_missing",
                "学习中监测未运行",
                "本次学习没有可用的孩子动作基准，已跳过姿势和光线判断。",
                session,
            )
            return

        posture = assess_posture(features, self._calibration)
        if posture.decidable and posture.abnormal:
            posture_window_minutes = int(
                settings["posture_trigger_window_minutes"]
            )
            self._capture_evidence(
                "posture_abnormal",
                now,
                frame,
                points,
                posture.measurements,
                posture_window_minutes,
            )
            self._record_anomaly(
                now,
                session,
                settings,
                "posture_abnormal",
                "姿势异常达到提醒条件",
                (
                    "头部位置偏离已确认的日常动作基准。"
                    if posture.head_too_low
                    else "当前姿势偏离已确认的坐姿、写字和阅读基准。"
                ),
                posture.deviation,
                visibility,
                {
                    "closest_baseline": posture.closest_step,
                    "head_too_low": posture.head_too_low,
                    "calibration_version_id": self._calibration_version_id,
                    "measurements": list(posture.measurements),
                },
                int(settings["posture_trigger_count"]),
                posture_window_minutes,
                int(settings["posture_reminder_cooldown_minutes"]),
                "posture_abnormal",
                posture_reminders_enabled,
                (
                    "{孩子称呼}，头稍微抬高一点，保持舒服的坐姿。"
                    if posture.head_too_low
                    else "{孩子称呼}，请调整一下坐姿，保持舒服和放松。"
                ),
            )
        elif not posture.decidable:
            self._record_skip_once(
                now,
                "posture_baseline_missing",
                "姿势判断已跳过",
                "孩子动作基准缺少可比较的姿态特征。",
                session,
            )

        brightness = calculate_brightness(
            frame,
            self._config.study_area.rect,
        )
        light = assess_light(brightness, self._calibration)
        if light.decidable and light.abnormal:
            deviation = (light.threshold - brightness) / max(
                light.threshold,
                1.0,
            )
            light_window_minutes = int(
                settings["light_trigger_window_minutes"]
            )
            light_measurements = (
                {
                    "key": "brightness",
                    "actual": brightness,
                    "minimum": light.threshold,
                },
            )
            self._capture_evidence(
                "light_abnormal",
                now,
                frame,
                points,
                light_measurements,
                light_window_minutes,
            )
            self._record_anomaly(
                now,
                session,
                settings,
                "light_abnormal",
                "光线偏暗达到提醒条件",
                "书桌区域亮度低于已确认的正常光线基准。",
                deviation,
                visibility,
                {
                    "brightness": brightness,
                    "threshold": light.threshold,
                    "calibration_version_id": self._calibration_version_id,
                    "measurements": list(light_measurements),
                },
                int(settings["light_trigger_count"]),
                light_window_minutes,
                int(settings["light_reminder_cooldown_minutes"]),
                "light_abnormal",
                True,
                "{孩子称呼}，书桌有点暗，请把灯光调亮一些。",
            )
        elif not light.decidable:
            self._record_skip_once(
                now,
                "light_baseline_missing",
                "光线判断已跳过",
                "孩子动作基准缺少正常光线数据。",
                session,
            )

    def deactivate(self) -> None:
        if self._pose is not None:
            self._pose.close()
            self._pose = None
        self._session_id = None
        self._calibration_version_id = None
        self._calibration = None
        self._last_sample_at = None
        self._absence_started_at = None
        self._absence_samples = 0
        self._seat_leave_confirmed = False
        self._reminder_played_this_sample = False
        self._reported_skip_reasons.clear()
        self._evidence_candidates.clear()

    def _activate(self, session: dict) -> None:
        session_id = int(session["id"])
        if self._session_id == session_id and self._pose is not None:
            return
        self.deactivate()
        self._session_id = session_id
        self._calibration_version_id = session.get("calibration_version_id")
        version = self._calibrations.get_version(
            self._calibration_version_id
        )
        self._calibration = (
            None if version is None else version["calibration"]
        )
        self._pose = self._pose_factory(
            self._config.person.min_detection_confidence
        )

    def _observe_absence(
        self,
        frame,
        now: datetime,
        session: dict,
        settings: dict,
        reason: str,
        points: Optional[dict] = None,
    ) -> None:
        if self._absence_started_at is None:
            self._absence_started_at = now
        self._absence_samples += 1
        seat_window_minutes = int(
            settings["seat_leave_trigger_window_minutes"]
        )
        self._capture_evidence(
            "seat_left",
            now,
            frame,
            points or {},
            (
                {
                    "key": "pose_detected",
                    "actual": False,
                    "expected": True,
                },
            ),
            seat_window_minutes,
        )
        if self._absence_samples >= 2 and not self._seat_leave_confirmed:
            self._seat_leave_confirmed = True
            self._record_anomaly(
                now,
                session,
                settings,
                "seat_left",
                "检测到孩子离开书桌",
                "连续两次采样未在书桌区域检测到有效姿态。",
                None,
                None,
                {
                    "reason": reason,
                    "absence_started_at": self._absence_started_at.isoformat(),
                },
                int(settings["seat_leave_trigger_count"]),
                seat_window_minutes,
                int(settings["seat_leave_reminder_cooldown_minutes"]),
                "seat_leave",
                True,
                "{孩子称呼}，如果没有需要离开，可以先回来坐好。",
                candidate_started_at=self._absence_started_at,
            )
        auto_pause_after = timedelta(
            minutes=int(settings["away_auto_pause_minutes"])
        )
        if (
            self._seat_leave_confirmed
            and now - self._absence_started_at >= auto_pause_after
        ):
            self._sessions.pause(
                session["session_date"],
                now,
                source="camera",
            )
            self._activities.record(
                now,
                "anomaly",
                "away_auto_paused",
                "camera",
                "success",
                "持续离座后已自动暂停学习",
                "连续离座达到{}分钟。".format(
                    settings["away_auto_pause_minutes"]
                ),
                {"study_session_id": int(session["id"])},
            )

    def _observe_return(self, now: datetime) -> None:
        if self._seat_leave_confirmed:
            self._activities.record(
                now,
                "anomaly",
                "seat_returned",
                "camera",
                "success",
                "孩子已回到书桌区域",
            )
        elif self._absence_samples:
            self._evidence_candidates.pop("seat_left", None)
        self._absence_started_at = None
        self._absence_samples = 0
        self._seat_leave_confirmed = False

    def _capture_evidence(
        self,
        event_type: str,
        now: datetime,
        frame,
        points: dict,
        measurements: tuple[dict, ...],
        window_minutes: int,
    ) -> None:
        if self._evidence_dir is None:
            return
        try:
            image_bytes = _encode_evidence_frame(
                frame,
                points,
                self._config.study_area.rect,
            )
        except Exception:
            LOGGER.warning(
                "unable to prepare study monitor evidence frame",
                exc_info=True,
            )
            return
        since = now - timedelta(minutes=window_minutes)
        candidates = [
            candidate
            for candidate in self._evidence_candidates.get(event_type, [])
            if candidate.captured_at >= since
        ]
        candidates.append(
            EvidenceCandidate(now, image_bytes, measurements)
        )
        self._evidence_candidates[event_type] = candidates[-30:]

    def _save_evidence(
        self,
        event_type: str,
        session_id: int,
        now: datetime,
        window_minutes: int,
    ) -> list[dict]:
        if self._evidence_dir is None:
            return []
        since = now - timedelta(minutes=window_minutes)
        candidates = [
            candidate
            for candidate in self._evidence_candidates.pop(event_type, [])
            if candidate.captured_at >= since
        ]
        if not candidates:
            return []
        event_directory = (
            self._evidence_dir
            / now.date().isoformat()
            / "session-{}".format(session_id)
            / event_type
        )
        try:
            event_directory.mkdir(parents=True, exist_ok=True)
            evidence = []
            for index, candidate in enumerate(candidates, start=1):
                filename = "{}_{}.jpg".format(
                    candidate.captured_at.strftime("%H%M%S_%f"),
                    index,
                )
                image_path = event_directory / filename
                image_path.write_bytes(candidate.image_bytes)
                evidence.append(
                    {
                        "captured_at": candidate.captured_at.isoformat(),
                        "image_path": image_path.relative_to(
                            self._evidence_dir
                        ).as_posix(),
                        "measurements": list(candidate.measurements),
                    }
                )
            return evidence
        except OSError:
            LOGGER.warning(
                "unable to save study monitor evidence",
                exc_info=True,
            )
            return []

    def _record_anomaly(
        self,
        now: datetime,
        session: dict,
        settings: dict,
        event_type: str,
        title: str,
        summary: str,
        deviation: Optional[float],
        confidence: Optional[float],
        payload: dict,
        trigger_count: int,
        window_minutes: int,
        cooldown_minutes: int,
        template_key: str,
        reminder_enabled: bool,
        default_message: str,
        candidate_started_at: Optional[datetime] = None,
    ) -> None:
        session_id = int(session["id"])
        since = now - timedelta(minutes=window_minutes)
        previous_count = self._observations.recent_count(
            session_id,
            event_type,
            since,
        )
        threshold_reached = previous_count + 1 >= trigger_count
        reminded = False
        suppression_reason = "threshold_not_reached"
        message = None
        if threshold_reached:
            if not reminder_enabled:
                suppression_reason = "reminder_disabled"
            else:
                template = self._speech_templates.get(template_key)
                if template is not None and not template["enabled"]:
                    suppression_reason = "template_disabled"
                else:
                    last_reminded = self._observations.last_reminded_at(
                        session_id,
                        event_type,
                    )
                    if (
                        last_reminded is not None
                        and now - last_reminded
                        < timedelta(minutes=cooldown_minutes)
                    ):
                        suppression_reason = "cooldown"
                    elif self._reminder_played_this_sample:
                        suppression_reason = "sample_reminder_limit"
                    elif AudioOutput.is_speaking():
                        suppression_reason = "audio_busy"
                    else:
                        template_text = (
                            default_message
                            if template is None
                            else template["template_text"]
                        )
                        message = template_text.replace(
                            "{孩子称呼}",
                            settings["child_name"],
                        )
                        try:
                            self._audio.speak(message)
                            reminded = True
                            self._reminder_played_this_sample = True
                            suppression_reason = None
                        except Exception:
                            LOGGER.warning(
                                "unable to play study monitor reminder",
                                exc_info=True,
                            )
                            suppression_reason = "audio_error"
        payload = {
            **payload,
            "trigger_count": trigger_count,
            "window_minutes": window_minutes,
            "cooldown_minutes": cooldown_minutes,
            "observations_in_window": previous_count + 1,
            "monitor_interval_seconds": int(
                settings["observation_interval_seconds"]
            ),
        }
        should_record_activity = (
            reminded or previous_count + 1 == trigger_count
        )
        if should_record_activity:
            evidence = self._save_evidence(
                event_type,
                session_id,
                now,
                window_minutes,
            )
            if evidence:
                payload["evidence"] = evidence
        if reminded:
            payload["audio_output_source"] = self._audio.source
        self._observations.record(
            session_id,
            event_type,
            candidate_started_at or now,
            now,
            confidence,
            deviation,
            reminded,
            suppression_reason,
            payload,
        )
        if should_record_activity:
            activity_summary = summary
            if message is not None:
                activity_summary = "{} 已播报：{}".format(summary, message)
            elif suppression_reason:
                activity_summary = "{} 未播报原因：{}。".format(
                    summary,
                    suppression_reason,
                )
            self._activities.record(
                now,
                "anomaly",
                event_type,
                "camera",
                "success" if reminded else "info",
                title,
                activity_summary,
                payload,
            )

    def _record_skip_once(
        self,
        now: datetime,
        reason: str,
        title: str,
        summary: str,
        session: dict,
    ) -> None:
        if reason in self._reported_skip_reasons:
            return
        self._reported_skip_reasons.add(reason)
        self._activities.record(
            now,
            "anomaly",
            reason,
            "camera",
            "ignored",
            title,
            summary,
            {"study_session_id": int(session["id"])},
        )


def _encode_evidence_frame(frame, points: dict, study_rect) -> bytes:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "opencv-contrib-python is required to save evidence"
        ) from exc
    annotated = frame.copy()
    height, width = annotated.shape[:2]
    x_min, y_min, x_max, y_max = study_rect
    cv2.rectangle(
        annotated,
        (int(x_min * width), int(y_min * height)),
        (int(x_max * width), int(y_max * height)),
        (80, 210, 190),
        2,
    )
    pixel_points = {}
    for name, point in points.items():
        x, y, visibility = point
        if visibility < 0.40:
            continue
        pixel_points[name] = (int(x * width), int(y * height))
        cv2.circle(
            annotated,
            pixel_points[name],
            5,
            (40, 170, 240),
            -1,
        )
    for first, second in (
        ("left_shoulder", "right_shoulder"),
        ("left_hip", "right_hip"),
        ("nose", "left_shoulder"),
        ("nose", "right_shoulder"),
    ):
        if first in pixel_points and second in pixel_points:
            cv2.line(
                annotated,
                pixel_points[first],
                pixel_points[second],
                (40, 170, 240),
                2,
            )
    encoded, buffer = cv2.imencode(
        ".jpg",
        annotated,
        [int(cv2.IMWRITE_JPEG_QUALITY), 88],
    )
    if not encoded:
        raise RuntimeError("unable to encode evidence image")
    return buffer.tobytes()

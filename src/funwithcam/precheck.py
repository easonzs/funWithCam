import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .config import AppConfig
from .identity.matcher import IdentityMatcher
from .identity.repository import FaceProfileRepository
from .identity.vision import FaceDetector, FaceRecognizer
from .presence import PresenceMonitor
from .storage.activity_repository import ActivityRepository
from .storage.database import Database
from .storage.study_session_repository import StudySessionRepository
from .video import LatestFrameCapture
from .vision import PosePresenceDetector


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameCheck:
    brightness: float
    person_eligible: bool
    person_reason: str
    face_count: int
    child_verified: bool


@dataclass(frozen=True)
class PrecheckResult:
    passed: bool
    reason: str
    valid_frame_ratio: float
    average_brightness: Optional[float]
    person_ratio: float
    single_face_ratio: float
    child_ratio: float

    @property
    def message(self) -> str:
        messages = {
            "passed": "开始前检查通过",
            "video_unavailable": "未能稳定读取摄像头画面，请检查视频流",
            "too_dark": "书桌区域光线偏暗，请先开灯",
            "outside_study_area": "请坐到书桌区域后再开始",
            "person_not_detected": "未在书桌区域稳定检测到人物",
            "multiple_people": "检测到多人，请只让孩子留在书桌区域",
            "face_not_visible": "没有稳定看到单人画面，请面向摄像头后重试",
            "child_not_verified": "没有确认是孩子，请面向摄像头后重试",
            "cancelled": "开始前检查已取消",
            "system_error": "开始前检查发生错误，请查看服务日志",
        }
        return messages.get(self.reason, "开始前检查未通过")


def calculate_brightness(frame, rect) -> float:
    height, width = frame.shape[:2]
    x_min, y_min, x_max, y_max = rect
    crop = frame[
        int(y_min * height) : max(int(y_max * height), 1),
        int(x_min * width) : max(int(x_max * width), 1),
    ]
    if crop.size == 0:
        return 0.0
    blue = crop[:, :, 0].astype("float32")
    green = crop[:, :, 1].astype("float32")
    red = crop[:, :, 2].astype("float32")
    return float((0.114 * blue + 0.587 * green + 0.299 * red).mean())


def summarize_checks(
    checks: list[FrameCheck],
    attempts: int,
    min_valid_frame_ratio: float,
    min_brightness: float,
) -> PrecheckResult:
    valid_frame_ratio = len(checks) / max(attempts, 1)
    if not checks or valid_frame_ratio < min_valid_frame_ratio:
        return PrecheckResult(
            False,
            "video_unavailable",
            valid_frame_ratio,
            None,
            0.0,
            0.0,
            0.0,
        )

    average_brightness = sum(item.brightness for item in checks) / len(checks)
    person_ratio = sum(item.person_eligible for item in checks) / len(checks)
    single_face_ratio = sum(item.face_count == 1 for item in checks) / len(checks)
    child_ratio = sum(item.child_verified for item in checks) / len(checks)
    if average_brightness < min_brightness:
        reason = "too_dark"
    elif person_ratio < min_valid_frame_ratio:
        reasons = [
            item.person_reason for item in checks if not item.person_eligible
        ]
        reason = (
            "outside_study_area"
            if reasons.count("outside_study_area") >= max(len(reasons) / 2, 1)
            else "person_not_detected"
        )
    elif any(item.face_count > 1 for item in checks):
        reason = "multiple_people"
    elif single_face_ratio < min_valid_frame_ratio:
        reason = "face_not_visible"
    elif child_ratio < min_valid_frame_ratio:
        reason = "child_not_verified"
    else:
        reason = "passed"
    return PrecheckResult(
        reason == "passed",
        reason,
        valid_frame_ratio,
        average_brightness,
        person_ratio,
        single_face_ratio,
        child_ratio,
    )


class CameraPrecheckRunner:
    def __init__(self, config: AppConfig, database_path: Path):
        self._config = config
        self._database_path = database_path

    def __call__(self, stop_event: threading.Event) -> PrecheckResult:
        config = self._config
        capture = LatestFrameCapture(
            config.video.stream_url,
            config.video.reconnect_seconds,
            config.video.warmup_seconds,
        )
        pose = PosePresenceDetector(config.person.min_detection_confidence)
        presence = PresenceMonitor(
            config.study_area,
            config.person,
            config.presence,
        )
        faces = FaceDetector(
            config.identity.detector_model,
            config.identity.detection_threshold,
            config.identity.nms_threshold,
            config.identity.min_face_size_px,
        )
        face_recognizer = FaceRecognizer(config.identity.recognizer_model)
        templates = FaceProfileRepository(
            Database(self._database_path)
        ).list_active("formal")
        matcher = IdentityMatcher(
            templates,
            config.identity.match_threshold,
            config.identity.ambiguity_margin,
        )
        checks: list[FrameCheck] = []
        attempts = 0
        last_frame_at = None
        started_at = time.monotonic()
        connection_deadline = (
            started_at
            + config.precheck.timeout_seconds
            - config.precheck.duration_seconds
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
                    if sample_deadline is not None:
                        attempts += 1
                    stop_event.wait(interval)
                    continue

                if sample_deadline is None:
                    sample_deadline = now + config.precheck.duration_seconds
                attempts += 1
                last_frame_at = latest.captured_monotonic_at
                observation = pose.detect(
                    latest.frame,
                    datetime.now(ZoneInfo(config.timezone)),
                    latest.captured_monotonic_at,
                )
                presence_result = presence.update(observation)
                face_result = faces.detect_one(latest.frame)
                child_verified = False
                if face_result.face is not None:
                    match = matcher.match(
                        face_recognizer.extract(
                            latest.frame,
                            face_result.face,
                        )
                    )
                    child_verified = match.identity == "child"
                checks.append(
                    FrameCheck(
                        brightness=calculate_brightness(
                            latest.frame,
                            config.study_area.rect,
                        ),
                        person_eligible=presence_result.eligible,
                        person_reason=presence_result.reason,
                        face_count=len(face_result.faces),
                        child_verified=child_verified,
                    )
                )
                stop_event.wait(interval)
        finally:
            pose.close()
            capture.close()

        if stop_event.is_set():
            return PrecheckResult(
                False,
                "cancelled",
                0.0,
                None,
                0.0,
                0.0,
                0.0,
            )
        return summarize_checks(
            checks,
            attempts,
            config.precheck.min_valid_frame_ratio,
            config.precheck.min_brightness,
        )


class PrecheckCoordinator:
    def __init__(
        self,
        sessions: StudySessionRepository,
        activities: ActivityRepository,
        timezone: ZoneInfo,
        runner: Callable[[threading.Event], PrecheckResult],
    ):
        self._sessions = sessions
        self._activities = activities
        self._timezone = timezone
        self._runner = runner
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_date: Optional[str] = None
        self._last_result: Optional[PrecheckResult] = None

    def request(
        self,
        action: str,
        session_date: str,
        source: str,
        now: datetime,
        on_complete: Optional[Callable[[PrecheckResult], None]] = None,
    ) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("开始前检查正在进行")
            if action == "start":
                self._sessions.request_start(session_date, now)
            elif action == "resume":
                self._sessions.request_resume(session_date, now)
            else:
                raise ValueError("不支持的开始前检查操作")
            self._activities.record(
                now,
                "study",
                "precheck_started",
                source,
                "info",
                "开始学习前检查" if action == "start" else "继续学习前检查",
                "正在检查摄像头、书桌区域、孩子身份和光线。",
                {"action": action},
            )
            self._last_date = session_date
            self._last_result = None
            self._thread = threading.Thread(
                target=self._run,
                args=(action, session_date, source, on_complete),
                name="study-precheck",
                daemon=True,
            )
            self._thread.start()

    def latest(self, session_date: str) -> Optional[PrecheckResult]:
        with self._lock:
            if self._last_date != session_date:
                return None
            return self._last_result

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def _run(
        self,
        action: str,
        session_date: str,
        source: str,
        on_complete: Optional[Callable[[PrecheckResult], None]],
    ) -> None:
        try:
            result = self._runner(self._stop_event)
        except Exception:
            LOGGER.exception("study precheck failed unexpectedly")
            result = PrecheckResult(
                False,
                "system_error",
                0.0,
                None,
                0.0,
                0.0,
                0.0,
            )

        LOGGER.info(
            "study precheck passed=%s reason=%s frames=%.2f brightness=%s "
            "person=%.2f single_face=%.2f child=%.2f",
            result.passed,
            result.reason,
            result.valid_frame_ratio,
            (
                "-"
                if result.average_brightness is None
                else "{:.1f}".format(result.average_brightness)
            ),
            result.person_ratio,
            result.single_face_ratio,
            result.child_ratio,
        )
        now = datetime.now(self._timezone)
        try:
            if result.passed:
                self._sessions.confirm_precheck(session_date, source, now)
            else:
                self._sessions.cancel_precheck(session_date, now)
        except Exception:
            LOGGER.exception("unable to apply study precheck result")
            result = PrecheckResult(
                False,
                "system_error",
                result.valid_frame_ratio,
                result.average_brightness,
                result.person_ratio,
                result.single_face_ratio,
                result.child_ratio,
            )
        self._activities.record(
            now,
            "study" if result.passed else "anomaly",
            "precheck_passed" if result.passed else "precheck_failed",
            source,
            "success" if result.passed else "failed",
            (
                "学习记录已开始"
                if result.passed and action == "start"
                else "学习记录已继续"
                if result.passed
                else "开始前检查未通过"
            ),
            result.message,
            {
                "action": action,
                "reason": result.reason,
                "valid_frame_ratio": result.valid_frame_ratio,
                "average_brightness": result.average_brightness,
                "person_ratio": result.person_ratio,
                "single_face_ratio": result.single_face_ratio,
                "child_ratio": result.child_ratio,
            },
        )
        with self._lock:
            self._last_result = result
        if on_complete is not None:
            try:
                on_complete(result)
            except Exception:
                LOGGER.exception("study precheck completion callback failed")

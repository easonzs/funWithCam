import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .config import load_config
from .identity.matcher import FrameMatch, IdentityMatcher, MultiFrameVerifier
from .identity.repository import FaceProfileRepository
from .identity.vision import FaceDetector, FaceRecognizer
from .presence import PresenceMonitor
from .reminders import (
    build_briefing_message,
    build_reminder_plan,
    build_second_reminder_message,
)
from .storage.database import Database
from .storage.family_repository import FamilyRepository
from .storage.reminder_repository import ReminderRepository
from .video import LatestFrameCapture
from .vision import PosePresenceDetector


LOGGER = logging.getLogger(__name__)
SECOND_REMINDER_GRACE = timedelta(minutes=30)


def validate_formal_identities(database_path: Path) -> None:
    database = Database(database_path)
    database.initialize()
    templates = FaceProfileRepository(database).list_active("formal")
    if not any(template.role == "child" for template in templates):
        raise RuntimeError(
            "formal child identity is missing; run face-enroll --profile formal"
        )
    if not any(template.role == "parent" for template in templates):
        raise RuntimeError(
            "formal parent identity is missing; enroll at least one parent"
        )


def run_assistant(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event | None = None,
) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    timezone = ZoneInfo(config.timezone)
    database = Database(database_path)
    database.initialize()
    family = FamilyRepository(database)
    reminders = ReminderRepository(database)
    validate_formal_identities(database_path)
    templates = FaceProfileRepository(database).list_active("formal")
    if stop_event is None:
        stop_event = threading.Event()

    matcher = IdentityMatcher(
        templates,
        config.identity.match_threshold,
        config.identity.ambiguity_margin,
    )
    verifier = _new_verifier(config)
    detector = FaceDetector(
        config.identity.detector_model,
        config.identity.detection_threshold,
        config.identity.nms_threshold,
        config.identity.min_face_size_px,
    )
    recognizer = FaceRecognizer(config.identity.recognizer_model)
    pose = PosePresenceDetector(config.person.min_detection_confidence)
    presence = PresenceMonitor(config.study_area, config.person, config.presence)
    capture = LatestFrameCapture(
        config.video.stream_url,
        config.video.reconnect_seconds,
    )
    audio = AudioOutput(config.audio.command)
    interval = 1.0 / config.video.inference_fps
    last_inference_at = 0.0
    last_schedule_check = 0.0
    identity_suppressed_until = 0.0
    active_date = None

    LOGGER.info("starting FunWithCam assistant service")
    capture.start()
    try:
        while not stop_event.is_set():
            now = datetime.now(timezone)
            now_monotonic = time.monotonic()
            if active_date != now.date():
                active_date = now.date()
                presence.reset()
                verifier = _new_verifier(config)
                identity_suppressed_until = 0.0

            if now_monotonic - last_schedule_check >= 1.0:
                last_schedule_check = now_monotonic
                _process_second_reminder(now, family, reminders, audio, timezone)

            status = reminders.get_daily_status(now.date().isoformat())
            if status["briefed_at"] is not None or status["disabled_at"] is not None:
                stop_event.wait(0.1)
                continue

            settings = family.get_settings()
            plan = build_reminder_plan(
                now.date(),
                timezone,
                family.get_daily_preferences(now.date()),
                settings,
            )
            if now < plan.first_briefing_at or now_monotonic < identity_suppressed_until:
                stop_event.wait(0.1)
                continue
            if now_monotonic - last_inference_at < interval:
                stop_event.wait(min(0.02, interval / 2))
                continue
            last_inference_at = now_monotonic

            latest = capture.get_latest(config.video.max_frame_age_seconds)
            if latest is None:
                continue
            observation = pose.detect(
                latest.frame,
                now,
                latest.captured_monotonic_at,
            )
            presence_result = presence.update(observation)
            if not presence_result.confirmed:
                continue

            detection = detector.detect_one(latest.frame)
            if detection.face is None:
                frame_match = FrameMatch(
                    "unknown",
                    None,
                    detection.confidence,
                    detection.reason,
                )
            else:
                frame_match = matcher.match(
                    recognizer.extract(latest.frame, detection.face)
                )
            identity = verifier.update(
                frame_match,
                latest.captured_monotonic_at,
            )
            LOGGER.info(
                "first briefing identity=%s confirmed=%s frames=%s reason=%s",
                identity.identity,
                identity.confirmed,
                identity.matching_frames,
                identity.reason,
            )
            if not identity.confirmed:
                continue
            if identity.identity == "parent":
                LOGGER.info("parent detected; first briefing remains available")
                identity_suppressed_until = now_monotonic + 30
                presence.reset()
                verifier = _new_verifier(config)
                continue
            if identity.identity != "child":
                continue

            items = family.list_agenda(now.date())
            courses = family.list_courses_for_date(now.date())
            message = build_briefing_message(
                items,
                courses,
                plan,
                now,
                settings["child_name"],
            )
            audio.speak(message)
            reminders.mark_briefed(
                now.date().isoformat(),
                now,
                datetime.now(timezone),
                message,
            )
            LOGGER.info("first briefing completed")
    except KeyboardInterrupt:
        LOGGER.info("assistant service stopped")
    finally:
        pose.close()
        capture.close()


def _process_second_reminder(
    now: datetime,
    family: FamilyRepository,
    reminders: ReminderRepository,
    audio: AudioOutput,
    timezone: ZoneInfo,
) -> None:
    status = reminders.get_daily_status(now.date().isoformat())
    if status["briefed_at"] is None or status["second_reminder_at"] is not None:
        return
    settings = family.get_settings()
    plan = build_reminder_plan(
        now.date(),
        timezone,
        family.get_daily_preferences(now.date()),
        settings,
    )
    if plan.second_reminder_at is None or now < plan.second_reminder_at:
        return

    briefed_at = datetime.fromisoformat(status["briefed_at"])
    if (
        plan.second_reminder_at <= briefed_at
        or now > plan.second_reminder_at + SECOND_REMINDER_GRACE
    ):
        reminders.mark_second_reminder(
            now.date().isoformat(),
            plan.second_reminder_at,
            now,
            "expired",
            None,
        )
        return

    message = build_second_reminder_message(
        family.list_agenda(now.date()),
        plan.second_reminder_at,
        settings["child_name"],
    )
    audio.speak(message)
    reminders.mark_second_reminder(
        now.date().isoformat(),
        plan.second_reminder_at,
        datetime.now(timezone),
        "played",
        message,
    )
    LOGGER.info("second reminder completed")


def _new_verifier(config) -> MultiFrameVerifier:
    return MultiFrameVerifier(
        config.identity.confirmation_frames,
        config.identity.confirmation_window_seconds,
    )

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy

from funwithcam.study_monitor import (
    StudyMonitor,
    assess_light,
    assess_posture,
)
from funwithcam.storage.database import Database
from funwithcam.storage.observation_repository import ObservationRepository


def _statistics(median, spread=0.02):
    return {
        "median": median,
        "p10": median - spread,
        "p90": median + spread,
    }


def _calibration():
    def posture(head, slope=0.0, torso=0.0):
        return {
            "features": {
                "head_to_shoulder_ratio": _statistics(head),
                "shoulder_slope": _statistics(slope),
                "torso_offset_ratio": _statistics(torso),
            }
        }

    return {
        "steps": {
            "sitting": posture(0.72),
            "writing": posture(0.42),
            "reading": posture(0.60),
            "lighting": {
                "features": {
                    "brightness": _statistics(100.0, 5.0),
                }
            },
        }
    }


class FakePose:
    def __init__(self, results):
        self._results = iter(results)
        self.closed = False

    def extract(self, _frame):
        return next(self._results)

    def close(self):
        self.closed = True


class FakeActivities:
    def __init__(self):
        self.events = []

    def record(self, *args, **kwargs):
        self.events.append((args, kwargs))


class FakeCalibrations:
    def __init__(self, calibration):
        self.calibration = calibration

    def get_version(self, version_id):
        if version_id is None or self.calibration is None:
            return None
        return {"id": version_id, "calibration": self.calibration}


class FakeObservations:
    def __init__(self):
        self.events = []

    def recent_count(self, session_id, event_type, since):
        return sum(
            event["study_session_id"] == session_id
            and event["event_type"] == event_type
            and event["confirmed_at"] >= since
            for event in self.events
        )

    def last_reminded_at(self, session_id, event_type):
        matching = [
            event["confirmed_at"]
            for event in self.events
            if event["study_session_id"] == session_id
            and event["event_type"] == event_type
            and event["reminded"]
        ]
        return None if not matching else max(matching)

    def record(
        self,
        study_session_id,
        event_type,
        candidate_started_at,
        confirmed_at,
        confidence,
        deviation,
        reminded,
        suppression_reason,
        payload,
    ):
        self.events.append(
            {
                "study_session_id": study_session_id,
                "event_type": event_type,
                "candidate_started_at": candidate_started_at,
                "confirmed_at": confirmed_at,
                "confidence": confidence,
                "deviation": deviation,
                "reminded": reminded,
                "suppression_reason": suppression_reason,
                "payload": payload,
            }
        )


class FakeSessions:
    def __init__(self):
        self.pauses = []

    def pause(self, session_date, now, source="system"):
        self.pauses.append((session_date, now, source))


class FakeTemplates:
    def get(self, event_key):
        return None


class FakeAudio:
    def __init__(self):
        self.messages = []

    def speak(self, message):
        self.messages.append(message)


class StudyMonitorAssessmentTest(unittest.TestCase):
    def test_normal_sitting_and_writing_are_both_accepted(self):
        calibration = _calibration()

        sitting = assess_posture(
            {
                "head_to_shoulder_ratio": 0.72,
                "shoulder_slope": 0.0,
                "torso_offset_ratio": 0.0,
            },
            calibration,
        )
        writing = assess_posture(
            {
                "head_to_shoulder_ratio": 0.40,
                "shoulder_slope": 0.01,
                "torso_offset_ratio": 0.01,
            },
            calibration,
        )

        self.assertFalse(sitting.abnormal)
        self.assertFalse(writing.abnormal)
        self.assertEqual("writing", writing.closest_step)

    def test_only_clear_deviation_is_abnormal(self):
        result = assess_posture(
            {
                "head_to_shoulder_ratio": 0.10,
                "shoulder_slope": 0.0,
                "torso_offset_ratio": 0.0,
            },
            _calibration(),
        )

        self.assertTrue(result.decidable)
        self.assertTrue(result.abnormal)
        self.assertTrue(result.head_too_low)

    def test_light_uses_confirmed_normal_light_baseline(self):
        normal = assess_light(90.0, _calibration())
        dark = assess_light(50.0, _calibration())
        missing = assess_light(50.0, None)

        self.assertFalse(normal.abnormal)
        self.assertTrue(dark.abnormal)
        self.assertFalse(missing.decidable)


class StudyMonitorRuntimeTest(unittest.TestCase):
    def _monitor(self, pose_results, calibration=None):
        config = SimpleNamespace(
            person=SimpleNamespace(
                min_detection_confidence=0.6,
                min_bbox_area_ratio=0.03,
                max_bbox_area_ratio=0.65,
            ),
            study_area=SimpleNamespace(rect=(0.2, 0.15, 0.8, 0.95)),
        )
        activities = FakeActivities()
        observations = FakeObservations()
        sessions = FakeSessions()
        audio = FakeAudio()
        pose = FakePose(pose_results)
        monitor = StudyMonitor(
            config,
            activities,
            FakeCalibrations(calibration or _calibration()),
            observations,
            sessions,
            FakeTemplates(),
            audio,
            pose_factory=lambda _confidence: pose,
        )
        return monitor, pose, activities, observations, sessions, audio

    @staticmethod
    def _settings():
        return {
            "child_name": "小明",
            "observation_interval_seconds": "10",
            "posture_trigger_count": "2",
            "posture_trigger_window_minutes": "5",
            "posture_reminder_cooldown_minutes": "10",
            "light_trigger_count": "2",
            "light_trigger_window_minutes": "5",
            "light_reminder_cooldown_minutes": "10",
            "seat_leave_trigger_count": "1",
            "seat_leave_trigger_window_minutes": "10",
            "seat_leave_reminder_cooldown_minutes": "10",
            "away_auto_pause_minutes": "1",
        }

    @staticmethod
    def _session():
        return {
            "id": 7,
            "session_date": "2026-10-13",
            "calibration_version_id": 3,
        }

    @staticmethod
    def _pose_result(head_ratio=0.10):
        return (
            {
                "shoulder_center_x": 0.5,
                "shoulder_center_y": 0.5,
                "shoulder_width": 0.2,
                "bbox_area_ratio": 0.2,
                "head_to_shoulder_ratio": head_ratio,
                "shoulder_slope": 0.0,
                "torso_offset_ratio": 0.0,
            },
            {},
            0.9,
        )

    def test_interval_count_and_cooldown_control_posture_reminder(self):
        monitor, _pose, _activities, observations, _sessions, audio = (
            self._monitor(
                [
                    self._pose_result(),
                    self._pose_result(),
                    self._pose_result(),
                ]
            )
        )
        frame = numpy.full((20, 20, 3), 100, dtype=numpy.uint8)
        now = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)

        monitor.observe(
            frame,
            now,
            0.0,
            self._session(),
            self._settings(),
            True,
        )
        self.assertFalse(monitor.is_due(5.0, 10))
        monitor.observe(
            frame,
            now + timedelta(seconds=10),
            10.0,
            self._session(),
            self._settings(),
            True,
        )
        monitor.observe(
            frame,
            now + timedelta(seconds=20),
            20.0,
            self._session(),
            self._settings(),
            True,
        )

        posture_events = [
            event
            for event in observations.events
            if event["event_type"] == "posture_abnormal"
        ]
        self.assertEqual(3, len(posture_events))
        self.assertEqual(1, len(audio.messages))
        self.assertTrue(posture_events[1]["reminded"])
        self.assertEqual("cooldown", posture_events[2]["suppression_reason"])

    def test_two_missing_pose_samples_confirm_leave_and_auto_pause(self):
        monitor, pose, _activities, observations, sessions, audio = (
            self._monitor([None, None, None])
        )
        frame = numpy.full((20, 20, 3), 100, dtype=numpy.uint8)
        now = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)

        for seconds in (0, 10, 70):
            monitor.observe(
                frame,
                now + timedelta(seconds=seconds),
                float(seconds),
                self._session(),
                self._settings(),
                True,
            )

        seat_events = [
            event
            for event in observations.events
            if event["event_type"] == "seat_left"
        ]
        self.assertEqual(1, len(seat_events))
        self.assertEqual(1, len(audio.messages))
        self.assertEqual(
            [("2026-10-13", now + timedelta(seconds=70), "camera")],
            sessions.pauses,
        )
        monitor.deactivate()
        self.assertTrue(pose.closed)
        self.assertTrue(monitor.is_due(71.0, 10))

    def test_missing_calibration_is_recorded_without_posture_judgement(self):
        monitor, _pose, activities, observations, _sessions, audio = (
            self._monitor(
                [self._pose_result(), self._pose_result()],
                calibration=None,
            )
        )
        monitor._calibrations.calibration = None
        frame = numpy.full((20, 20, 3), 100, dtype=numpy.uint8)
        now = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)

        monitor.observe(
            frame,
            now,
            0.0,
            self._session(),
            self._settings(),
            True,
        )
        monitor.observe(
            frame,
            now + timedelta(seconds=10),
            10.0,
            self._session(),
            self._settings(),
            True,
        )

        self.assertEqual([], observations.events)
        self.assertEqual([], audio.messages)
        self.assertEqual(1, len(activities.events))


class ObservationRepositoryTest(unittest.TestCase):
    def test_records_counts_and_last_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            now = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
            with database.connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO study_sessions (
                        session_date, state, started_at,
                        created_at, updated_at
                    ) VALUES (?, 'STUDYING', ?, ?, ?)
                    """,
                    (
                        now.date().isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                session_id = int(cursor.lastrowid)
                connection.commit()
            repository = ObservationRepository(database)
            repository.record(
                session_id,
                "posture_abnormal",
                now,
                now,
                0.9,
                0.5,
                False,
                "threshold_not_reached",
                {"sample": 1},
            )
            repository.record(
                session_id,
                "posture_abnormal",
                now + timedelta(seconds=10),
                now + timedelta(seconds=10),
                0.9,
                0.6,
                True,
                None,
                {"sample": 2},
            )

            count = repository.recent_count(
                session_id,
                "posture_abnormal",
                now - timedelta(minutes=1),
            )
            last_reminded = repository.last_reminded_at(
                session_id,
                "posture_abnormal",
            )

        self.assertEqual(2, count)
        self.assertEqual(now + timedelta(seconds=10), last_reminded)


if __name__ == "__main__":
    unittest.main()

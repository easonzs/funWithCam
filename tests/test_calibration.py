import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from funwithcam.calibration import (
    representative_sample_index,
    summarize_features,
)
from funwithcam.calibration_steps import CALIBRATION_STEP_KEYS
from funwithcam.storage.calibration_repository import CalibrationRepository
from funwithcam.storage.database import Database


def _capture_payload(step: str, offset: float = 0.0) -> dict:
    return {
        "original_filename": "{}_{}_original.jpg".format(step, offset),
        "annotated_filename": "{}_{}_annotated.jpg".format(step, offset),
        "features": {
            "shoulder_width": {
                "median": 0.2 + offset,
                "p10": 0.18 + offset,
                "p90": 0.22 + offset,
            },
            "brightness": {
                "median": 80 + offset,
                "p10": 75 + offset,
                "p90": 85 + offset,
            },
        },
        "quality": {
            "attempted_frames": 20,
            "valid_samples": 16,
            "valid_ratio": 0.8,
        },
        "camera_fingerprint": "camera-one",
    }


class CalibrationMathTest(unittest.TestCase):
    def test_summarizes_normal_range_and_selects_middle_frame(self):
        samples = [
            {"shoulder_width": 0.10, "brightness": 60},
            {"shoulder_width": 0.20, "brightness": 80},
            {"shoulder_width": 0.30, "brightness": 100},
        ]

        summary = summarize_features(samples)
        representative = representative_sample_index(samples, summary)

        self.assertAlmostEqual(0.20, summary["shoulder_width"]["median"])
        self.assertAlmostEqual(0.12, summary["shoulder_width"]["p10"])
        self.assertAlmostEqual(0.28, summary["shoulder_width"]["p90"])
        self.assertEqual(1, representative)


class CalibrationRepositoryTest(unittest.TestCase):
    def test_four_confirmed_steps_publish_one_version(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = CalibrationRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            draft = repository.create_draft(now)

            version_id = None
            for index, step in enumerate(CALIBRATION_STEP_KEYS):
                moment = now + timedelta(minutes=index)
                repository.mark_capturing(draft["id"], step, moment)
                repository.save_capture(
                    draft["id"],
                    step,
                    _capture_payload(step),
                    moment,
                )
                version_id = repository.confirm_step(draft["id"], moment)

            completed = repository.get_draft(draft["id"])
            latest = repository.get_latest_version()

        self.assertEqual("completed", completed["status"])
        self.assertEqual(version_id, latest["id"])
        self.assertEqual(draft["id"], latest["calibration"]["draft_id"])
        self.assertEqual(
            set(CALIBRATION_STEP_KEYS),
            set(latest["calibration"]["steps"]),
        )

    def test_cancelled_new_draft_does_not_replace_existing_version(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = CalibrationRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            first = repository.create_draft(now)
            for step in CALIBRATION_STEP_KEYS:
                repository.mark_capturing(first["id"], step, now)
                repository.save_capture(
                    first["id"],
                    step,
                    _capture_payload(step),
                    now,
                )
                repository.confirm_step(first["id"], now)
            original_version = repository.get_latest_version()

            second = repository.create_draft(now + timedelta(hours=1))
            repository.mark_capturing(
                second["id"],
                CALIBRATION_STEP_KEYS[0],
                now + timedelta(hours=1),
            )
            repository.save_capture(
                second["id"],
                CALIBRATION_STEP_KEYS[0],
                _capture_payload(CALIBRATION_STEP_KEYS[0], 1.0),
                now + timedelta(hours=1),
            )
            repository.cancel(second["id"], now + timedelta(hours=1))
            latest = repository.get_latest_version()
            second_status = repository.get_draft(second["id"])["status"]

        self.assertEqual(original_version["id"], latest["id"])
        self.assertEqual("cancelled", second_status)

    def test_recovery_marks_interrupted_capture_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = CalibrationRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            draft = repository.create_draft(now)
            repository.mark_capturing(draft["id"], "sitting", now)

            recovered = repository.recover_interrupted(
                now + timedelta(minutes=1)
            )
            current = repository.get_draft(draft["id"])

        self.assertEqual(1, recovered)
        self.assertEqual("error", current["status"])
        self.assertIn("重新启动", current["error_message"])

    def test_retry_preserves_previous_image_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = CalibrationRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            draft = repository.create_draft(now)
            repository.mark_capturing(draft["id"], "sitting", now)
            repository.save_capture(
                draft["id"],
                "sitting",
                _capture_payload("sitting"),
                now,
            )
            repository.retry_step(draft["id"], now)

            current = repository.get_draft(draft["id"])
            filename_allowed = repository.image_filename_allowed(
                draft["id"],
                current["steps"]["sitting"]["attempts"][0][
                    "original_filename"
                ],
            )
            traversal_allowed = repository.image_filename_allowed(
                draft["id"],
                "../sitting_original.jpg",
            )

        attempts = current["steps"]["sitting"]["attempts"]
        self.assertEqual(1, len(attempts))
        self.assertTrue(filename_allowed)
        self.assertFalse(traversal_allowed)


if __name__ == "__main__":
    unittest.main()

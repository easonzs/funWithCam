import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import numpy

from funwithcam.calibration import (
    CalibrationCaptureResult,
    CalibrationCoordinator,
    CameraCalibrationRunner,
    representative_sample_index,
    summarize_features,
)
from funwithcam.calibration_steps import CALIBRATION_STEP_KEYS
from funwithcam.config import load_config
from funwithcam.identity.matcher import FrameMatch
from funwithcam.identity.vision import FaceDetectionResult
from funwithcam.storage.calibration_repository import CalibrationRepository
from funwithcam.storage.database import Database
from funwithcam.runtime_state import set_calibration_active


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    def test_failed_capture_message_explains_actual_cause(self):
        result = CalibrationCaptureResult(
            False,
            "insufficient_samples",
            diagnostics={
                "valid_samples": 4,
                "required_samples": 12,
                "attempted_frames": 20,
            },
        )

        self.assertIn("只获得4 / 12个有效姿态样本", result.message)
        self.assertIn("尝试20帧", result.message)

    def test_system_error_message_preserves_exception_detail(self):
        result = CalibrationCaptureResult(
            False,
            "system_error",
            diagnostics={
                "exception_type": "RuntimeError",
                "error": "video decoder failed",
            },
        )

        self.assertIn("RuntimeError", result.message)
        self.assertIn("video decoder failed", result.message)

    def test_no_pose_message_is_different_from_outside_area(self):
        result = CalibrationCaptureResult(
            False,
            "pose_not_detected",
            diagnostics={"attempted_frames": 18},
        )

        self.assertIn("没有检测到稳定姿态关键点", result.message)
        self.assertNotIn("书桌区域外", result.message)

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


class CalibrationRunnerTest(unittest.TestCase):
    def test_writing_can_collect_pose_after_one_time_face_confirmation(self):
        frames = [
            type(
                "Latest",
                (),
                {"frame": numpy.zeros((12, 12, 3), dtype=numpy.uint8),
                 "captured_monotonic_at": float(index)},
            )()
            for index in range(11)
        ]
        frame_iterator = iter(frames)

        def next_frame(*_args):
            try:
                return next(frame_iterator)
            except StopIteration:
                return frames[-1]

        face = object()
        face_results = [
            FaceDetectionResult(face, (face,), 0.99, (100, 100), "face_ready")
            for _ in range(3)
        ] + [
            FaceDetectionResult(None, (), None, None, "no_face")
            for _ in range(8)
        ]
        config = SimpleNamespace(
            timezone="Asia/Shanghai",
            audio=SimpleNamespace(command=("say", "{text}")),
            video=SimpleNamespace(
                stream_url="rtsp://camera",
                reconnect_seconds=0.1,
                warmup_seconds=0,
                inference_fps=1000,
                max_frame_age_seconds=2,
            ),
            identity=SimpleNamespace(
                detector_model=Path("detector.onnx"),
                recognizer_model=Path("recognizer.onnx"),
                detection_threshold=0.85,
                nms_threshold=0.3,
                min_face_size_px=80,
                match_threshold=0.45,
                ambiguity_margin=0.08,
                confirmation_frames=3,
                confirmation_window_seconds=3,
            ),
            person=SimpleNamespace(
                min_detection_confidence=0.6,
                min_bbox_area_ratio=0.03,
                max_bbox_area_ratio=0.65,
            ),
            calibration=SimpleNamespace(
                duration_seconds=0.01,
                timeout_seconds=1,
                min_valid_samples=2,
                image_dir=Path("data/calibrations"),
            ),
            study_area=SimpleNamespace(rect=(0.2, 0.15, 0.8, 0.95)),
        )
        stop_event = MagicMock()
        stop_event.is_set.return_value = False
        capture = MagicMock()
        capture.get_latest.side_effect = next_frame
        pose_result = (
            {
                "shoulder_center_x": 0.5,
                "shoulder_center_y": 0.5,
                "shoulder_width": 0.2,
                "shoulder_slope": 0.0,
                "head_to_shoulder_ratio": 0.5,
                "torso_offset_ratio": 0.0,
                "bbox_area_ratio": 0.2,
            },
            {},
            0.9,
        )
        with (
            patch("funwithcam.calibration.FaceProfileRepository") as profiles,
            patch("funwithcam.calibration.FaceDetector") as detector,
            patch("funwithcam.calibration.FaceRecognizer") as recognizer,
            patch("funwithcam.calibration.IdentityMatcher") as matcher,
            patch("funwithcam.calibration._PoseFeatureExtractor") as pose,
            patch("funwithcam.calibration.LatestFrameCapture", return_value=capture),
            patch("funwithcam.calibration._save_images"),
            patch("funwithcam.calibration._speak_safely"),
        ):
            profiles.return_value.list_active.return_value = []
            detector.return_value.detect_one.side_effect = face_results
            recognizer.return_value.extract.return_value = (1.0,)
            matcher.return_value.match.return_value = FrameMatch(
                "child", "小明", 0.8, "matched"
            )
            pose.return_value.extract.return_value = pose_result
            with tempfile.TemporaryDirectory() as directory:
                config.calibration.image_dir = Path(directory)
                runner = CameraCalibrationRunner(config, Path("test.db"))
                result = runner("draft", "writing", stop_event)

        self.assertTrue(result.passed)
        self.assertEqual(3, recognizer.return_value.extract.call_count)
        self.assertGreaterEqual(result.quality["valid_samples"], 2)

    def test_caches_face_templates_before_capture_thread_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            Database(database_path).initialize()
            config = load_config(
                PROJECT_ROOT / "config" / "config.example.yaml"
            )
            stop_event = MagicMock()
            stop_event.is_set.return_value = True
            with (
                patch("funwithcam.calibration.FaceProfileRepository") as profiles,
                patch("funwithcam.calibration.FaceDetector"),
                patch("funwithcam.calibration.FaceRecognizer"),
                patch("funwithcam.calibration._PoseFeatureExtractor"),
                patch("funwithcam.calibration.LatestFrameCapture") as capture_class,
                patch("funwithcam.calibration._speak_safely"),
            ):
                capture_class.return_value = MagicMock()
                profiles.return_value.list_active.return_value = []
                runner = CameraCalibrationRunner(config, database_path)
                result = runner("draft", "sitting", stop_event)

        self.assertEqual("cancelled", result.reason)
        profiles.return_value.list_active.assert_called_once_with("formal")


class CalibrationRepositoryTest(unittest.TestCase):
    def test_assistant_pause_timeout_explains_camera_release_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = CalibrationRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            draft = repository.create_draft(now)
            coordinator = CalibrationCoordinator(
                repository,
                ZoneInfo("Asia/Shanghai"),
                lambda *_: CalibrationCaptureResult(True, "passed"),
                assistant_pause_timeout=0.01,
            )
            try:
                with patch(
                    "funwithcam.calibration.wait_for_assistant_pause",
                    return_value=False,
                ):
                    with self.assertRaisesRegex(ValueError, "释放摄像头"):
                        coordinator.request(draft["id"], "sitting", now)
                current = repository.get_draft(draft["id"])
            finally:
                set_calibration_active(False)

        self.assertEqual("error", current["status"])
        self.assertIn("视频流读取阻塞", current["error_message"])

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

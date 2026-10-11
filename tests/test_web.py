import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from funwithcam.calibration import CalibrationCaptureResult
from funwithcam.calibration_steps import CALIBRATION_STEP_KEYS
from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState
from funwithcam.precheck import PrecheckResult
from funwithcam.storage.auth_repository import AuthRepository
from funwithcam.storage.calibration_repository import CalibrationRepository
from funwithcam.storage.database import Database
from funwithcam.storage.reminder_repository import ReminderRepository
from funwithcam.storage.study_session_repository import StudySessionRepository
from funwithcam.storage.workflow_repository import WorkflowRepository
from funwithcam.web import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


PASSED_PRECHECK = PrecheckResult(
    True,
    "passed",
    1.0,
    80.0,
    1.0,
    1.0,
    1.0,
)


def _passed_precheck(stop_event):
    return PASSED_PRECHECK


class BlockingPrecheck:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, stop_event):
        self.started.set()
        self.release.wait(timeout=2)
        return PASSED_PRECHECK


class PassingCalibration:
    def __call__(self, draft_id, step, stop_event):
        return CalibrationCaptureResult(
            True,
            "passed",
            "{}_original.jpg".format(step),
            "{}_annotated.jpg".format(step),
            {
                "shoulder_width": {
                    "median": 0.2,
                    "p10": 0.18,
                    "p90": 0.22,
                },
                "brightness": {
                    "median": 82,
                    "p10": 78,
                    "p90": 86,
                },
            },
            {
                "attempted_frames": 20,
                "valid_samples": 16,
                "valid_ratio": 0.8,
            },
            "camera-one",
        )


def _wait_for_state(database, session_date, expected):
    workflow = WorkflowRepository(database)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = workflow.load(session_date)
        if snapshot is not None and snapshot.state == expected:
            return
        time.sleep(0.01)
    raise AssertionError("workflow did not reach {}".format(expected))


def _wait_for_draft_status(database, draft_id, expected):
    repository = CalibrationRepository(database)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        draft = repository.get_draft(draft_id)
        if draft is not None and draft["status"] == expected:
            return draft
        time.sleep(0.01)
    raise AssertionError("calibration draft did not reach {}".format(expected))


class WebAppTest(unittest.TestCase):
    def test_login_and_add_today_item(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                datetime.now(timezone.utc),
            )
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
                precheck_runner=_passed_precheck,
            )

            with TestClient(app) as client:
                unauthorized = client.get("/", follow_redirects=False)
                self.assertEqual(303, unauthorized.status_code)
                self.assertEqual("/login", unauthorized.headers["location"])

                login = client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                    follow_redirects=False,
                )
                self.assertEqual(303, login.status_code)

                dashboard = client.get("/")
                self.assertEqual(200, dashboard.status_code)
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    dashboard.text,
                ).group(1)

                added = client.post(
                    "/today/items",
                    data={
                        "csrf": csrf,
                        "item_type": "homework",
                        "title": "数学练习册",
                        "estimated_minutes": "30",
                        "preparation_minutes": "0",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, added.status_code)
                self.assertIn("数学练习册", client.get("/").text)

                settings = client.post(
                    "/settings",
                    data={
                        "csrf": csrf,
                        "child_name": "小明",
                        "first_briefing_weekday_time": "17:00",
                        "first_briefing_rest_day_time": "09:00",
                        "second_reminder_weekday_time": "18:10",
                        "second_reminder_rest_day_time": "09:30",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, settings.status_code)
                self.assertIn('value="小明"', client.get("/").text)

    def test_rejects_post_without_csrf(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                datetime.now(timezone.utc),
            )
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
                precheck_runner=_passed_precheck,
            )

            with TestClient(app) as client:
                client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                )
                response = client.post(
                    "/today/items",
                    data={
                        "item_type": "homework",
                        "title": "不应写入",
                    },
                )

        self.assertEqual(403, response.status_code)

    def test_study_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                now,
            )
            WorkflowRepository(database).save(
                now.date().isoformat(),
                WorkflowSnapshot(WorkflowState.WAITING_TO_START),
                now,
            )
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
                precheck_runner=_passed_precheck,
            )

            with TestClient(app) as client:
                client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                )
                dashboard = client.get("/")
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    dashboard.text,
                ).group(1)

                started = client.post(
                    "/study/start",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )
                _wait_for_state(
                    database,
                    now.date().isoformat(),
                    WorkflowState.STUDYING,
                )
                paused = client.post(
                    "/study/pause",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )
                resumed = client.post(
                    "/study/resume",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )
                _wait_for_state(
                    database,
                    now.date().isoformat(),
                    WorkflowState.STUDYING,
                )
                finished = client.post(
                    "/study/finish",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )
        self.assertEqual(303, started.status_code)
        self.assertEqual(303, paused.status_code)
        self.assertEqual(303, resumed.status_code)
        self.assertEqual(303, finished.status_code)

    def test_start_enters_precheck_before_session_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                now,
            )
            WorkflowRepository(database).save(
                now.date().isoformat(),
                WorkflowSnapshot(WorkflowState.WAITING_TO_START),
                now,
            )
            runner = BlockingPrecheck()
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
                precheck_runner=runner,
            )

            with TestClient(app) as client:
                client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                )
                dashboard = client.get("/")
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    dashboard.text,
                ).group(1)
                response = client.post(
                    "/study/start",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )
                self.assertTrue(runner.started.wait(timeout=1))
                snapshot = WorkflowRepository(database).load(
                    now.date().isoformat()
                )
                self.assertEqual(WorkflowState.PRECHECK, snapshot.state)
                self.assertIsNone(
                    StudySessionRepository(database).get_latest(
                        now.date().isoformat()
                    )
                )
                runner.release.set()
                _wait_for_state(
                    database,
                    now.date().isoformat(),
                    WorkflowState.STUDYING,
                )

        self.assertEqual(303, response.status_code)

    def test_first_briefing_can_be_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                now,
            )
            ReminderRepository(database).mark_briefed(
                now.date().isoformat(),
                now,
                now,
                "今日简报",
            )
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
                precheck_runner=_passed_precheck,
            )

            with TestClient(app) as client:
                client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                )
                dashboard = client.get("/")
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    dashboard.text,
                ).group(1)
                reset = client.post(
                    "/today/reset-first-briefing",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )

            status = ReminderRepository(database).get_daily_status(
                now.date().isoformat()
            )

        self.assertEqual(303, reset.status_code)
        self.assertEqual(
            "WAITING_FOR_FIRST_APPEARANCE",
            status["workflow_state"],
        )

    def test_calibration_wizard_requires_login_and_publishes_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "test.db"
            image_dir = root / "calibration-images"
            config_path = root / "config.yaml"
            config_path.write_text(
                (
                    PROJECT_ROOT / "config" / "config.example.yaml"
                ).read_text(encoding="utf-8").replace(
                    "image_dir: data/calibrations",
                    "image_dir: {}".format(image_dir),
                ),
                encoding="utf-8",
            )
            database = Database(database_path)
            database.initialize()
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                datetime.now(timezone.utc),
            )
            app = create_app(
                config_path,
                database_path,
                precheck_runner=_passed_precheck,
                calibration_runner=PassingCalibration(),
            )

            with TestClient(app) as client:
                unauthorized = client.get(
                    "/calibration",
                    follow_redirects=False,
                )
                self.assertEqual(303, unauthorized.status_code)

                client.post(
                    "/login",
                    data={
                        "username": "parent",
                        "password": "long-password",
                    },
                )
                page = client.get("/calibration")
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    page.text,
                ).group(1)
                started = client.post(
                    "/calibration/start",
                    data={"csrf": csrf},
                    follow_redirects=False,
                )
                self.assertEqual(303, started.status_code)
                repository = CalibrationRepository(database)
                draft = repository.get_active()
                self.assertIsNotNone(draft)

                missing_csrf = client.post(
                    "/calibration/capture",
                    data={
                        "draft_id": draft["id"],
                        "step": "sitting",
                    },
                )
                self.assertEqual(403, missing_csrf.status_code)

                for index, step in enumerate(CALIBRATION_STEP_KEYS):
                    captured = client.post(
                        "/calibration/capture",
                        data={
                            "csrf": csrf,
                            "draft_id": draft["id"],
                            "step": step,
                        },
                        follow_redirects=False,
                    )
                    self.assertEqual(303, captured.status_code)
                    reviewed = _wait_for_draft_status(
                        database,
                        draft["id"],
                        "review",
                    )
                    review_page = client.get("/calibration")
                    self.assertIn("查看标注图", review_page.text)
                    self.assertIn("查看原图", review_page.text)

                    if index == 0:
                        filename = reviewed["steps"][step][
                            "annotated_filename"
                        ]
                        image_path = image_dir / draft["id"] / filename
                        image_path.parent.mkdir(parents=True, exist_ok=True)
                        image_path.write_bytes(b"\xff\xd8\xff\xd9")
                        image = client.get(
                            "/calibration/image/{}/{}".format(
                                draft["id"],
                                filename,
                            )
                        )
                        unknown = client.get(
                            "/calibration/image/{}/unknown.jpg".format(
                                draft["id"]
                            )
                        )
                        self.assertEqual(200, image.status_code)
                        self.assertEqual(404, unknown.status_code)

                    confirmed = client.post(
                        "/calibration/confirm",
                        data={
                            "csrf": csrf,
                            "draft_id": draft["id"],
                        },
                        follow_redirects=False,
                    )
                    self.assertEqual(303, confirmed.status_code)

                completed = client.get("/calibration")
                latest = repository.get_latest_version()

        self.assertIn("四项基准已保存", completed.text)
        self.assertIsNotNone(latest)
        self.assertIn("data-view=\"annotated\"", review_page.text)


if __name__ == "__main__":
    unittest.main()

import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState
from funwithcam.precheck import PrecheckResult
from funwithcam.storage.auth_repository import AuthRepository
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


def _wait_for_state(database, session_date, expected):
    workflow = WorkflowRepository(database)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = workflow.load(session_date)
        if snapshot is not None and snapshot.state == expected:
            return
        time.sleep(0.01)
    raise AssertionError("workflow did not reach {}".format(expected))


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


if __name__ == "__main__":
    unittest.main()

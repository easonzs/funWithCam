import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from funwithcam.calibration import CalibrationCaptureResult
from funwithcam.calibration_steps import CALIBRATION_STEP_KEYS
from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState
from funwithcam.precheck import PrecheckResult
from funwithcam.storage.activity_repository import ActivityRepository
from funwithcam.storage.auth_repository import AuthRepository
from funwithcam.storage.calibration_repository import CalibrationRepository
from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
from funwithcam.storage.interaction_repository import SpeechIntentRepository
from funwithcam.storage.reminder_repository import ReminderRepository
from funwithcam.storage.study_session_repository import StudySessionRepository
from funwithcam.storage.speech_template_repository import SpeechTemplateRepository
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
    def test_health_endpoints_report_setup_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            app = create_app(
                PROJECT_ROOT / "config" / "config.example.yaml",
                database_path,
                precheck_runner=_passed_precheck,
            )

            with TestClient(app) as client:
                live = client.get("/health/live")
                ready = client.get("/health/ready")

        self.assertEqual(200, live.status_code)
        self.assertEqual({"status": "ok"}, live.json())
        self.assertEqual(200, ready.status_code)
        self.assertEqual("ready", ready.json()["status"])
        self.assertTrue(ready.json()["setup_required"])
        self.assertEqual(
            ["child", "parent"],
            ready.json()["missing_identities"],
        )
        self.assertEqual([], ready.json()["missing_models"])

    def test_interaction_rules_task_types_and_parent_evidence_can_be_saved(self):
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
                    data={
                        "username": "parent",
                        "password": "long-password",
                    },
                )
                page = client.get("/interaction")
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    page.text,
                ).group(1)

                settings_response = client.post(
                    "/interaction/settings",
                    data={
                        "csrf": csrf,
                        "dialogue_first_retry_seconds": "15",
                        "dialogue_next_retry_seconds": "45",
                        "dialogue_max_prompts": "2",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, settings_response.status_code)

                speech_response = client.post(
                    "/speech/start",
                    data={
                        "csrf": csrf,
                        "custom_phrases": "现在开始\n准备学习",
                        "keyword_groups": "开始,准备\n作业,学习",
                        "confirmation_mode": "always",
                        "confirmation_prompt": "要开始吗？",
                        "confirm_phrases": "确认开始",
                        "cancel_phrases": "取消",
                        "enabled": "on",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, speech_response.status_code)

                task_type_response = client.post(
                    "/task-types",
                    data={
                        "csrf": csrf,
                        "task_key": "parent_review",
                        "label": "家长检查",
                        "category": "other",
                        "completion_mode": "parent",
                        "finish_prompt": "请家长确认“{title}”。",
                        "include_in_finish_check": "on",
                        "enabled": "on",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, task_type_response.status_code)
                self.assertTrue(
                    task_type_response.headers["location"].startswith(
                        "/finish-checks?"
                    )
                )

                add_response = client.post(
                    "/today/items",
                    data={
                        "csrf": csrf,
                        "item_type": "temporary",
                        "title": "检查书包",
                        "estimated_minutes": "",
                        "preparation_minutes": "0",
                        "task_type_key": "parent_review",
                        "finish_check_present": "1",
                        "include_in_finish_check": "on",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, add_response.status_code)

                today = datetime.now(
                    ZoneInfo("Asia/Shanghai")
                ).date()
                family = FamilyRepository(database)
                item = next(
                    item
                    for item in family.list_agenda(today)
                    if item["title"] == "检查书包"
                )
                item_id = item["id"]
                self.assertEqual(1, item["include_in_finish_check"])
                finish_check_response = client.post(
                    "/today/items/{}/finish-check".format(item_id),
                    data={"csrf": csrf, "enabled": "0"},
                    follow_redirects=False,
                )
                self.assertEqual(303, finish_check_response.status_code)
                done_response = client.post(
                    "/today/items/{}/status".format(item_id),
                    data={"csrf": csrf, "status": "done"},
                    follow_redirects=False,
                )
                self.assertEqual(303, done_response.status_code)

            settings = family.get_settings()
            saved_intent = SpeechIntentRepository(database).get("start")
            saved_item = next(
                agenda_item
                for agenda_item in family.list_agenda(today)
                if agenda_item["id"] == item_id
            )

        self.assertEqual("15", settings["dialogue_first_retry_seconds"])
        self.assertEqual("always", saved_intent["confirmation_mode"])
        self.assertEqual(
            [["开始", "准备"], ["作业", "学习"]],
            saved_intent["keyword_groups"],
        )
        self.assertEqual(
            ["现在开始", "准备学习"],
            saved_intent["custom_phrases"],
        )
        self.assertEqual("done", saved_item["status"])
        self.assertEqual(
            "parent_confirmed",
            saved_item["completion_evidence"],
        )
        self.assertEqual(0, saved_item["include_in_finish_check"])

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
                self.assertTrue(
                    added.headers["location"].startswith("/schedule?")
                )
                self.assertIn("数学练习册", client.get("/schedule").text)

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
                self.assertTrue(
                    settings.headers["location"].startswith("/settings?")
                )
                self.assertIn('value="小明"', client.get("/settings").text)
                self.assertNotIn('name="child_name"', client.get("/").text)

    def test_page_structure_routes_forms_and_navigation(self):
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
                protected_paths = (
                    "/",
                    "/schedule",
                    "/records",
                    "/history",
                    "/config",
                    "/config/arrival",
                    "/config/start",
                    "/config/studying",
                    "/config/away",
                    "/config/finish",
                    "/config/checkin",
                    "/config/speech",
                    "/config/speech-templates",
                    "/config/interaction",
                    "/config/device",
                    "/config/profile",
                    "/config/settings",
                    "/config/privacy",
                    "/settings",
                    "/speech",
                    "/speech-templates",
                    "/interaction",
                    "/finish-checks",
                    "/guide",
                    "/reminders",
                    "/schedule/new",
                    "/courses/new",
                    "/today/settings",
                    "/calibration",
                )
                for path in protected_paths:
                    unauthorized = client.get(path, follow_redirects=False)
                    self.assertEqual(303, unauthorized.status_code, path)
                    self.assertEqual("/login", unauthorized.headers["location"])

                client.post(
                    "/login",
                    data={"username": "parent", "password": "long-password"},
                )

                for path in protected_paths:
                    page = client.get(path)
                    self.assertEqual(200, page.status_code, path)
                    self.assertIn('class="desktop-nav"', page.text)
                    self.assertIn('class="mobile-nav"', page.text)

                dashboard = client.get("/")
                self.assertIn('href="/" aria-current="page"', dashboard.text)
                self.assertIn("安排摘要", dashboard.text)
                self.assertIn("今天的学习摘要", dashboard.text)
                self.assertNotIn('name="child_name"', dashboard.text)
                self.assertNotIn('action="/courses"', dashboard.text)
                self.assertNotIn('action="/today/preferences"', dashboard.text)
                csrf = re.search(
                    r'name="csrf" value="([^"]+)"',
                    dashboard.text,
                ).group(1)

                schedule = client.get("/schedule")
                self.assertIn(
                    'href="/schedule" aria-current="page"',
                    schedule.text,
                )
                self.assertNotIn('action="/today/items"', schedule.text)
                self.assertNotIn('action="/courses"', schedule.text)

                records = client.get("/records")
                self.assertIn(
                    'href="/records" aria-current="page"',
                    records.text,
                )
                self.assertIn("本周学习次数", records.text)
                self.assertIn("平均学习时长", records.text)
                self.assertIn("执行历史", records.text)

                config_page = client.get("/config/arrival")
                self.assertIn(
                    'href="/config/studying"',
                    config_page.text,
                )
                self.assertIn(
                    'href="/config/arrival" class="is-active"',
                    config_page.text,
                )
                self.assertIn("内容从哪里来", config_page.text)
                self.assertIn("去日程维护安排", config_page.text)
                self.assertIn("配置中心 / 到家与日程提醒", config_page.text)

                history = client.get("/history")
                self.assertIn(
                    'href="/records" aria-current="page"',
                    history.text,
                )
                self.assertIn("执行历史", history.text)
                self.assertIn('name="selected_date"', history.text)
                self.assertIn('name="category"', history.text)

                settings = client.get("/settings")
                self.assertIn(
                    'href="/settings" aria-current="page"',
                    settings.text,
                )
                self.assertIn('name="child_name"', settings.text)
                self.assertNotIn("data-speech-command=", settings.text)

                speech = client.get("/speech")
                self.assertIn(
                    'href="/speech" aria-current="page"',
                    speech.text,
                )
                self.assertIn("可用语音口令", speech.text)
                self.assertIn(
                    'data-speech-command="start"',
                    speech.text,
                )
                self.assertIn("开始写作业", speech.text)
                self.assertIn("今天几点上课", speech.text)
                self.assertIn("结束打卡", speech.text)
                self.assertIn("结束录制", speech.text)
                self.assertIn("仅在视频正在录制时参与匹配", speech.text)
                self.assertIn("系统默认口令", speech.text)
                self.assertIn("自定义追加口令", speech.text)
                self.assertIn("关键词组合怎么写", speech.text)
                self.assertIn("name=\"custom_phrases\"", speech.text)
                self.assertNotIn("name=\"phrases\"", speech.text)

                speech_templates = client.get("/speech-templates")
                self.assertIn(
                    'href="/speech-templates" aria-current="page"',
                    speech_templates.text,
                )
                self.assertIn("变量从哪里来", speech_templates.text)
                self.assertIn("今天安排简报", speech_templates.text)
                self.assertIn("系统不会读取 Mac 日历", speech_templates.text)
                self.assertIn("事件话术模板", speech_templates.text)

                save_template = client.post(
                    "/speech-templates/first_briefing",
                    data={
                        "csrf": csrf,
                        "template_text": "{孩子称呼}|{今天安排简报}",
                        "enabled": "on",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, save_template.status_code)
                self.assertEqual(
                    "{孩子称呼}|{今天安排简报}",
                    SpeechTemplateRepository(database)
                    .get("first_briefing")["template_text"],
                )
                invalid_template = client.post(
                    "/speech-templates/first_briefing",
                    data={
                        "csrf": csrf,
                        "template_text": "{不存在的变量}",
                        "enabled": "on",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(303, invalid_template.status_code)
                self.assertIn(
                    "不支持的话术变量",
                    unquote(invalid_template.headers["location"]),
                )

                interaction = client.get("/interaction")
                self.assertIn(
                    'href="/interaction" aria-current="page"',
                    interaction.text,
                )
                self.assertIn("当前规则", interaction.text)
                self.assertIn("等待与重复询问", interaction.text)
                self.assertIn("这组设置用在哪里", interaction.text)
                self.assertNotIn("离座阈值", interaction.text)
                self.assertNotIn("任务与打卡类型", interaction.text)

                finish_checks = client.get("/finish-checks")
                self.assertIn(
                    'href="/finish-checks" aria-current="page"',
                    finish_checks.text,
                )
                self.assertIn("模板如何使用", finish_checks.text)
                self.assertIn("可用于新增日程", finish_checks.text)
                self.assertIn("保存 学校作业", finish_checks.text)
                self.assertIn("创建结束检查模板", finish_checks.text)

                guide = client.get("/guide")
                self.assertIn(
                    'href="/guide" aria-current="page"',
                    guide.text,
                )
                self.assertIn("学习中离开书桌", guide.text)
                self.assertIn("询问：继续学习还是结束学习", guide.text)
                self.assertIn("视觉监测已支持", guide.text)
                self.assertIn("视频打卡", guide.text)

                reminders = client.get("/reminders")
                self.assertIn(
                    'href="/reminders" aria-current="page"',
                    reminders.text,
                )
                self.assertIn("学习过程规则", reminders.text)
                self.assertIn("当前话术示例", reminders.text)
                self.assertIn("当前默认话术", reminders.text)
                self.assertIn("头稍微抬高一点，保持舒服的坐姿", reminders.text)
                reminder_values = {
                    "observation_interval_seconds": "20",
                    "posture_trigger_count": "4",
                    "posture_trigger_window_minutes": "6",
                    "posture_reminder_cooldown_minutes": "11",
                    "light_trigger_count": "3",
                    "light_trigger_window_minutes": "7",
                    "light_reminder_cooldown_minutes": "12",
                    "seat_leave_trigger_count": "4",
                    "seat_leave_trigger_window_minutes": "13",
                    "seat_leave_reminder_cooldown_minutes": "14",
                    "away_auto_pause_minutes": "3",
                    "away_interrupted_minutes": "50",
                }
                saved_reminders = client.post(
                    "/reminders",
                    data={"csrf": csrf, **reminder_values},
                    follow_redirects=False,
                )
                self.assertEqual(303, saved_reminders.status_code)
                self.assertTrue(
                    saved_reminders.headers["location"].startswith(
                        "/reminders?"
                    )
                )
                refreshed_reminders = client.get("/reminders")
                self.assertIn('value="20"', refreshed_reminders.text)
                self.assertIn('value="14"', refreshed_reminders.text)
                self.assertIn('value="3"', refreshed_reminders.text)
                self.assertIn('value="50"', refreshed_reminders.text)
                self.assertIn("与“重复询问”不同", refreshed_reminders.text)

                agenda_form = client.get("/schedule/new")
                self.assertIn('action="/today/items"', agenda_form.text)
                self.assertIn(
                    'name="include_in_finish_check"',
                    agenda_form.text,
                )
                course_form = client.get("/courses/new")
                self.assertIn('action="/courses"', course_form.text)
                today_settings = client.get("/today/settings")
                self.assertIn(
                    'action="/today/preferences"',
                    today_settings.text,
                )

                course = client.post(
                    "/courses",
                    data={
                        "csrf": csrf,
                        "name": "英语课",
                        "weekday": "1",
                        "start_time": "19:00",
                        "delivery_type": "online",
                        "preparation_minutes": "10",
                        "travel_minutes": "0",
                    },
                    follow_redirects=False,
                )
                self.assertTrue(
                    course.headers["location"].startswith(
                        "/schedule?tab=courses&message="
                    )
                )

    def test_history_page_filters_and_displays_events(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            database = Database(database_path)
            database.initialize()
            occurred_at = datetime(
                2026,
                10,
                11,
                18,
                5,
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
            AuthRepository(database).set_password(
                "parent",
                "long-password",
                occurred_at,
            )
            activities = ActivityRepository(database)
            activities.record(
                occurred_at,
                "speech",
                "speech_input",
                "camera_audio",
                "info",
                "识别到语音口令",
                "开始学习",
                {"command": "start"},
            )
            activities.record(
                occurred_at,
                "reminder",
                "first_briefing",
                "assistant",
                "success",
                "首次提醒已播放",
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
                response = client.get(
                    "/history?selected_date=2026-10-11&category=speech"
                )

        self.assertEqual(200, response.status_code)
        self.assertIn("18:05:00", response.text)
        self.assertIn("识别到语音口令", response.text)
        self.assertIn("开始学习", response.text)
        self.assertIn("摄像头麦克风", response.text)
        self.assertIn("关联口令", response.text)
        self.assertNotIn("首次提醒已播放", response.text)

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
            activity = ActivityRepository(database).list_for_date(
                now.date().isoformat()
            )

        self.assertEqual(303, reset.status_code)
        self.assertEqual(
            "WAITING_FOR_FIRST_APPEARANCE",
            status["workflow_state"],
        )
        self.assertEqual("first_briefing_reset", activity[0]["event_type"])

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

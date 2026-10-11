import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from funwithcam.assistant_service import run_assistant
from funwithcam.calibration import (
    CalibrationCaptureResult,
    CalibrationCoordinator,
    CameraCalibrationRunner,
)
from funwithcam.calibration_steps import (
    CALIBRATION_STEP_BY_KEY,
    CALIBRATION_STEPS,
)
from funwithcam.config import load_config
from funwithcam.precheck import (
    CameraPrecheckRunner,
    PrecheckCoordinator,
    PrecheckResult,
)
from funwithcam.speech import run_speech_service
from funwithcam.storage.auth_repository import AuthRepository, csrf_matches
from funwithcam.storage.calibration_repository import CalibrationRepository
from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
from funwithcam.storage.reminder_repository import ReminderRepository
from funwithcam.storage.study_session_repository import StudySessionRepository
from funwithcam.storage.workflow_repository import WorkflowRepository


SESSION_COOKIE = "funwithcam_session"
WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
WORKFLOW_LABELS = {
    "WAITING_FOR_FIRST_APPEARANCE": "等待孩子首次出现",
    "IDENTITY_CONFIRMING": "正在确认身份",
    "BRIEFED": "正在播放今日简报",
    "WAITING_TO_START": "今日简报已完成",
    "PRECHECK": "开始前检查",
    "STUDYING": "学习记录中",
    "AWAY_GRACE": "短暂离座",
    "PAUSED": "学习已暂停",
    "INTERRUPTED": "学习已中断",
    "FINISH_CONFIRMING": "等待结束确认",
    "COMPLETED": "今日学习已结束",
    "DISABLED": "今日提醒已停用",
}
WORKFLOW_GUIDANCE = {
    "WAITING_FOR_FIRST_APPEARANCE": "系统会在合适时间识别孩子并播放今日安排。",
    "IDENTITY_CONFIRMING": "正在确认书桌区域里是否是孩子。",
    "BRIEFED": "正在播放今日安排，请稍候。",
    "WAITING_TO_START": "今日安排已经播报，孩子准备好后可以开始学习。",
    "PRECHECK": "正在检查摄像头、书桌区域、孩子身份和光线。",
    "STUDYING": "正在记录本次学习，姿势和光线异常会写入记录。",
    "AWAY_GRACE": "孩子暂时离开座位，系统正在等待返回。",
    "PAUSED": "本次学习已经暂停，继续时会重新检查书桌状态。",
    "INTERRUPTED": "学习记录暂时中断，可以重新开始检查。",
    "FINISH_CONFIRMING": "正在等待确认是否结束本次学习。",
    "COMPLETED": "今天的学习记录已经结束，可以再次开始新的记录。",
    "DISABLED": "今天不会继续播放提醒。",
}


class _LoginRequired(Exception):
    pass


LOGGER = logging.getLogger(__name__)


def create_app(
    config_path: Path,
    database_path: Path,
    assistant_enabled: bool = False,
    precheck_runner: Optional[
        Callable[[threading.Event], PrecheckResult]
    ] = None,
    calibration_runner: Optional[
        Callable[
            [str, str, threading.Event],
            CalibrationCaptureResult,
        ]
    ] = None,
) -> FastAPI:
    config = load_config(config_path)
    timezone = ZoneInfo(config.timezone)
    database = Database(database_path)
    database.initialize()
    auth = AuthRepository(database)
    family = FamilyRepository(database)
    reminders = ReminderRepository(database)
    study_sessions = StudySessionRepository(database)
    calibrations = CalibrationRepository(database)
    calibrations.recover_interrupted(datetime.now(timezone))
    prechecks = PrecheckCoordinator(
        study_sessions,
        timezone,
        precheck_runner or CameraPrecheckRunner(config, database_path),
    )
    calibration_coordinator = CalibrationCoordinator(
        calibrations,
        timezone,
        calibration_runner
        or CameraCalibrationRunner(config, database_path),
    )
    workflow = WorkflowRepository(database)
    web_root = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(web_root / "templates"))

    def render_page(
        request: Request,
        template_name: str,
        session,
        active_nav: str,
        **context,
    ):
        context.update(
            {
                "csrf": session.csrf_token,
                "active_nav": active_nav,
            }
        )
        return templates.TemplateResponse(
            request,
            template_name,
            context,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event = None
        assistant_thread = None
        speech_thread = None
        if assistant_enabled:
            stop_event = threading.Event()
            assistant_thread = threading.Thread(
                target=_run_assistant_thread,
                args=(config_path, database_path, stop_event),
                name="assistant-service",
                daemon=True,
            )
            assistant_thread.start()
            if config.speech.enabled:
                speech_thread = threading.Thread(
                    target=_run_speech_thread,
                    args=(
                        config_path,
                        database_path,
                        stop_event,
                        prechecks,
                    ),
                    name="speech-service",
                    daemon=True,
                )
                speech_thread.start()
        try:
            yield
        finally:
            if stop_event is not None:
                stop_event.set()
            if assistant_thread is not None:
                assistant_thread.join(timeout=5)
            if speech_thread is not None:
                speech_thread.join(timeout=5)
            prechecks.close()
            calibration_coordinator.close()

    app = FastAPI(
        title="FunWithCam",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(web_root / "static")),
        name="static",
    )

    @app.exception_handler(_LoginRequired)
    async def login_required_handler(request: Request, exc: _LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, error: Optional[str] = None):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": error},
        )

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        token = auth.create_session(
            str(form.get("username", "")),
            str(form.get("password", "")),
            datetime.now(timezone),
            config.web.session_hours,
        )
        if token is None:
            return _redirect("/login", error="用户名或密码不正确")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=config.web.session_hours * 3600,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        auth.delete_session(token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        today = datetime.now(timezone).date()
        snapshot = workflow.load(today.isoformat())
        workflow_value = (
            snapshot.state.value
            if snapshot is not None
            else "WAITING_FOR_FIRST_APPEARANCE"
        )
        items = family.list_agenda(today)
        today_courses = family.list_courses_for_date(today)
        preferences = family.get_daily_preferences(today)
        settings = family.get_settings()
        reminder_summary = _reminder_summary(
            today.weekday(),
            preferences,
            settings,
        )
        return render_page(
            request,
            "dashboard.html",
            session,
            "today",
            today=today,
            weekday=WEEKDAY_NAMES[today.weekday()],
            items=items,
            today_courses=today_courses,
            reminder_summary=reminder_summary,
            workflow_state=WORKFLOW_LABELS[workflow_value],
            workflow_guidance=WORKFLOW_GUIDANCE[workflow_value],
            workflow_value=workflow_value,
            study_session=study_sessions.get_latest(today.isoformat()),
            precheck_result=prechecks.latest(today.isoformat()),
            message=message,
            error=error,
        )

    @app.get("/schedule", response_class=HTMLResponse)
    def schedule_page(
        request: Request,
        tab: str = "today",
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        if tab not in ("today", "courses"):
            tab = "today"
        today = datetime.now(timezone).date()
        return render_page(
            request,
            "schedule.html",
            session,
            "schedule",
            tab=tab,
            today=today,
            weekday=WEEKDAY_NAMES[today.weekday()],
            items=family.list_agenda(today),
            weekly_courses=family.list_weekly_courses(),
            message=message,
            error=error,
        )

    @app.get("/schedule/new", response_class=HTMLResponse)
    def new_agenda_page(
        request: Request,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "agenda_form.html",
            session,
            "schedule",
            error=error,
        )

    @app.get("/courses/new", response_class=HTMLResponse)
    def new_course_page(
        request: Request,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "course_form.html",
            session,
            "schedule",
            error=error,
        )

    @app.get("/today/settings", response_class=HTMLResponse)
    def today_settings_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        today = datetime.now(timezone).date()
        return render_page(
            request,
            "today_settings.html",
            session,
            "today",
            today=today,
            weekday=WEEKDAY_NAMES[today.weekday()],
            preferences=family.get_daily_preferences(today),
            message=message,
            error=error,
        )

    @app.get("/records", response_class=HTMLResponse)
    def records_page(request: Request):
        _, session = _require_session(request, auth, timezone)
        records = study_sessions.list_recent()
        for record in records:
            record["study_minutes"] = int(round(record["study_minutes"]))
            record["state_label"] = WORKFLOW_LABELS.get(
                record["state"],
                record["state"],
            )
        return render_page(
            request,
            "records.html",
            session,
            "records",
            records=records,
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        today = datetime.now(timezone).date()
        return render_page(
            request,
            "settings.html",
            session,
            "settings",
            settings=family.get_settings(),
            calibration_version=calibrations.get_latest_version(),
            speech_enabled=config.speech.enabled,
            camera_configured=bool(config.video.stream_url.strip()),
            today=today,
            message=message,
            error=error,
        )

    @app.get("/calibration", response_class=HTMLResponse)
    def calibration_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        draft = (
            calibrations.get_active()
            or calibrations.get_latest_completed_draft()
        )
        current = None
        current_number = None
        if draft is not None:
            current = CALIBRATION_STEP_BY_KEY[draft["current_step"]]
            current_number = next(
                index
                for index, item in enumerate(CALIBRATION_STEPS, start=1)
                if item["key"] == draft["current_step"]
            )
        return render_page(
            request,
            "calibration.html",
            session,
            "settings",
            draft=draft,
            current=current,
            current_number=current_number,
            steps=CALIBRATION_STEPS,
            latest_version=calibrations.get_latest_version(),
            study_active=calibrations.has_active_study_session(),
            message=message,
            error=error,
        )

    @app.post("/calibration/start")
    async def start_calibration(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            calibrations.create_draft(datetime.now(timezone))
        except ValueError as exc:
            return _redirect("/calibration", error=str(exc))
        return _redirect(
            "/calibration",
            message="基准录入已建立，请从正常坐姿开始",
        )

    @app.post("/calibration/capture")
    async def capture_calibration_step(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        draft_id = str(form.get("draft_id", ""))
        step = str(form.get("step", ""))
        try:
            calibration_coordinator.request(
                draft_id,
                step,
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/calibration", error=str(exc))
        return _redirect(
            "/calibration",
            message="正在采集，通常需要约 10 秒",
        )

    @app.post("/calibration/confirm")
    async def confirm_calibration_step(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            version_id = calibrations.confirm_step(
                str(form.get("draft_id", "")),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/calibration", error=str(exc))
        if version_id is not None:
            return _redirect(
                "/calibration",
                message="四项基准已保存为版本 {}".format(version_id),
            )
        return _redirect(
            "/calibration",
            message="这一步已确认，请继续下一步",
        )

    @app.post("/calibration/retry")
    async def retry_calibration_step(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            calibrations.retry_step(
                str(form.get("draft_id", "")),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/calibration", error=str(exc))
        return _redirect("/calibration", message="可以重新采集当前步骤")

    @app.post("/calibration/cancel")
    async def cancel_calibration(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            calibrations.cancel(
                str(form.get("draft_id", "")),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/calibration", error=str(exc))
        return _redirect(
            "/calibration",
            message="本次录入已取消，已经采集的图片仍保留在本机",
        )

    @app.get("/calibration/image/{draft_id}/{filename}")
    def calibration_image(draft_id: str, filename: str, request: Request):
        _require_session(request, auth, timezone)
        if not calibrations.image_filename_allowed(draft_id, filename):
            raise HTTPException(status_code=404, detail="image not found")
        draft_dir = (config.calibration.image_dir / draft_id).resolve()
        image_path = (draft_dir / filename).resolve()
        if image_path.parent != draft_dir or not image_path.is_file():
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(image_path, media_type="image/jpeg")

    @app.post("/today/items")
    async def add_today_item(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            family.add_agenda(
                datetime.now(timezone).date(),
                str(form.get("item_type", "")),
                str(form.get("title", "")),
                _form_optional(form.get("subject")),
                _form_optional(form.get("scheduled_time")),
                _optional_int(form.get("estimated_minutes")),
                _int_or_zero(form.get("preparation_minutes")),
                str(form.get("notes", "")),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/schedule/new", error=str(exc))
        return _redirect("/schedule", message="今日安排已添加")

    @app.post("/today/items/{item_id}/status")
    async def set_item_status(item_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            family.set_agenda_status(
                item_id,
                str(form.get("status", "")),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/schedule", error=str(exc))
        return _redirect("/schedule", message="完成状态已更新")

    @app.post("/today/items/{item_id}/delete")
    async def delete_item(item_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        family.delete_agenda(item_id)
        return _redirect("/schedule", message="安排已删除")

    @app.post("/today/preferences")
    async def save_today_preferences(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            family.save_daily_preferences(
                datetime.now(timezone).date(),
                str(form.get("day_type", "")),
                str(form.get("second_reminder_mode", "")),
                _form_optional(form.get("second_reminder_time")),
                form.get("homework_reminders_enabled") == "on",
                form.get("posture_reminders_enabled") == "on",
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/today/settings", error=str(exc))
        return _redirect(
            "/today/settings",
            message="今天的提醒设置已保存",
        )

    @app.post("/today/reset-first-briefing")
    async def reset_first_briefing(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            reminders.reset_day(
                datetime.now(timezone).date().isoformat(),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/settings", error=str(exc))
        return _redirect("/settings", message="今日首次提醒已重置")

    @app.post("/study/{action}")
    async def change_study_state(action: str, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        today = datetime.now(timezone).date().isoformat()
        now = datetime.now(timezone)
        try:
            if action == "start":
                prechecks.request("start", today, "web", now)
                message = "正在进行开始前检查，请在书桌前保持 5 秒"
            elif action == "pause":
                study_sessions.pause(today, now)
                message = "学习记录已暂停"
            elif action == "resume":
                prechecks.request("resume", today, "web", now)
                message = "正在进行开始前检查，请在书桌前保持 5 秒"
            elif action == "finish":
                study_sessions.finish(today, now)
                message = "学习记录已结束"
            else:
                raise ValueError("不支持的学习操作")
        except ValueError as exc:
            return _redirect("/", error=str(exc))
        return _redirect("/", message=message)

    @app.post("/courses")
    async def add_course(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            family.add_weekly_course(
                str(form.get("name", "")),
                int(str(form.get("weekday", "0"))),
                str(form.get("start_time", "")),
                str(form.get("delivery_type", "")),
                _int_or_zero(form.get("preparation_minutes")),
                _int_or_zero(form.get("travel_minutes")),
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/courses/new", error=str(exc))
        return _redirect(
            "/schedule?tab=courses",
            message="固定课程已添加",
        )

    @app.post("/courses/{course_id}/enabled")
    async def set_course_enabled(course_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        family.set_course_enabled(
            course_id,
            str(form.get("enabled", "")) == "1",
            datetime.now(timezone),
        )
        return _redirect(
            "/schedule?tab=courses",
            message="课程状态已更新",
        )

    @app.post("/courses/{course_id}/delete")
    async def delete_course(course_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        family.delete_weekly_course(course_id)
        return _redirect(
            "/schedule?tab=courses",
            message="固定课程已删除",
        )

    @app.post("/settings")
    async def save_settings(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            family.save_settings(
                {
                    "child_name": str(form.get("child_name", "")),
                    "first_briefing_weekday_time": str(
                        form.get("first_briefing_weekday_time", "")
                    ),
                    "first_briefing_rest_day_time": str(
                        form.get("first_briefing_rest_day_time", "")
                    ),
                    "second_reminder_weekday_time": str(
                        form.get("second_reminder_weekday_time", "")
                    ),
                    "second_reminder_rest_day_time": str(
                        form.get("second_reminder_rest_day_time", "")
                    ),
                },
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/settings", error=str(exc))
        return _redirect("/settings", message="长期设置已保存")

    return app


def _require_session(request: Request, auth: AuthRepository, timezone: ZoneInfo):
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        raise _LoginRequired()
    session = auth.get_session(token, datetime.now(timezone))
    if session is None:
        raise _LoginRequired()
    return token, session


def _require_csrf(token: str, submitted: str) -> None:
    if not csrf_matches(token, submitted):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def _redirect(path: str, **query: str) -> RedirectResponse:
    if query:
        separator = "&" if "?" in path else "?"
        path = "{}{}{}".format(path, separator, urlencode(query))
    return RedirectResponse(path, status_code=303)


def _reminder_summary(
    weekday: int,
    preferences: dict,
    settings: dict,
) -> dict:
    day_type = preferences["day_type"]
    if day_type == "auto":
        day_type = "school_day" if weekday < 5 else "rest_day"
        day_type_label = "上学日（自动判断）" if weekday < 5 else "休息日（自动判断）"
    elif day_type == "school_day":
        day_type_label = "上学日（今天单独设置）"
    else:
        day_type_label = "休息日（今天单独设置）"

    suffix = "weekday" if day_type == "school_day" else "rest_day"
    first_time = settings["first_briefing_{}_time".format(suffix)]
    second_mode = preferences["second_reminder_mode"]
    if second_mode == "disabled":
        second_label = "今天不进行第二次提醒"
    elif second_mode == "custom":
        second_label = "{}（今天单独设置）".format(
            preferences["second_reminder_time"]
        )
    else:
        second_label = "{}（使用长期设置）".format(
            settings["second_reminder_{}_time".format(suffix)]
        )

    return {
        "day_type": day_type_label,
        "first_briefing": first_time,
        "second_reminder": second_label,
        "homework_reminders": (
            "开启" if preferences["homework_reminders_enabled"] else "关闭"
        ),
        "posture_reminders": (
            "开启" if preferences["posture_reminders_enabled"] else "关闭"
        ),
    }


def _form_optional(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value) -> Optional[int]:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    number = int(text)
    if number <= 0:
        raise ValueError("预计时长必须大于 0")
    return number


def _int_or_zero(value) -> int:
    text = "" if value is None else str(value).strip()
    if not text:
        return 0
    number = int(text)
    if number < 0:
        raise ValueError("分钟数不能小于 0")
    return number


def _run_assistant_thread(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event,
) -> None:
    try:
        run_assistant(config_path, database_path, stop_event)
    except Exception:
        LOGGER.exception("assistant service stopped unexpectedly")


def _run_speech_thread(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event,
    prechecks: PrecheckCoordinator,
) -> None:
    try:
        run_speech_service(
            config_path,
            database_path,
            stop_event,
            prechecks,
        )
    except Exception:
        LOGGER.exception("speech service stopped unexpectedly")

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from funwithcam.assistant_service import run_assistant
from funwithcam.config import load_config
from funwithcam.storage.auth_repository import AuthRepository, csrf_matches
from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
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


class _LoginRequired(Exception):
    pass


LOGGER = logging.getLogger(__name__)


def create_app(
    config_path: Path,
    database_path: Path,
    assistant_enabled: bool = False,
) -> FastAPI:
    config = load_config(config_path)
    timezone = ZoneInfo(config.timezone)
    database = Database(database_path)
    database.initialize()
    auth = AuthRepository(database)
    family = FamilyRepository(database)
    workflow = WorkflowRepository(database)
    web_root = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(web_root / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event = None
        assistant_thread = None
        if assistant_enabled:
            stop_event = threading.Event()
            assistant_thread = threading.Thread(
                target=_run_assistant_thread,
                args=(config_path, database_path, stop_event),
                name="assistant-service",
                daemon=True,
            )
            assistant_thread.start()
        try:
            yield
        finally:
            if stop_event is not None:
                stop_event.set()
            if assistant_thread is not None:
                assistant_thread.join(timeout=5)

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
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "username": session.username,
                "csrf": session.csrf_token,
                "today": today,
                "weekday": WEEKDAY_NAMES[today.weekday()],
                "items": items,
                "today_courses": today_courses,
                "today_count": len(items) + len(today_courses),
                "weekly_courses": family.list_weekly_courses(),
                "preferences": family.get_daily_preferences(today),
                "settings": family.get_settings(),
                "workflow_state": WORKFLOW_LABELS[workflow_value],
                "message": message,
                "error": error,
            },
        )

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
            return _redirect("/", error=str(exc))
        return _redirect("/", message="今日安排已添加")

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
            return _redirect("/", error=str(exc))
        return _redirect("/", message="完成状态已更新")

    @app.post("/today/items/{item_id}/delete")
    async def delete_item(item_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        family.delete_agenda(item_id)
        return _redirect("/", message="安排已删除")

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
            return _redirect("/", error=str(exc))
        return _redirect("/", message="今日提醒设置已保存")

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
            return _redirect("/", error=str(exc))
        return _redirect("/", message="固定课程已添加")

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
        return _redirect("/", message="课程状态已更新")

    @app.post("/courses/{course_id}/delete")
    async def delete_course(course_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        family.delete_weekly_course(course_id)
        return _redirect("/", message="固定课程已删除")

    @app.post("/settings")
    async def save_settings(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            family.save_settings(
                {
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
            return _redirect("/", error=str(exc))
        return _redirect("/", message="默认提醒时间已保存")

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
        path = "{}?{}".format(path, urlencode(query))
    return RedirectResponse(path, status_code=303)


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

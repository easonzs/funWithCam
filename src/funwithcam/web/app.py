import asyncio
import logging
import subprocess
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from funwithcam.audio import AudioOutput
from funwithcam.assistant_service import (
    FormalIdentityMissingError,
    run_assistant,
)
from funwithcam.calibration import (
    CalibrationCaptureResult,
    CalibrationCoordinator,
    CameraCalibrationRunner,
)
from funwithcam.calibration_steps import (
    CALIBRATION_STEP_BY_KEY,
    CALIBRATION_STEPS,
)
from funwithcam.camera_speaker import inspect_camera_speaker
from funwithcam.config import load_config
from funwithcam.identity.repository import FaceProfileRepository
from funwithcam.precheck import (
    CameraPrecheckRunner,
    PrecheckCoordinator,
    PrecheckResult,
)
from funwithcam.reminders import (
    build_briefing_message,
    build_reminder_plan,
    build_second_reminder_message,
)
from funwithcam.speech import run_speech_service
from funwithcam.storage.activity_repository import ActivityRepository
from funwithcam.storage.auth_repository import AuthRepository, csrf_matches
from funwithcam.storage.calibration_repository import CalibrationRepository
from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
from funwithcam.storage.interaction_repository import (
    SpeechIntentRepository,
    TaskTypeRepository,
)
from funwithcam.storage.reminder_repository import ReminderRepository
from funwithcam.storage.speech_template_repository import (
    DEFAULT_TEMPLATE_VARIABLES,
    SpeechTemplateRepository,
)
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
ACTIVITY_CATEGORIES = (
    ("all", "全部"),
    ("reminder", "提醒"),
    ("recognition", "识别"),
    ("speech", "语音"),
    ("study", "学习"),
    ("anomaly", "异常"),
)
ACTIVITY_CATEGORY_LABELS = dict(ACTIVITY_CATEGORIES)
ACTIVITY_STATUS_LABELS = {
    "success": "已完成",
    "failed": "未通过",
    "ignored": "已忽略",
    "cancelled": "已取消",
    "expired": "已过期",
    "info": "进行中",
}
ACTIVITY_SOURCE_LABELS = {
    "assistant": "系统",
    "camera": "摄像头",
    "camera_audio": "摄像头麦克风",
    "camera_speaker": "摄像头扬声器",
    "macbook_speaker": "MacBook 扬声器",
    "microphone": "MacBook 麦克风",
    "macbook_microphone": "MacBook 麦克风",
    "scheduler": "定时任务",
    "speech": "语音口令",
    "web": "后台页面",
    "system": "系统",
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
    activities = ActivityRepository(database)
    auth = AuthRepository(database)
    family = FamilyRepository(database)
    speech_intents = SpeechIntentRepository(database)
    speech_templates = SpeechTemplateRepository(database)
    task_types = TaskTypeRepository(database)
    reminders = ReminderRepository(database)
    study_sessions = StudySessionRepository(database)
    calibrations = CalibrationRepository(database)
    calibrations.recover_interrupted(datetime.now(timezone))
    prechecks = PrecheckCoordinator(
        study_sessions,
        activities,
        timezone,
        precheck_runner or CameraPrecheckRunner(config, database_path),
    )
    calibration_coordinator = CalibrationCoordinator(
        calibrations,
        timezone,
        calibration_runner
        or CameraCalibrationRunner(config, database_path),
        assistant_pause_timeout=max(
            6.0,
            config.video.reconnect_seconds + 3.0,
        ),
    )
    workflow = WorkflowRepository(database)
    evidence_root = (
        database_path.resolve().parent / "observation_evidence"
    ).resolve()
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
        app.state.go2rtc_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
        )
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
            await app.state.go2rtc_client.aclose()

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

    go2rtc_prefix = config.web.go2rtc_prefix.rstrip("/")
    go2rtc_base_url = config.web.go2rtc_base_url.rstrip("/")

    _GO2RTC_HOP_BY_HOP = frozenset(
        {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "host",
        }
    )

    @app.api_route(
        go2rtc_prefix + "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def go2rtc_proxy(path: str, request: Request):
        """把 8000 的 /{prefix}/* 转发到 go2rtc 的 1984。

        播放器用相对路径找 api/ws，所以 iframe 里的 /{prefix}/stream.html
        的 api 请求自然落在 /{prefix}/api/ws，这里原样转发到 go2rtc。
        """
        if config.web.go2rtc_prefix != "/":
            _require_session(request, auth, timezone)
        target = "{}/{}".format(go2rtc_base_url, path)
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _GO2RTC_HOP_BY_HOP
        }
        body = await request.body()
        try:
            upstream = await app.state.go2rtc_client.request(
                request.method,
                target,
                headers=headers,
                content=body,
                params=request.query_params,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="go2rtc unavailable")
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers={
                name: value
                for name, value in upstream.headers.items()
                if name.lower()
                not in ("content-length", "transfer-encoding", "connection")
            },
        )

    @app.websocket(go2rtc_prefix + "/api/ws")
    async def go2rtc_ws_proxy(websocket: WebSocket):
        """桥接 8000 与 go2rtc 的 WebSocket 信令（api/ws?src=...）。"""
        await websocket.accept()
        import websockets as ws_lib

        # websockets 库需要 ws:// 协议前缀，base_url 是 http(s)://
        upstream_scheme = (
            "wss" if go2rtc_base_url.startswith("https://") else "ws"
        )
        upstream_url = upstream_scheme + "://" + go2rtc_base_url.split(
            "://", 1
        )[1]
        target = upstream_url + websocket.url.path.removeprefix(
            go2rtc_prefix
        ) + (
            "?" + websocket.url.query if websocket.url.query else ""
        )
        try:
            async with ws_lib.connect(
                target,
                subprotocols=(
                    [
                        item.strip()
                        for item in websocket.headers.get(
                            "sec-websocket-protocol", ""
                        ).split(",")
                        if item.strip()
                    ]
                    or None
                ),
            ) as upstream:
                async def _bridge_ws():
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)

                bridge = asyncio.create_task(_bridge_ws())
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        data = message.get("text")
                        if data is not None:
                            await upstream.send(data)
                        else:
                            await upstream.send(message.get("bytes"))
                finally:
                    bridge.cancel()
        except Exception as exc:
            LOGGER.warning(
                "go2rtc ws proxy error: %s: %s", type(exc).__name__, exc
            )
            try:
                await websocket.close()
            except RuntimeError:
                pass

    @app.get("/camera", response_class=HTMLResponse)
    def camera_page(request: Request, message: Optional[str] = None, error: Optional[str] = None):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "camera.html",
            session,
            "camera",
            go2rtc_url=go2rtc_prefix
            + "/stream.html?src="
            + quote(config.audio.camera_stream, safe=""),
            message=message,
            error=error,
        )

    @app.get("/camera/motor")
    async def camera_motor_read(request: Request):
        _, session = _require_session(request, auth, timezone)
        try:
            response = await app.state.go2rtc_client.get(
                "{}/api/xiaomi/motor?src={}&read=1".format(
                    go2rtc_base_url,
                    quote(config.audio.camera_stream, safe=""),
                )
            )
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise HTTPException(status_code=502, detail="go2rtc motor API failed")
        return response.json()

    @app.post("/camera/motor")
    async def camera_motor_move(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        operation = str(form.get("operation", ""))
        if operation not in {"1", "2", "3", "4"}:
            raise HTTPException(status_code=400, detail="invalid operation")
        try:
            response = await app.state.go2rtc_client.post(
                "{}/api/xiaomi/motor?src={}&operation={}".format(
                    go2rtc_base_url,
                    quote(config.audio.camera_stream, safe=""),
                    operation,
                )
            )
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise HTTPException(status_code=502, detail="go2rtc motor API failed")
        return response.json()

    @app.exception_handler(_LoginRequired)
    async def login_required_handler(request: Request, exc: _LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/health/live")
    def health_live() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready() -> dict:
        with database.connection() as connection:
            connection.execute("SELECT 1").fetchone()
        templates = FaceProfileRepository(database).list_active("formal")
        missing_identities = [
            role
            for role in ("child", "parent")
            if not any(template.role == role for template in templates)
        ]
        missing_models = [
            str(path)
            for path in (
                config.identity.detector_model,
                config.identity.recognizer_model,
            )
            if not path.is_file()
        ]
        if config.speech.enabled and not config.speech.model_path.is_dir():
            missing_models.append(str(config.speech.model_path))
        return {
            "status": "ready" if not missing_models else "configuration_required",
            "setup_required": bool(missing_identities or missing_models),
            "missing_identities": missing_identities,
            "missing_models": missing_models,
        }

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
        today_events = activities.list_for_date(today.isoformat())
        latest_session = next(
            (
                record
                for record in study_sessions.list_recent()
                if record["session_date"] == today.isoformat()
            ),
            None,
        )
        completed_items = sum(item["status"] == "done" for item in items)
        video_checkins = sum(
            item.get("completion_evidence") == "video" for item in items
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
            dashboard_metrics={
                "study_minutes": (
                    int(round(latest_session["study_minutes"]))
                    if latest_session
                    else "—"
                ),
                "anomalies": sum(
                    event["category"] == "anomaly" for event in today_events
                ),
                "completed_items": completed_items,
                "total_items": len(items) + len(today_courses),
                "video_checkins": video_checkins,
            },
            camera_status=(
                "视频流已配置"
                if config.video.stream_url.strip()
                else "视频流未配置"
            ),
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
            task_types=task_types.list_all(enabled_only=True),
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
        current_iso_week = datetime.now(timezone).date().isocalendar()
        week_records = [
            record
            for record in records
            if (
                date.fromisoformat(record["session_date"]).isocalendar().year,
                date.fromisoformat(record["session_date"]).isocalendar().week,
            )
            == (current_iso_week.year, current_iso_week.week)
        ]
        completed_week_records = [
            record for record in week_records if record["state"] == "COMPLETED"
        ]
        average_minutes = (
            int(
                round(
                    sum(record["study_minutes"] for record in completed_week_records)
                    / len(completed_week_records)
                )
            )
            if completed_week_records
            else None
        )
        return render_page(
            request,
            "records.html",
            session,
            "records",
            records=records,
            record_metrics={
                "week_sessions": len(completed_week_records),
                "average_minutes": average_minutes,
                "average_away": "—",
                "posture_trend": "待积累",
            },
        )

    @app.get("/history", response_class=HTMLResponse)
    def history_page(
        request: Request,
        selected_date: Optional[str] = None,
        category: str = "all",
    ):
        _, session = _require_session(request, auth, timezone)
        today = datetime.now(timezone).date()
        try:
            history_date = (
                today
                if selected_date is None
                else date.fromisoformat(selected_date)
            )
        except ValueError:
            history_date = today
        if category not in ACTIVITY_CATEGORY_LABELS:
            category = "all"
        events = activities.list_for_date(
            history_date.isoformat(),
            None if category == "all" else category,
        )
        for event in events:
            event["category_label"] = ACTIVITY_CATEGORY_LABELS.get(
                event["category"],
                event["category"],
            )
            event["status_label"] = ACTIVITY_STATUS_LABELS.get(
                event["status"],
                event["status"],
            )
            event["source_label"] = ACTIVITY_SOURCE_LABELS.get(
                event["source"],
                event["source"],
            )
            event["details"] = _activity_details(event["payload"])
            event["evidence"] = _activity_evidence(event["payload"])
        return render_page(
            request,
            "history.html",
            session,
            "records",
            selected_date=history_date,
            selected_category=category,
            categories=ACTIVITY_CATEGORIES,
            events=events,
        )

    @app.get("/history/evidence/{evidence_path:path}")
    def history_evidence(request: Request, evidence_path: str):
        _require_session(request, auth, timezone)
        target = (evidence_root / evidence_path).resolve()
        try:
            target.relative_to(evidence_root)
        except ValueError as exc:
            raise HTTPException(status_code=404) from exc
        if not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(
            target,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/config", response_class=HTMLResponse)
    def config_home(request: Request):
        _require_session(request, auth, timezone)
        return RedirectResponse("/config/studying", status_code=303)

    @app.get("/config/{section}", response_class=HTMLResponse)
    def config_section_page(
        request: Request,
        section: str,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        allowed_sections = {
            "arrival",
            "start",
            "studying",
            "away",
            "finish",
            "checkin",
            "speech",
            "speech-templates",
            "interaction",
            "device",
            "profile",
            "settings",
            "privacy",
        }
        if section not in allowed_sections:
            raise HTTPException(status_code=404, detail="configuration section not found")

        today = datetime.now(timezone).date()
        settings = family.get_settings()
        context = {
            "config_section": section,
            "section": section,
            "today": today,
            "message": message,
            "error": error,
        }

        if section == "settings":
            return render_page(
                request,
                "settings.html",
                session,
                "config",
                settings=settings,
                calibration_version=calibrations.get_latest_version(),
                speech_enabled=config.speech.enabled,
                camera_configured=bool(config.video.stream_url.strip()),
                **context,
            )
        if section == "speech":
            input_source = (
                "摄像头麦克风"
                if config.speech.input_source == "camera"
                else "MacBook 麦克风"
            )
            return render_page(
                request,
                "speech.html",
                session,
                "config",
                speech_enabled=config.speech.enabled,
                speech_commands=speech_intents.list_all(),
                input_source=input_source,
                **context,
            )
        if section == "speech-templates":
            now = datetime.now(timezone)
            plan = build_reminder_plan(
                today,
                timezone,
                family.get_daily_preferences(today),
                settings,
            )
            items = family.list_agenda(today)
            courses = family.list_courses_for_date(today)
            template_items = []
            for item in speech_templates.list_all():
                if item["event_key"] == "first_briefing":
                    preview = build_briefing_message(
                        items,
                        courses,
                        plan,
                        now,
                        settings["child_name"],
                        item["template_text"],
                    )
                elif item["event_key"] == "second_reminder":
                    preview = build_second_reminder_message(
                        items,
                        plan.second_reminder_at or now,
                        settings["child_name"],
                        item["template_text"],
                    )
                else:
                    preview = _render_template_preview(
                        item["template_text"],
                        settings["child_name"],
                    )
                item["preview"] = preview
                item["runtime_supported"] = item["event_key"] in {
                    "first_briefing",
                    "second_reminder",
                    "posture_abnormal",
                    "light_abnormal",
                    "seat_leave",
                }
                template_items.append(item)
            return render_page(
                request,
                "speech_templates.html",
                session,
                "config",
                templates=template_items,
                variables=DEFAULT_TEMPLATE_VARIABLES,
                **context,
            )
        if section == "interaction":
            return render_page(
                request,
                "interaction.html",
                session,
                "config",
                settings=settings,
                **context,
            )
        if section == "finish":
            return render_page(
                request,
                "finish_checks.html",
                session,
                "config",
                task_types=task_types.list_all(),
                **context,
            )
        if section == "studying":
            return render_page(
                request,
                "reminders.html",
                session,
                "config",
                settings=settings,
                **context,
            )
        if section == "profile":
            draft = (
                calibrations.get_active()
                or calibrations.get_latest_completed_draft()
            )
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="孩子与基准",
                description="管理孩子称呼、身份资料和一次录入、长期使用的动作与光线基准。",
                scope="这里只维护长期参考，不改变今天的日程和提醒时间。",
                status="已配置" if calibrations.get_latest_version() else "需要录入",
                config_rows=[
                    ("孩子称呼", "用于首次简报和异常提醒", settings["child_name"], "已配置"),
                    (
                        "正式人脸档案",
                        "用于首次出现和开始学习时的身份确认",
                        "已部署" if FaceProfileRepository(database).list_active("formal") else "未确认",
                        "本地",
                    ),
                    (
                        "动作与光线基准",
                        "用于学习中观察和开始前检查",
                        "版本 {}".format(calibrations.get_latest_version()["id"])
                        if calibrations.get_latest_version()
                        else "尚未录入",
                        "可重录",
                    ),
                ],
                primary_link="/calibration",
                primary_label="查看或重新录入基准",
                **context,
            )
        if section == "device":
            input_source = (
                "摄像头麦克风"
                if config.speech.input_source == "camera"
                else "MacBook 麦克风"
            )
            output_source = (
                "摄像头扬声器"
                if config.audio.output_source == "camera"
                else "MacBook 扬声器"
            )
            device_rows = [
                ("视频流", "go2rtc 视频地址", "已配置" if config.video.stream_url.strip() else "未配置", "Camera"),
                ("语音输入", "当前收音来源", input_source, "已启用" if config.speech.enabled else "未启用"),
                ("语音输出", "当前播报出口", output_source, "已启用"),
                ("视觉观察", "姿态、亮度和离座判断", settings["observation_interval_seconds"] + " 秒采样", "已配置"),
            ]
            speaker_status = None
            if config.audio.output_source == "camera":
                speaker_status = inspect_camera_speaker(
                    config.audio.camera_api_url,
                    config.audio.camera_stream,
                )
                device_rows.insert(
                    3,
                    (
                        "摄像头反向音频",
                        "go2rtc 向摄像头发送声音",
                        speaker_status.detail,
                        "可用" if speaker_status.available else "待检查",
                    ),
                )
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="摄像头与声音",
                description="集中查看视频流、语音输入、语音输出和视觉采样的真实配置状态。",
                scope="这里只解释设备来源和运行条件；异常详情请查看记录。",
                status="Web 服务正常",
                config_rows=device_rows,
                primary_link="/health",
                primary_label="查看服务健康状态",
                audio_test_enabled=True,
                audio_test_text=config.audio.camera_test_text,
                audio_output_source=output_source,
                **context,
            )
        if section == "privacy":
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="数据与隐私",
                description="查看本地服务保存哪些记录，以及基准图片、执行历史和媒体文件如何使用。",
                scope="FunWithCam 默认在这台 MacBook 上处理数据，不读取 Mac 系统日历，也不自动上传云端。",
                status="本地处理",
                config_rows=[
                    ("学习与提醒记录", "事件、时间、来源和结果", "保存在 SQLite", "本地"),
                    ("基准图片", "家长主动采集并确认的样本", "保留历史版本", "可手工清理"),
                    ("执行历史", "提醒、识别、语音输入和系统输出", "按日期回溯", "可查看"),
                    ("连续视频与音频", "普通学习过程的原始媒体", "不默认保存", "关闭"),
                ],
                primary_link="/history",
                primary_label="查看执行历史",
                **context,
            )
        if section == "arrival":
            preferences = family.get_daily_preferences(today)
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="到家与日程提醒",
                description="首次确认到孩子后，系统读取 FunWithCam 日程并生成当天真正会播放的简报。",
                scope="这里只说明触发条件和内容来源；课程、作业和打卡内容统一在“日程”中维护。",
                status="首次提醒每天最多一次",
                config_rows=[
                    ("上学日首次简报", settings["first_briefing_weekday_time"] + " 后", "连续确认孩子", "已启用"),
                    ("休息日首次简报", settings["first_briefing_rest_day_time"] + " 后", "连续确认孩子", "已启用"),
                    ("第二次提醒", settings["second_reminder_weekday_time"] + " / " + settings["second_reminder_rest_day_time"], "仍未开始学习", "每天最多一次"),
                    ("今天日期类型", preferences["day_type"], "可在今日单独覆盖", "已生效"),
                ],
                source_rows=[
                    ("孩子称呼", "家庭基础设置", settings["child_name"]),
                    ("固定课程", "今日启用的每周课程", "{} 项".format(len(family.list_courses_for_date(today)))),
                    ("学校作业与打卡", "今天的日程安排", "{} 项".format(len(family.list_agenda(today)))),
                    ("下一次提醒时间", "提醒计划计算结果", _reminder_summary(today.weekday(), preferences, settings)["second_reminder"]),
                ],
                links=[
                    ("/schedule", "去日程维护安排"),
                    ("/config/speech-templates", "调整真实播报话术"),
                    ("/config/settings", "调整默认时间"),
                ],
                **context,
            )

        if section == "start":
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="开始学习",
                description="孩子准备好以后，通过语音或网页按钮进入开始前检查，检查通过后才建立正式学习记录。",
                scope="这里只说明开始条件和检查链路；具体口令在“语音口令”中维护。",
                status="开始前检查已接入",
                config_rows=[
                    ("开始入口", "语音口令或今日页面按钮", "开始学习 / 开始作业", "可用"),
                    ("身份确认", "正式人脸档案，多帧连续确认", "孩子本人", "已配置"),
                    ("环境检查", "视频流、书桌区域和光线", "连续通过后开始", "已接入"),
                    ("结果记录", "通过或失败都写入执行历史", "来源可回溯", "已接入"),
                ],
                links=[
                    ("/config/speech", "配置开始口令"),
                    ("/", "回到今日测试开始"),
                    ("/history", "查看开始检查记录"),
                ],
                **context,
            )
        if section == "away":
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="离座与恢复",
                description="离座阈值决定学习状态什么时候暂停；回来后的继续或结束询问使用独立的交互规则。",
                scope="离座阈值和重复询问是两个维度：前者来自视觉观察，后者只在系统已经提问后生效。",
                status="持续视觉监测与自动暂停已接入",
                config_rows=[
                    ("自动暂停", "连续离座达到阈值", settings["away_auto_pause_minutes"] + " 分钟", "可配置"),
                    ("长时间中断", "连续离座达到阈值", settings["away_interrupted_minutes"] + " 分钟", "可配置"),
                    ("回来后第一次询问", "系统提问后等待", settings["dialogue_first_retry_seconds"] + " 秒", "交互设置"),
                    ("后续重复询问", "没有有效回答时", settings["dialogue_next_retry_seconds"] + " 秒 / 最多 " + settings["dialogue_max_prompts"] + " 次", "交互设置"),
                ],
                links=[
                    ("/config/studying", "配置视觉异常规则"),
                    ("/config/interaction", "配置提问与重复询问"),
                    ("/guide#away", "查看完整离座流程"),
                ],
                **context,
            )
        if section == "checkin":
            return render_page(
                request,
                "config_section.html",
                session,
                "config",
                title="打卡录制",
                description="视频打卡是结束检查中的一种完成证据，准备、录制、保存和失败都会留下执行记录。",
                scope="这里展示打卡如何运行；任务类型和最长录制时长统一在“结束检查”中维护。",
                status="本地保存 · 由日程任务决定是否询问",
                config_rows=[
                    ("准备", "孩子说出打卡意图后", "系统提示准备材料", "已接入"),
                    ("开始", "孩子明确回答开始录制", "进入视频录制", "已接入"),
                    ("结束", "结束口令或达到时长上限", "保存视频证据", "已接入"),
                    ("失败", "视频流或录制异常", "播报失败原因并记录", "已接入"),
                ],
                task_types=task_types.list_all(),
                links=[
                    ("/config/speech", "配置结束打卡口令"),
                    ("/config/finish", "配置任务与最长时长"),
                    ("/guide#checkin", "查看打卡使用说明"),
                    ("/history", "查看打卡执行记录"),
                ],
                **context,
            )

        raise HTTPException(status_code=404, detail="configuration section not found")

    @app.post("/config/device/audio-test")
    async def test_audio_output(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        audio = AudioOutput.from_config(config.audio)
        now = datetime.now(timezone)
        try:
            await asyncio.to_thread(
                audio.speak,
                config.audio.camera_test_text,
            )
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            activities.record(
                now,
                "speech",
                "speech_output_test",
                audio.source,
                "failed",
                "语音输出测试失败",
                str(exc),
            )
            return _redirect("/config/device", error=str(exc))
        activities.record(
            datetime.now(timezone),
            "speech",
            "speech_output_test",
            audio.source,
            "success",
            "语音输出测试完成",
            config.audio.camera_test_text,
        )
        return _redirect(
            "/config/device",
            message="测试语音已经发送到{}".format(
                "摄像头扬声器"
                if config.audio.output_source == "camera"
                else "MacBook 扬声器"
            ),
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

    @app.get("/speech", response_class=HTMLResponse)
    def speech_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        input_source = (
            "摄像头麦克风"
            if config.speech.input_source == "camera"
            else "MacBook 麦克风"
        )
        return render_page(
            request,
            "speech.html",
            session,
            "speech",
            speech_enabled=config.speech.enabled,
            speech_commands=speech_intents.list_all(),
            input_source=input_source,
            message=message,
            error=error,
        )

    @app.post("/speech/{command}")
    async def save_speech_intent(command: str, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            speech_intents.save(
                command,
                _split_lines(form.get("custom_phrases")),
                _split_keyword_groups(form.get("keyword_groups")),
                str(form.get("confirmation_mode", "")),
                str(form.get("confirmation_prompt", "")),
                _split_lines(form.get("confirm_phrases")),
                _split_lines(form.get("cancel_phrases")),
                form.get("enabled") == "on",
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/speech", error=str(exc))
        return _redirect("/speech", message="语音口令规则已保存并立即生效")

    @app.get("/speech-templates", response_class=HTMLResponse)
    def speech_templates_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        today = datetime.now(timezone).date()
        now = datetime.now(timezone)
        settings = family.get_settings()
        plan = build_reminder_plan(
            today,
            timezone,
            family.get_daily_preferences(today),
            settings,
        )
        items = family.list_agenda(today)
        courses = family.list_courses_for_date(today)
        templates = []
        for item in speech_templates.list_all():
            if item["event_key"] == "first_briefing":
                preview = build_briefing_message(
                    items,
                    courses,
                    plan,
                    now,
                    settings["child_name"],
                    item["template_text"],
                )
            elif item["event_key"] == "second_reminder":
                preview = build_second_reminder_message(
                    items,
                    plan.second_reminder_at or now,
                    settings["child_name"],
                    item["template_text"],
                )
            else:
                preview = _render_template_preview(
                    item["template_text"],
                    settings["child_name"],
                )
            item["preview"] = preview
            item["runtime_supported"] = item["event_key"] in {
                "first_briefing",
                "second_reminder",
                "posture_abnormal",
                "light_abnormal",
                "seat_leave",
            }
            templates.append(item)
        return render_page(
            request,
            "speech_templates.html",
            session,
            "speech_templates",
            templates=templates,
            variables=DEFAULT_TEMPLATE_VARIABLES,
            today=today,
            message=message,
            error=error,
        )

    @app.post("/speech-templates/{event_key}")
    async def save_speech_template(event_key: str, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            speech_templates.save(
                event_key,
                str(form.get("template_text", "")),
                form.get("enabled") == "on",
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/speech-templates", error=str(exc))
        return _redirect(
            "/speech-templates",
            message="系统话术模板已保存并立即生效",
        )

    @app.get("/interaction", response_class=HTMLResponse)
    def interaction_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "interaction.html",
            session,
            "interaction",
            settings=family.get_settings(),
            message=message,
            error=error,
        )

    @app.post("/interaction/settings")
    async def save_interaction_settings(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        keys = (
            "dialogue_first_retry_seconds",
            "dialogue_next_retry_seconds",
            "dialogue_max_prompts",
        )
        try:
            family.save_settings(
                {key: str(form.get(key, "")).strip() for key in keys},
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/interaction", error=str(exc))
        return _redirect("/interaction", message="交互确认设置已保存")

    @app.get("/finish-checks", response_class=HTMLResponse)
    def finish_checks_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "finish_checks.html",
            session,
            "finish_checks",
            task_types=task_types.list_all(),
            message=message,
            error=error,
        )

    @app.post("/task-types")
    async def save_task_type(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            task_types.save(
                str(form.get("task_key", "")),
                str(form.get("label", "")),
                str(form.get("category", "")),
                str(form.get("completion_mode", "")),
                str(form.get("finish_prompt", "")),
                form.get("include_in_finish_check") == "on",
                _optional_int(form.get("max_recording_seconds")),
                form.get("enabled") == "on",
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/finish-checks", error=str(exc))
        return _redirect("/finish-checks", message="结束检查模板已保存")

    @app.get("/guide", response_class=HTMLResponse)
    def guide_page(request: Request):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "guide.html",
            session,
            "guide",
        )

    @app.get("/reminders", response_class=HTMLResponse)
    def reminders_page(
        request: Request,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ):
        _, session = _require_session(request, auth, timezone)
        return render_page(
            request,
            "reminders.html",
            session,
            "reminders",
            settings=family.get_settings(),
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
                _form_optional(form.get("task_type_key")),
                (
                    form.get("include_in_finish_check") == "on"
                    if form.get("finish_check_present") == "1"
                    else None
                ),
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
            status = str(form.get("status", ""))
            if status == "done":
                family.set_completion_evidence(
                    item_id,
                    "parent_confirmed",
                    datetime.now(timezone),
                )
            elif status == "pending":
                family.set_completion_evidence(
                    item_id,
                    "none",
                    datetime.now(timezone),
                )
            else:
                raise ValueError("invalid agenda status")
        except ValueError as exc:
            return _redirect("/schedule", error=str(exc))
        return _redirect("/schedule", message="完成状态已更新")

    @app.post("/today/items/{item_id}/finish-check")
    async def set_item_finish_check(item_id: int, request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        try:
            enabled = str(form.get("enabled", ""))
            if enabled not in ("0", "1"):
                raise ValueError("invalid finish check setting")
            family.set_finish_check_enabled(
                item_id,
                enabled == "1",
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/schedule", error=str(exc))
        return _redirect("/schedule", message="结束检查设置已更新")

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
        now = datetime.now(timezone)
        try:
            reminders.reset_day(
                now.date().isoformat(),
                now,
            )
        except ValueError as exc:
            return _redirect("/settings", error=str(exc))
        activities.record(
            now,
            "reminder",
            "first_briefing_reset",
            "web",
            "success",
            "首次提醒状态已重置",
            "系统会重新等待孩子出现并播放今日安排。",
        )
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
                study_sessions.pause(today, now, source="web")
                message = "学习记录已暂停"
            elif action == "resume":
                prechecks.request("resume", today, "web", now)
                message = "正在进行开始前检查，请在书桌前保持 5 秒"
            elif action == "finish":
                study_sessions.finish(today, now, source="web")
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

    @app.post("/reminders")
    async def save_reminder_settings(request: Request):
        token, _ = _require_session(request, auth, timezone)
        form = await request.form()
        _require_csrf(token, str(form.get("csrf", "")))
        reminder_keys = (
            "observation_interval_seconds",
            "posture_trigger_count",
            "posture_trigger_window_minutes",
            "posture_reminder_cooldown_minutes",
            "light_trigger_count",
            "light_trigger_window_minutes",
            "light_reminder_cooldown_minutes",
            "seat_leave_trigger_count",
            "seat_leave_trigger_window_minutes",
            "seat_leave_reminder_cooldown_minutes",
            "away_auto_pause_minutes",
            "away_interrupted_minutes",
        )
        try:
            family.save_settings(
                {
                    key: str(form.get(key, "")).strip()
                    for key in reminder_keys
                },
                datetime.now(timezone),
            )
        except ValueError as exc:
            return _redirect("/reminders", error=str(exc))
        return _redirect("/reminders", message="提醒规则已保存")

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


def _render_template_preview(template: str, child_name: str) -> str:
    return (
        template
        .replace("{孩子称呼}", child_name)
        .replace("{今天安排简报}", "今天有数学练习册。")
        .replace("{下一次提醒时间}", "18点10分")
        .replace("{当前时间}", "18点10分")
        .replace("{待完成任务列表}", "数学练习册")
    )


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


def _split_lines(value) -> list[str]:
    if value is None:
        return []
    text = str(value).replace("，", "\n").replace(",", "\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split_keyword_groups(value) -> list[list[str]]:
    if value is None:
        return []
    groups = []
    for line in str(value).splitlines():
        words = [
            word.strip()
            for word in line.replace("，", ",").split(",")
            if word.strip()
        ]
        if words:
            groups.append(words)
    return groups


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


def _activity_details(payload: dict) -> list[dict]:
    labels = {
        "action": "检查动作",
        "reason": "结果原因",
        "command": "关联口令",
        "matching_frames": "确认帧数",
        "scheduled_at": "计划时间",
        "valid_frame_ratio": "有效画面",
        "average_brightness": "平均亮度",
        "person_ratio": "人物稳定度",
        "single_face_ratio": "单人画面",
        "child_ratio": "孩子身份",
    }
    values = {
        "start": "开始学习",
        "resume": "继续学习",
        "confirmed": "身份确认通过",
        "passed": "检查通过",
        "video_unavailable": "视频不可用",
        "too_dark": "光线偏暗",
        "outside_study_area": "不在书桌区域",
        "person_not_detected": "未稳定检测到人物",
        "multiple_people": "检测到多人",
        "face_not_visible": "人脸不可见",
        "child_not_verified": "未确认孩子身份",
        "cancelled": "检查已取消",
        "system_error": "系统错误",
    }
    ratio_keys = {
        "valid_frame_ratio",
        "person_ratio",
        "single_face_ratio",
        "child_ratio",
    }
    details = []
    for key, value in payload.items():
        if value is None or key in {"evidence", "measurements"}:
            continue
        if key in ratio_keys:
            display_value = "{:.0f}%".format(float(value) * 100)
        elif key == "average_brightness":
            display_value = "{:.1f}".format(float(value))
        elif key == "scheduled_at":
            display_value = str(value).replace("T", " ")[:16]
        else:
            display_value = values.get(str(value), str(value))
        details.append(
            {
                "label": labels.get(key, key),
                "value": display_value,
            }
        )
    return details


def _activity_evidence(payload: dict) -> list[dict]:
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, list):
        return []
    evidence = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        image_path = str(item.get("image_path", "")).strip()
        captured_at = str(item.get("captured_at", "")).strip()
        if not image_path or not captured_at:
            continue
        measurements = [
            formatted
            for measurement in item.get("measurements", [])
            if isinstance(measurement, dict)
            for formatted in [_format_evidence_measurement(measurement)]
            if formatted is not None
        ]
        evidence.append(
            {
                "captured_at": captured_at,
                "captured_time": captured_at[11:19],
                "image_url": "/history/evidence/{}".format(
                    quote(image_path, safe="/")
                ),
                "measurements": measurements,
            }
        )
    return evidence


def _format_evidence_measurement(measurement: dict) -> Optional[dict]:
    labels = {
        "head_to_shoulder_ratio": "头部高度比例",
        "shoulder_slope": "肩线倾斜",
        "torso_offset_ratio": "躯干偏移",
        "brightness": "书桌区域亮度",
        "pose_detected": "姿态检测",
    }
    key = str(measurement.get("key", "")).strip()
    if not key or "actual" not in measurement:
        return None
    actual = _format_measurement_value(measurement["actual"])
    if "minimum" in measurement and "maximum" in measurement:
        threshold = "{}–{}".format(
            _format_measurement_value(measurement["minimum"]),
            _format_measurement_value(measurement["maximum"]),
        )
    elif "minimum" in measurement:
        threshold = "≥ {}".format(
            _format_measurement_value(measurement["minimum"])
        )
    elif "maximum" in measurement:
        threshold = "≤ {}".format(
            _format_measurement_value(measurement["maximum"])
        )
    elif "expected" in measurement:
        threshold = _format_measurement_value(measurement["expected"])
    else:
        threshold = "—"
    return {
        "label": labels.get(key, key),
        "actual": actual,
        "threshold": threshold,
    }


def _format_measurement_value(value) -> str:
    if isinstance(value, bool):
        return "已检测到" if value else "未检测到"
    if isinstance(value, (int, float)):
        return "{:.3f}".format(float(value))
    return str(value)


def _run_assistant_thread(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            run_assistant(config_path, database_path, stop_event)
            return
        except FormalIdentityMissingError as exc:
            LOGGER.warning(
                "assistant is waiting for formal identities: %s",
                exc,
            )
            stop_event.wait(10)
        except Exception:
            LOGGER.exception("assistant service stopped unexpectedly")
            return


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

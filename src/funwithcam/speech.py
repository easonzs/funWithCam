import logging
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .checkin import CheckinRecorder
from .config import AppConfig, SpeechConfig, load_config
from .precheck import PrecheckCoordinator, PrecheckResult
from .storage.activity_repository import ActivityRepository
from .storage.database import Database
from .storage.family_repository import FamilyRepository
from .storage.interaction_repository import (
    CheckinRepository,
    DEFAULT_SPEECH_PHRASES,
    DialogueRepository,
    SpeechIntentRepository,
    complete_confirmation_prompt,
)
from .storage.study_session_repository import StudySessionRepository


LOGGER = logging.getLogger(__name__)
CAMERA_AUDIO_SAMPLE_RATE = 16000

START = "start"
PAUSE = "pause"
RESUME = "resume"
FINISH = "finish"
QUERY_HOMEWORK = "query_homework"
QUERY_COURSES = "query_courses"
START_CHECKIN = "start_checkin"
FINISH_CHECKIN = "finish_checkin"

SPEECH_COMMAND_GUIDE = (
    {
        "command": START,
        "label": "开始学习",
        "phrases": DEFAULT_SPEECH_PHRASES[START],
        "description": "完成开始前检查并建立学习记录；今日简报完成后可用。",
    },
    {
        "command": PAUSE,
        "label": "暂停学习",
        "phrases": DEFAULT_SPEECH_PHRASES[PAUSE],
        "description": "暂停当前学习记录；仅在正在学习时可用。",
    },
    {
        "command": RESUME,
        "label": "继续学习",
        "phrases": DEFAULT_SPEECH_PHRASES[RESUME],
        "description": "重新检查书桌状态后继续记录；仅在暂停或中断时可用。",
    },
    {
        "command": FINISH,
        "label": "结束学习",
        "phrases": DEFAULT_SPEECH_PHRASES[FINISH],
        "description": "结束当前学习记录；仅在学习、离座或暂停状态可用。",
    },
    {
        "command": QUERY_HOMEWORK,
        "label": "查询今日作业",
        "phrases": DEFAULT_SPEECH_PHRASES[QUERY_HOMEWORK],
        "description": "播报日程中尚未完成的学校作业。",
    },
    {
        "command": QUERY_COURSES,
        "label": "查询今日课程",
        "phrases": DEFAULT_SPEECH_PHRASES[QUERY_COURSES],
        "description": "播报今天录入的课程和每周固定课程。",
    },
    {
        "command": START_CHECKIN,
        "label": "开始打卡",
        "phrases": DEFAULT_SPEECH_PHRASES[START_CHECKIN],
        "description": "选择今天的视频打卡任务并进入准备流程。",
    },
    {
        "command": FINISH_CHECKIN,
        "label": "结束打卡",
        "phrases": DEFAULT_SPEECH_PHRASES[FINISH_CHECKIN],
        "description": "仅在视频打卡录制中停止录制并保存视频。",
    },
)


@dataclass(frozen=True)
class SpeechMatch:
    command: Optional[str]
    quality: str
    intent: Optional[dict] = None

    @property
    def requires_confirmation(self) -> bool:
        if self.intent is None:
            return False
        mode = self.intent["confirmation_mode"]
        return mode == "always" or (
            mode == "ambiguous" and self.quality != "exact"
        )


def normalize_speech_text(text: str) -> str:
    return "".join(
        character
        for character in text.lower()
        if character not in " ，。！？,.!?、\t\r\n"
    )


def match_speech_intent(text: str, intents: list[dict]) -> SpeechMatch:
    normalized = normalize_speech_text(text)
    exact = [
        intent
        for intent in intents
        if any(
            normalize_speech_text(phrase) in normalized
            for phrase in intent["phrases"]
        )
    ]
    if len(exact) == 1:
        return SpeechMatch(exact[0]["command"], "exact", exact[0])
    if len(exact) > 1:
        return SpeechMatch(None, "conflict")

    composite = []
    for intent in intents:
        groups = intent.get("keyword_groups") or []
        if groups and all(
            any(normalize_speech_text(keyword) in normalized for keyword in group)
            for group in groups
        ):
            composite.append(intent)
    if len(composite) == 1:
        return SpeechMatch(composite[0]["command"], "composite", composite[0])
    if len(composite) > 1:
        return SpeechMatch(None, "conflict")
    return SpeechMatch(None, "none")


def parse_speech_command(text: str) -> Optional[str]:
    intents = [
        {
            **item,
            "keyword_groups": [],
            "confirmation_mode": "never",
        }
        for item in SPEECH_COMMAND_GUIDE
    ]
    return match_speech_intent(text, intents).command


class SpeechCommandHandler:
    def __init__(
        self,
        config: AppConfig,
        database_path: Path,
        prechecks: PrecheckCoordinator,
    ):
        database = Database(database_path)
        database.initialize()
        self._timezone = ZoneInfo(config.timezone)
        self._activities = ActivityRepository(database)
        self._family = FamilyRepository(database)
        self._sessions = StudySessionRepository(database)
        self._intents = SpeechIntentRepository(database)
        self._dialogues = DialogueRepository(database)
        self._checkins = CheckinRepository(database)
        self._audio = AudioOutput.from_config(config.audio)
        self._prechecks = prechecks
        self._recorder = CheckinRecorder(
            config.video.stream_url,
            database_path.resolve().parent / "checkins",
            self._checkins,
        )
        self._input_source = (
            "camera_audio"
            if config.speech.input_source == "camera"
            else "macbook_microphone"
        )

    def handle(self, text: str) -> bool:
        now = datetime.now(self._timezone)
        session_date = now.date().isoformat()
        dialogue = self._dialogues.get_active(session_date)
        if dialogue is not None:
            return self._handle_dialogue(dialogue, text, now)

        match = match_speech_intent(text, self._intents.list_enabled())
        if match.command is None:
            self._activities.record(
                now,
                "speech",
                "speech_input",
                self._input_source,
                "ignored",
                "语音未匹配到口令",
                text,
                {"match_quality": match.quality},
            )
            return False

        command = match.command
        self._activities.record(
            now,
            "speech",
            "speech_input",
            self._input_source,
            "success",
            "识别到语音口令",
            text,
            {"command": command, "match_quality": match.quality},
        )
        if match.requires_confirmation:
            settings = self._family.get_settings()
            intent = match.intent or {}
            prompt = complete_confirmation_prompt(
                intent["confirmation_prompt"],
                intent["confirm_phrases"],
                intent["cancel_phrases"],
            )
            self._dialogues.start(
                session_date,
                "command_confirmation",
                "waiting_confirmation",
                {
                    "command": command,
                    "original_text": text,
                    "confirm_phrases": intent["confirm_phrases"],
                    "cancel_phrases": intent["cancel_phrases"],
                },
                prompt,
                intent["confirm_phrases"] + intent["cancel_phrases"],
                now,
                int(settings["dialogue_first_retry_seconds"]),
            )
            self._speak_and_record(prompt, command, "success")
            return True

        return self._execute(command, text, now)

    def _execute(self, command: str, original_text: str, now: datetime) -> bool:
        session_date = now.date().isoformat()
        child_name = self._family.get_settings()["child_name"]
        try:
            if command == START:
                self._prechecks.request(
                    "start",
                    session_date,
                    "speech",
                    now,
                    self._announce_precheck,
                )
                message = "{}，请在书桌前保持五秒，我先检查一下。".format(
                    child_name
                )
            elif command == PAUSE:
                self._sessions.pause(session_date, now, source="speech")
                message = "{}，好的，先休息一下。".format(child_name)
            elif command == RESUME:
                self._prechecks.request(
                    "resume",
                    session_date,
                    "speech",
                    now,
                    self._announce_precheck,
                )
                message = "{}，请在书桌前保持五秒，我先检查一下。".format(
                    child_name
                )
            elif command == FINISH:
                return self._start_finish_review(now)
            elif command == QUERY_HOMEWORK:
                message = _homework_message(
                    child_name,
                    self._family.list_agenda(now.date()),
                )
            elif command == QUERY_COURSES:
                message = _course_message(
                    child_name,
                    self._family.list_agenda(now.date()),
                    self._family.list_courses_for_date(now.date()),
                )
            elif command == START_CHECKIN:
                return self._start_checkin(original_text, now)
            elif command == FINISH_CHECKIN:
                raise ValueError("video checkin is not recording")
            else:
                raise ValueError("unknown speech command")
        except ValueError:
            LOGGER.info("speech command rejected in current workflow: %s", command)
            self._speak_and_record(
                "{}，现在还不能执行这个操作。".format(child_name),
                command,
                "failed",
            )
            return True

        self._speak_and_record(message, command, "success")
        return True

    def _handle_dialogue(
        self,
        dialogue: dict,
        text: str,
        now: datetime,
    ) -> bool:
        self._activities.record(
            now,
            "speech",
            "speech_input",
            self._input_source,
            "success",
            "收到交互回复",
            text,
            {
                "dialogue_type": dialogue["dialogue_type"],
                "dialogue_state": dialogue["state"],
            },
        )
        normalized = normalize_speech_text(text)
        context = dialogue["context"]
        if dialogue["dialogue_type"] == "command_confirmation":
            if _contains_any(normalized, context["cancel_phrases"]):
                self._dialogues.complete(dialogue["id"], "cancelled", now)
                self._speak_and_record(
                    "好的，已取消这次操作。",
                    context["command"],
                    "cancelled",
                )
                return True
            if _contains_any(normalized, context["confirm_phrases"]):
                self._dialogues.complete(dialogue["id"], "completed", now)
                return self._execute(
                    context["command"],
                    context.get("original_text", ""),
                    now,
                )
            return self._ignore_invalid_dialogue_reply(dialogue, now)

        if dialogue["dialogue_type"] == "finish_review":
            return self._handle_finish_review(dialogue, normalized, now)
        if dialogue["dialogue_type"] == "checkin":
            return self._handle_checkin(dialogue, normalized, now)
        return False

    def _start_finish_review(self, now: datetime) -> bool:
        items = self._family.list_finish_check_items(now.date())
        child_name = self._family.get_settings()["child_name"]
        if not items:
            self._sessions.finish(now.date().isoformat(), now, source="speech")
            self._speak_and_record(
                "{}，今天没有待确认任务，本次学习记录已经结束。".format(
                    child_name
                ),
                FINISH,
                "success",
            )
            return True
        settings = self._family.get_settings()
        context = {
            "item_ids": [item["id"] for item in items],
            "index": 0,
            "results": {},
        }
        prompt = self._finish_item_prompt(items[0])
        self._dialogues.start(
            now.date().isoformat(),
            "finish_review",
            "review_item",
            context,
            prompt,
            _finish_replies(items[0]),
            now,
            int(settings["dialogue_first_retry_seconds"]),
        )
        self._speak_and_record(prompt, FINISH, "success")
        return True

    def _handle_finish_review(
        self,
        dialogue: dict,
        normalized: str,
        now: datetime,
    ) -> bool:
        context = dialogue["context"]
        items = {
            item["id"]: item
            for item in self._family.list_agenda(now.date())
        }
        item_id = context["item_ids"][context["index"]]
        item = items.get(item_id)
        if item is None:
            return self._advance_finish_review(dialogue, "skipped", now)

        if item.get("completion_mode") == "video":
            if _contains_any(normalized, ("现在打卡", "开始打卡", "马上打卡")):
                self._dialogues.complete(dialogue["id"], "completed", now)
                return self._prepare_checkin(item, now, context)
            if _contains_any(normalized, ("稍后完成", "还没有", "等会")):
                return self._advance_finish_review(dialogue, "later", now)
            if _contains_any(normalized, ("跳过", "不打卡")):
                return self._advance_finish_review(dialogue, "skipped", now)
        elif item.get("completion_mode") == "parent":
            if _contains_any(normalized, ("稍后完成", "还没有", "等会")):
                return self._advance_finish_review(dialogue, "later", now)
            if _contains_any(normalized, ("跳过", "不确认")):
                return self._advance_finish_review(dialogue, "skipped", now)
        else:
            if _contains_any(normalized, ("完成了", "已经完成", "做完了")):
                return self._advance_finish_review(
                    dialogue, "child_confirmed", now
                )
            if _contains_any(normalized, ("还没有", "没完成", "稍后完成")):
                return self._advance_finish_review(dialogue, "later", now)
            if _contains_any(normalized, ("跳过", "不确认")):
                return self._advance_finish_review(dialogue, "skipped", now)
        if _contains_any(normalized, ("继续学习", "取消结束")):
            self._dialogues.complete(dialogue["id"], "cancelled", now)
            self._speak_and_record(
                "好的，继续保持学习记录。",
                FINISH,
                "cancelled",
            )
            return True
        return self._ignore_invalid_dialogue_reply(dialogue, now)

    def _advance_finish_review(
        self,
        dialogue: dict,
        evidence: str,
        now: datetime,
    ) -> bool:
        context = dialogue["context"]
        item_id = context["item_ids"][context["index"]]
        self._family.set_completion_evidence(item_id, evidence, now)
        context["results"][str(item_id)] = evidence
        context["index"] += 1
        if context["index"] >= len(context["item_ids"]):
            self._dialogues.complete(dialogue["id"], "completed", now)
            self._sessions.finish(now.date().isoformat(), now, source="speech")
            self._speak_and_record(
                "完成情况已经记录，本次学习结束。家长可以在日程和执行历史中查看。",
                FINISH,
                "success",
            )
            return True
        items = {
            item["id"]: item
            for item in self._family.list_agenda(now.date())
        }
        next_item = items[context["item_ids"][context["index"]]]
        prompt = self._finish_item_prompt(next_item)
        settings = self._family.get_settings()
        self._dialogues.update(
            dialogue["id"],
            "review_item",
            context,
            prompt,
            _finish_replies(next_item),
            now,
            int(settings["dialogue_first_retry_seconds"]),
        )
        self._speak_and_record(prompt, FINISH, "success")
        return True

    def _finish_item_prompt(self, item: dict) -> str:
        prompt = item.get("finish_prompt") or (
            "“{title}”完成了吗？请回答“完成了”“还没有”或者“跳过”。"
        )
        return prompt.format(title=item["title"])

    def _start_checkin(self, text: str, now: datetime) -> bool:
        items = [
            item
            for item in self._family.list_finish_check_items(now.date())
            if item.get("completion_mode") == "video"
        ]
        normalized = normalize_speech_text(text)
        matched = [
            item
            for item in items
            if normalize_speech_text(item["title"]) in normalized
            or (
                bool(item.get("task_type_label"))
                and normalize_speech_text(item["task_type_label"])
                in normalized
            )
        ]
        if len(matched) == 1:
            return self._prepare_checkin(matched[0], now)
        if len(items) == 1:
            return self._prepare_checkin(items[0], now)
        if not items:
            self._speak_and_record(
                "今天没有录入需要视频打卡的任务，请让家长先在日程中添加。",
                START_CHECKIN,
                "failed",
            )
            return True
        names = "、".join(item["title"] for item in items)
        self._speak_and_record(
            "今天有{}。请说出完整的打卡名称。".format(names),
            START_CHECKIN,
            "failed",
        )
        return True

    def _prepare_checkin(
        self,
        item: dict,
        now: datetime,
        finish_context: Optional[dict] = None,
    ) -> bool:
        settings = self._family.get_settings()
        recording_id = self._checkins.create(
            item["id"],
            now.date().isoformat(),
            item["task_type_key"],
            now,
        )
        context = {
            "agenda_item_id": item["id"],
            "task_type_key": item["task_type_key"],
            "recording_id": recording_id,
            "max_recording_seconds": item.get("max_recording_seconds"),
            "finish_context": finish_context,
        }
        prompt = (
            "{}准备开始。请准备好材料并坐到画面中，准备好后说“开始打卡”，"
            "也可以说“取消打卡”。"
        ).format(item["title"])
        self._dialogues.start(
            now.date().isoformat(),
            "checkin",
            "preparing",
            context,
            prompt,
            ["开始打卡", "取消打卡"],
            now,
            int(settings["dialogue_first_retry_seconds"]),
        )
        self._record_checkin_activity(
            now,
            "checkin_preparing",
            "info",
            "视频打卡等待准备",
            item["title"],
            context,
        )
        self._speak_and_record(prompt, START_CHECKIN, "success")
        return True

    def _handle_checkin(
        self,
        dialogue: dict,
        normalized: str,
        now: datetime,
    ) -> bool:
        context = dialogue["context"]
        if dialogue["state"] == "preparing":
            if _contains_any(normalized, ("取消打卡", "取消")):
                self._checkins.finish(
                    context["recording_id"], "cancelled", now
                )
                self._dialogues.complete(dialogue["id"], "cancelled", now)
                self._record_checkin_activity(
                    now,
                    "checkin_cancelled",
                    "cancelled",
                    "视频打卡已取消",
                    "孩子在准备阶段取消了打卡。",
                    context,
                )
                self._speak_and_record(
                    "好的，本次打卡已取消。",
                    START_CHECKIN,
                    "cancelled",
                )
                return True
            if _contains_any(normalized, ("开始打卡", "准备好了")):
                try:
                    self._recorder.start(
                        context["recording_id"],
                        context["task_type_key"],
                        now,
                    )
                except (RuntimeError, ValueError) as exc:
                    self._dialogues.complete(dialogue["id"], "cancelled", now)
                    self._record_checkin_activity(
                        now,
                        "checkin_failed",
                        "failed",
                        "视频打卡启动失败",
                        str(exc),
                        context,
                    )
                    self._speak_and_record(
                        "打卡录制没有启动：{}。".format(str(exc)),
                        START_CHECKIN,
                        "failed",
                    )
                    return True
                context["started_at"] = now.isoformat()
                finish_phrases = self._checkin_finish_phrases()
                finish_phrase = finish_phrases[0]
                prompt = (
                    "录制已经开始。完成后请明确说“{}”，"
                    "需要放弃请说“取消打卡”。"
                ).format(finish_phrase)
                self._dialogues.update(
                    dialogue["id"],
                    "recording",
                    context,
                    prompt,
                    [*finish_phrases, "取消打卡"],
                    now,
                    None,
                )
                self._record_checkin_activity(
                    now,
                    "checkin_started",
                    "success",
                    "视频打卡开始录制",
                    None,
                    context,
                )
                self._speak_and_record(prompt, START_CHECKIN, "success")
                return True
        elif dialogue["state"] == "recording":
            if _contains_any(normalized, ("取消打卡",)):
                self._recorder.stop(now, save=False)
                self._dialogues.complete(dialogue["id"], "cancelled", now)
                self._record_checkin_activity(
                    now,
                    "checkin_cancelled",
                    "cancelled",
                    "视频打卡已取消",
                    "孩子在录制阶段取消了打卡。",
                    context,
                )
                self._speak_and_record(
                    "本次视频已经取消。",
                    START_CHECKIN,
                    "cancelled",
                )
                return True
            if self._matches_checkin_finish_intent(normalized):
                try:
                    duration = self._recorder.stop(now, save=True)
                except (RuntimeError, ValueError) as exc:
                    self._dialogues.complete(dialogue["id"], "cancelled", now)
                    self._record_checkin_activity(
                        now,
                        "checkin_failed",
                        "failed",
                        "视频打卡保存失败",
                        str(exc),
                        context,
                    )
                    self._speak_and_record(
                        "视频保存失败：{}。".format(str(exc)),
                        START_CHECKIN,
                        "failed",
                    )
                    return True
                self._family.set_completion_evidence(
                    context["agenda_item_id"],
                    "video",
                    now,
                )
                self._dialogues.complete(dialogue["id"], "completed", now)
                self._record_checkin_activity(
                    now,
                    "checkin_saved",
                    "success",
                    "视频打卡已保存",
                    "录制时长 {:.1f} 秒".format(duration),
                    context,
                )
                message = "打卡视频已保存，共录制{}秒。".format(
                    max(1, round(duration))
                )
                self._speak_and_record(
                    message,
                    START_CHECKIN,
                    "success",
                )
                finish_context = context.get("finish_context")
                if finish_context:
                    return self._resume_finish_after_checkin(
                        finish_context,
                        now,
                    )
                return True
        return self._ignore_invalid_dialogue_reply(dialogue, now)

    def _checkin_finish_phrases(self) -> list[str]:
        intent = self._intents.get(FINISH_CHECKIN)
        if intent is None or not intent["enabled"]:
            return list(DEFAULT_SPEECH_PHRASES[FINISH_CHECKIN])
        return intent["phrases"]

    def _matches_checkin_finish_intent(self, text: str) -> bool:
        intent = self._intents.get(FINISH_CHECKIN)
        if intent is None or not intent["enabled"]:
            return False
        return match_speech_intent(text, [intent]).command == FINISH_CHECKIN

    def _resume_finish_after_checkin(
        self,
        context: dict,
        now: datetime,
    ) -> bool:
        item_id = context["item_ids"][context["index"]]
        context["results"][str(item_id)] = "video"
        context["index"] += 1
        if context["index"] >= len(context["item_ids"]):
            self._sessions.finish(now.date().isoformat(), now, source="speech")
            self._speak_and_record(
                "所有完成情况已经记录，本次学习结束。",
                FINISH,
                "success",
            )
            return True
        items = {
            item["id"]: item
            for item in self._family.list_agenda(now.date())
        }
        next_item = items[context["item_ids"][context["index"]]]
        prompt = self._finish_item_prompt(next_item)
        settings = self._family.get_settings()
        self._dialogues.start(
            now.date().isoformat(),
            "finish_review",
            "review_item",
            context,
            prompt,
            _finish_replies(next_item),
            now,
            int(settings["dialogue_first_retry_seconds"]),
        )
        self._speak_and_record(prompt, FINISH, "success")
        return True

    def _ignore_invalid_dialogue_reply(
        self,
        dialogue: dict,
        now: datetime,
    ) -> bool:
        self._activities.record(
            now,
            "speech",
            "dialogue_reply_ignored",
            self._input_source,
            "ignored",
            "交互回复未匹配",
            "保持等待，按配置时间再次询问。",
            {
                "dialogue_type": dialogue["dialogue_type"],
                "dialogue_state": dialogue["state"],
                "allowed_replies": dialogue["allowed_replies"],
            },
        )
        return True

    def repeat_due_prompts(self) -> None:
        now = datetime.now(self._timezone)
        self._finish_overdue_checkin(now)
        settings = self._family.get_settings()
        max_prompts = int(settings["dialogue_max_prompts"])
        next_retry = int(settings["dialogue_next_retry_seconds"])
        for dialogue in self._dialogues.due(now):
            if self._dialogues.mark_prompted(
                dialogue["id"],
                now,
                next_retry,
                max_prompts,
            ):
                self._speak_and_record(
                    dialogue["prompt"],
                    "dialogue_retry",
                    "success",
                )
            else:
                self._activities.record(
                    now,
                    "speech",
                    "dialogue_expired",
                    "assistant",
                    "expired",
                    "交互等待已结束",
                    "多次询问仍未收到有效回答，系统停止继续询问。",
                    {"dialogue_type": dialogue["dialogue_type"]},
                )

    def _finish_overdue_checkin(self, now: datetime) -> None:
        dialogue = self._dialogues.get_active(now.date().isoformat())
        if (
            dialogue is None
            or dialogue["dialogue_type"] != "checkin"
            or dialogue["state"] != "recording"
        ):
            return
        context = dialogue["context"]
        maximum = context.get("max_recording_seconds")
        started_at = context.get("started_at")
        if not maximum or not started_at:
            return
        elapsed = (now - datetime.fromisoformat(started_at)).total_seconds()
        if elapsed < int(maximum):
            return
        try:
            duration = self._recorder.stop(now, save=True)
        except (RuntimeError, ValueError) as exc:
            self._dialogues.complete(dialogue["id"], "cancelled", now)
            self._record_checkin_activity(
                now,
                "checkin_failed",
                "failed",
                "视频打卡自动停止失败",
                str(exc),
                context,
            )
            self._speak_and_record(
                "视频已达到最长时长，但保存失败：{}。".format(str(exc)),
                START_CHECKIN,
                "failed",
            )
            return
        self._family.set_completion_evidence(
            context["agenda_item_id"],
            "video",
            now,
        )
        self._dialogues.complete(dialogue["id"], "completed", now)
        self._record_checkin_activity(
            now,
            "checkin_saved",
            "success",
            "视频打卡达到最长时长并自动保存",
            "录制时长 {:.1f} 秒".format(duration),
            context,
        )
        self._speak_and_record(
            "视频已达到最长录制时长，打卡已经自动保存。",
            START_CHECKIN,
            "success",
        )
        finish_context = context.get("finish_context")
        if finish_context:
            self._resume_finish_after_checkin(finish_context, now)

    def _record_checkin_activity(
        self,
        now: datetime,
        event_type: str,
        status: str,
        title: str,
        summary: Optional[str],
        context: dict,
    ) -> None:
        self._activities.record(
            now,
            "study",
            event_type,
            "camera",
            status,
            title,
            summary,
            {
                "agenda_item_id": context["agenda_item_id"],
                "task_type_key": context["task_type_key"],
                "recording_id": context["recording_id"],
            },
        )

    def _announce_precheck(self, result: PrecheckResult) -> None:
        child_name = self._family.get_settings()["child_name"]
        if result.passed:
            message = "{}，检查通过，开始记录学习。".format(child_name)
        else:
            message = "{}，{}。".format(child_name, result.message)
        self._speak_and_record(
            message,
            "precheck_result",
            "success" if result.passed else "failed",
        )

    def _speak_and_record(
        self,
        message: str,
        command: str,
        status: str,
    ) -> None:
        self._audio.speak(message)
        self._activities.record(
            datetime.now(self._timezone),
            "speech",
            "speech_output",
            self._audio.source,
            status,
            "系统语音回复",
            message,
            {"command": command},
        )


class SherpaSpeechListener:
    def __init__(
        self,
        config: SpeechConfig,
        on_text: Callable[[str], bool],
        audio_stream_url: Optional[str] = None,
    ):
        self._config = config
        self._on_text = on_text
        self._audio_stream_url = audio_stream_url

    def run(self, stop_event: threading.Event) -> None:
        try:
            import sounddevice
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "speech dependencies are missing; install the speech extra"
            ) from exc
        if not self._config.model_path.is_dir():
            raise FileNotFoundError(
                "sherpa-onnx model directory not found: {}".format(
                    self._config.model_path
                )
            )

        encoder = self._config.model_path / "encoder-epoch-99-avg-1.int8.onnx"
        decoder = self._config.model_path / "decoder-epoch-99-avg-1.onnx"
        joiner = self._config.model_path / "joiner-epoch-99-avg-1.int8.onnx"
        tokens = self._config.model_path / "tokens.txt"
        for model_file in (encoder, decoder, joiner, tokens):
            if not model_file.is_file():
                raise FileNotFoundError(
                    "sherpa-onnx model file not found: {}".format(model_file)
                )

        audio_queue: queue.Queue[object] = queue.Queue(maxsize=4)

        def callback(indata, frames, callback_time, status) -> None:
            if status:
                LOGGER.warning("microphone status: %s", status)
            if not AudioOutput.is_speaking():
                _queue_audio(
                    audio_queue,
                    (indata.copy().reshape(-1), self._config.sample_rate),
                )

        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(tokens),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            num_threads=1,
            sample_rate=16000,
            feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=1.5,
            rule2_min_trailing_silence=0.8,
            rule3_min_utterance_length=10,
        )
        stream = recognizer.create_stream()
        if self._config.input_source == "camera":
            if not self._audio_stream_url:
                raise RuntimeError(
                    "camera speech input requires video.stream_url"
                )
            LOGGER.info("using camera audio input")
            with _CameraAudioInput(
                self._audio_stream_url,
                self._config.block_size,
            ) as camera_audio:
                self._run_recognizer(
                    stop_event,
                    recognizer,
                    stream,
                    camera_audio,
                )
            return

        LOGGER.info("using MacBook microphone input")
        last_command = None
        last_command_at = 0.0
        LOGGER.info("starting local speech recognition")
        with sounddevice.InputStream(
            samplerate=self._config.sample_rate,
            blocksize=self._config.block_size,
            device=self._config.device,
            dtype="float32",
            channels=1,
            callback=callback,
        ):
            self._run_recognizer(
                stop_event,
                recognizer,
                stream,
                audio_queue,
            )

    def _run_recognizer(
        self,
        stop_event: threading.Event,
        recognizer,
        stream,
        audio_queue,
    ) -> None:
        last_command = None
        last_command_at = 0.0
        while not stop_event.is_set():
            try:
                sample, sample_rate = audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if AudioOutput.is_speaking():
                _discard_audio(audio_queue)
                continue
            stream.accept_waveform(sample_rate, sample)
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            if not recognizer.is_endpoint(stream):
                continue
            text = str(recognizer.get_result(stream)).strip()
            recognizer.reset(stream)
            if not text:
                continue
            now = time.monotonic()
            if (
                text == last_command
                and now - last_command_at
                < self._config.command_cooldown_seconds
            ):
                continue
            LOGGER.info("speech recognized: %s", text)
            if self._on_text(text):
                last_command = text
                last_command_at = now


class _CameraAudioInput:
    def __init__(self, stream_url: str, block_size: int):
        self._stream_url = stream_url
        self._chunk_bytes = (
            max(
                int(
                    block_size
                    * CAMERA_AUDIO_SAMPLE_RATE
                    / 48000
                ),
                1,
            )
            * 4
        )
        self._queue: queue.Queue[object] = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()

    def __enter__(self) -> "_CameraAudioInput":
        if shutil.which("ffmpeg") is None:
            raise RuntimeError(
                "camera speech input requires ffmpeg in PATH"
            )
        self._thread = threading.Thread(
            target=self._run,
            name="camera-audio-input",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def get(self, timeout: float) -> tuple[object, int]:
        sample = self._queue.get(timeout=timeout)
        if sample is _CAMERA_AUDIO_END:
            raise RuntimeError("camera audio stream stopped")
        return sample

    def get_nowait(self) -> object:
        return self._queue.get_nowait()

    def close(self) -> None:
        self._stop.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            _terminate_process(process)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        import numpy

        while not self._stop.is_set():
            process = self._start_process()
            if process is None:
                self._stop.wait(3)
                continue
            with self._process_lock:
                self._process = process
            try:
                while not self._stop.is_set():
                    raw = process.stdout.read(self._chunk_bytes)
                    if not raw:
                        break
                    usable_length = len(raw) - (len(raw) % 4)
                    if usable_length == 0:
                        continue
                    sample = numpy.frombuffer(
                        raw[:usable_length],
                        dtype="<f4",
                    ).copy()
                    _queue_audio(
                        self._queue,
                        (sample, CAMERA_AUDIO_SAMPLE_RATE),
                    )
            finally:
                with self._process_lock:
                    if self._process is process:
                        self._process = None
                _terminate_process(process)
            if not self._stop.is_set():
                LOGGER.warning("camera audio stream ended; reconnecting")
                self._stop.wait(3)

        _queue_audio(self._queue, _CAMERA_AUDIO_END)

    def _start_process(self) -> Optional[subprocess.Popen]:
        try:
            return subprocess.Popen(
                _camera_audio_command(self._stream_url),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            LOGGER.exception("unable to start camera audio input")
            return None


_CAMERA_AUDIO_END = object()


def _camera_audio_command(stream_url: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-rtsp_transport",
        "tcp",
        "-i",
        stream_url,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(CAMERA_AUDIO_SAMPLE_RATE),
        "-acodec",
        "pcm_f32le",
        "-f",
        "f32le",
        "pipe:1",
    ]


def _queue_audio(audio_queue: queue.Queue, item: object) -> None:
    try:
        audio_queue.put_nowait(item)
    except queue.Full:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass
        audio_queue.put_nowait(item)


def _discard_audio(audio_queue: queue.Queue) -> None:
    while True:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            return


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_speech_service(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event,
    prechecks: PrecheckCoordinator,
) -> None:
    config = load_config(config_path)
    handler = SpeechCommandHandler(config, database_path, prechecks)
    prompt_thread = threading.Thread(
        target=_run_dialogue_prompts,
        args=(handler, stop_event),
        name="dialogue-prompts",
        daemon=True,
    )
    prompt_thread.start()
    try:
        SherpaSpeechListener(
            config.speech,
            handler.handle,
            config.video.stream_url,
        ).run(stop_event)
    finally:
        prompt_thread.join(timeout=2)


def run_speech_test(config_path: Path) -> None:
    config = load_config(config_path)
    stop_event = threading.Event()

    def print_text(text: str) -> bool:
        command = parse_speech_command(text)
        print(
            "text={} command={}".format(
                text,
                command or "unknown",
            )
        )
        return command is not None

    try:
        SherpaSpeechListener(
            config.speech,
            print_text,
            config.video.stream_url,
        ).run(stop_event)
    except KeyboardInterrupt:
        stop_event.set()


def _homework_message(child_name: str, items: list[dict]) -> str:
    homework = [
        item["title"]
        for item in items
        if item["item_type"] == "homework" and item["status"] == "pending"
    ]
    if not homework:
        return "{}，今天没有未完成的作业。".format(child_name)
    return "{}，今天的作业有{}。".format(child_name, "、".join(homework))


def _course_message(
    child_name: str,
    items: list[dict],
    weekly_courses: list[dict],
) -> str:
    courses = [
        (item["scheduled_time"], item["title"])
        for item in items
        if item["item_type"] == "course" and item["status"] == "pending"
    ]
    courses.extend(
        (course["start_time"], course["name"])
        for course in weekly_courses
    )
    courses.sort(key=lambda item: item[0] or "99:99")
    if not courses:
        return "{}，今天没有录入课程。".format(child_name)
    descriptions = [
        "{} {}".format(_spoken_time(start_time), name)
        for start_time, name in courses
    ]
    return "{}，今天的课程有{}。".format(child_name, "、".join(descriptions))


def _spoken_time(value: Optional[str]) -> str:
    if not value:
        return "时间未定"
    hours, minutes = value.split(":", 1)
    if minutes == "00":
        return "{}点".format(int(hours))
    return "{}点{}分".format(int(hours), int(minutes))


def _contains_any(normalized_text: str, phrases) -> bool:
    return any(
        normalize_speech_text(phrase) in normalized_text
        for phrase in phrases
    )


def _finish_replies(item: dict) -> list[str]:
    if item.get("completion_mode") == "video":
        return ["现在打卡", "稍后完成", "跳过", "继续学习"]
    if item.get("completion_mode") == "parent":
        return ["稍后完成", "跳过", "继续学习"]
    return ["完成了", "还没有", "跳过", "继续学习"]


def _run_dialogue_prompts(
    handler: SpeechCommandHandler,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(1):
        try:
            handler.repeat_due_prompts()
        except Exception:
            LOGGER.exception("unable to process dialogue retry")

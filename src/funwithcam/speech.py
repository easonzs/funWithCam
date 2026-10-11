import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .config import AppConfig, SpeechConfig, load_config
from .storage.database import Database
from .storage.family_repository import FamilyRepository
from .storage.study_session_repository import StudySessionRepository


LOGGER = logging.getLogger(__name__)

START = "start"
PAUSE = "pause"
RESUME = "resume"
FINISH = "finish"
QUERY_HOMEWORK = "query_homework"
QUERY_COURSES = "query_courses"


def parse_speech_command(text: str) -> Optional[str]:
    normalized = "".join(
        character
        for character in text.lower()
        if character not in " ，。！？,.!?、\t\r\n"
    )
    phrases = (
        (
            FINISH,
            ("结束学习", "结束记录", "作业写完了", "我写完了", "写完了"),
        ),
        (PAUSE, ("我要休息", "休息一下", "暂停学习", "休息")),
        (RESUME, ("继续学习", "继续写作业", "继续记录", "继续")),
        (START, ("开始写作业", "开始学习", "开始记录")),
        (
            QUERY_HOMEWORK,
            ("今天有什么作业", "今天作业是什么", "今天的作业是什么"),
        ),
        (
            QUERY_COURSES,
            ("今天有什么课", "今天的课是什么", "今天几点上课"),
        ),
    )
    for command, candidates in phrases:
        if any(candidate in normalized for candidate in candidates):
            return command
    return None


class SpeechCommandHandler:
    def __init__(self, config: AppConfig, database_path: Path):
        database = Database(database_path)
        database.initialize()
        self._timezone = ZoneInfo(config.timezone)
        self._family = FamilyRepository(database)
        self._sessions = StudySessionRepository(database)
        self._audio = AudioOutput(config.audio.command)

    def handle(self, text: str) -> bool:
        command = parse_speech_command(text)
        if command is None:
            return False

        now = datetime.now(self._timezone)
        session_date = now.date().isoformat()
        child_name = self._family.get_settings()["child_name"]
        try:
            if command == START:
                self._sessions.start(session_date, "speech", now)
                message = "{}，好的，开始记录学习。".format(child_name)
            elif command == PAUSE:
                self._sessions.pause(session_date, now)
                message = "{}，好的，先休息一下。".format(child_name)
            elif command == RESUME:
                self._sessions.resume(session_date, now)
                message = "{}，好的，继续记录学习。".format(child_name)
            elif command == FINISH:
                self._sessions.finish(session_date, now)
                message = "{}，本次学习记录已经结束。".format(child_name)
            elif command == QUERY_HOMEWORK:
                message = _homework_message(
                    child_name,
                    self._family.list_agenda(now.date()),
                )
            else:
                message = _course_message(
                    child_name,
                    self._family.list_agenda(now.date()),
                    self._family.list_courses_for_date(now.date()),
                )
        except ValueError:
            LOGGER.info("speech command rejected in current workflow: %s", command)
            self._audio.speak("{}，现在还不能执行这个操作。".format(child_name))
            return True

        self._audio.speak(message)
        return True


class VoskSpeechListener:
    def __init__(
        self,
        config: SpeechConfig,
        on_text: Callable[[str], bool],
    ):
        self._config = config
        self._on_text = on_text

    def run(self, stop_event: threading.Event) -> None:
        try:
            import sounddevice
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:
            raise RuntimeError(
                "speech dependencies are missing; install the speech extra"
            ) from exc
        if not self._config.model_path.is_dir():
            raise FileNotFoundError(
                "Vosk model directory not found: {}".format(
                    self._config.model_path
                )
            )

        audio_queue: queue.Queue[bytes] = queue.Queue()

        def callback(indata, frames, callback_time, status) -> None:
            if status:
                LOGGER.warning("microphone status: %s", status)
            if not AudioOutput.is_speaking():
                audio_queue.put(bytes(indata))

        model = Model(str(self._config.model_path))
        recognizer = KaldiRecognizer(model, self._config.sample_rate)
        last_command = None
        last_command_at = 0.0
        LOGGER.info("starting local speech recognition")
        with sounddevice.RawInputStream(
            samplerate=self._config.sample_rate,
            blocksize=self._config.block_size,
            device=self._config.device,
            dtype="int16",
            channels=1,
            callback=callback,
        ):
            while not stop_event.is_set():
                try:
                    data = audio_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if AudioOutput.is_speaking() or not recognizer.AcceptWaveform(data):
                    continue
                text = str(json.loads(recognizer.Result()).get("text", "")).strip()
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


def run_speech_service(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event,
) -> None:
    config = load_config(config_path)
    handler = SpeechCommandHandler(config, database_path)
    VoskSpeechListener(config.speech, handler.handle).run(stop_event)


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
        VoskSpeechListener(config.speech, print_text).run(stop_event)
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

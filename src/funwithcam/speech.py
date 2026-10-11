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
from .precheck import PrecheckCoordinator, PrecheckResult
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
    def __init__(
        self,
        config: AppConfig,
        database_path: Path,
        prechecks: PrecheckCoordinator,
    ):
        database = Database(database_path)
        database.initialize()
        self._timezone = ZoneInfo(config.timezone)
        self._family = FamilyRepository(database)
        self._sessions = StudySessionRepository(database)
        self._audio = AudioOutput(config.audio.command)
        self._prechecks = prechecks

    def handle(self, text: str) -> bool:
        command = parse_speech_command(text)
        if command is None:
            return False

        now = datetime.now(self._timezone)
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
                self._sessions.pause(session_date, now)
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

    def _announce_precheck(self, result: PrecheckResult) -> None:
        child_name = self._family.get_settings()["child_name"]
        if result.passed:
            message = "{}，检查通过，开始记录学习。".format(child_name)
        else:
            message = "{}，{}。".format(child_name, result.message)
        self._audio.speak(message)


class SherpaSpeechListener:
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

        audio_queue: queue.Queue[object] = queue.Queue()

        def callback(indata, frames, callback_time, status) -> None:
            if status:
                LOGGER.warning("microphone status: %s", status)
            if not AudioOutput.is_speaking():
                audio_queue.put(indata.copy().reshape(-1))

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
            while not stop_event.is_set():
                try:
                    data = audio_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if AudioOutput.is_speaking():
                    continue
                stream.accept_waveform(self._config.sample_rate, data)
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


def run_speech_service(
    config_path: Path,
    database_path: Path,
    stop_event: threading.Event,
    prechecks: PrecheckCoordinator,
) -> None:
    config = load_config(config_path)
    handler = SpeechCommandHandler(config, database_path, prechecks)
    SherpaSpeechListener(config.speech, handler.handle).run(stop_event)


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
        SherpaSpeechListener(config.speech, print_text).run(stop_event)
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

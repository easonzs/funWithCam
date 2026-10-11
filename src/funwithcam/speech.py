import logging
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .config import AppConfig, SpeechConfig, load_config
from .precheck import PrecheckCoordinator, PrecheckResult
from .storage.activity_repository import ActivityRepository
from .storage.database import Database
from .storage.family_repository import FamilyRepository
from .storage.study_session_repository import StudySessionRepository


LOGGER = logging.getLogger(__name__)
CAMERA_AUDIO_SAMPLE_RATE = 16000

START = "start"
PAUSE = "pause"
RESUME = "resume"
FINISH = "finish"
QUERY_HOMEWORK = "query_homework"
QUERY_COURSES = "query_courses"

SPEECH_COMMAND_GUIDE = (
    {
        "command": START,
        "label": "开始学习",
        "phrases": ("开始写作业", "开始学习", "开始记录"),
        "description": "完成开始前检查并建立学习记录；今日简报完成后可用。",
    },
    {
        "command": PAUSE,
        "label": "暂停学习",
        "phrases": ("我要休息", "休息一下", "暂停学习", "休息"),
        "description": "暂停当前学习记录；仅在正在学习时可用。",
    },
    {
        "command": RESUME,
        "label": "继续学习",
        "phrases": ("继续学习", "继续写作业", "继续记录", "继续"),
        "description": "重新检查书桌状态后继续记录；仅在暂停或中断时可用。",
    },
    {
        "command": FINISH,
        "label": "结束学习",
        "phrases": (
            "结束学习",
            "结束记录",
            "作业写完了",
            "我写完了",
            "写完了",
        ),
        "description": "结束当前学习记录；仅在学习、离座或暂停状态可用。",
    },
    {
        "command": QUERY_HOMEWORK,
        "label": "查询今日作业",
        "phrases": (
            "今天有什么作业",
            "今天作业是什么",
            "今天的作业是什么",
        ),
        "description": "播报日程中尚未完成的学校作业。",
    },
    {
        "command": QUERY_COURSES,
        "label": "查询今日课程",
        "phrases": ("今天有什么课", "今天的课是什么", "今天几点上课"),
        "description": "播报今天录入的课程和每周固定课程。",
    },
)


def parse_speech_command(text: str) -> Optional[str]:
    normalized = "".join(
        character
        for character in text.lower()
        if character not in " ，。！？,.!?、\t\r\n"
    )
    for item in SPEECH_COMMAND_GUIDE:
        if any(candidate in normalized for candidate in item["phrases"]):
            return item["command"]
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
        self._activities = ActivityRepository(database)
        self._family = FamilyRepository(database)
        self._sessions = StudySessionRepository(database)
        self._audio = AudioOutput(config.audio.command)
        self._prechecks = prechecks

    def handle(self, text: str) -> bool:
        command = parse_speech_command(text)
        now = datetime.now(self._timezone)
        if command is None:
            self._activities.record(
                now,
                "speech",
                "speech_input",
                "microphone",
                "ignored",
                "语音未匹配到口令",
                text,
            )
            return False

        self._activities.record(
            now,
            "speech",
            "speech_input",
            "microphone",
            "success",
            "识别到语音口令",
            text,
            {"command": command},
        )
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
                self._sessions.finish(session_date, now, source="speech")
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
            self._speak_and_record(
                "{}，现在还不能执行这个操作。".format(child_name),
                command,
                "failed",
            )
            return True

        self._speak_and_record(message, command, "success")
        return True

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
            "assistant",
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
    SherpaSpeechListener(
        config.speech,
        handler.handle,
        config.video.stream_url,
    ).run(stop_event)


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

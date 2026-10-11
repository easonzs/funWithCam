from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple, Union


@dataclass(frozen=True)
class VideoConfig:
    stream_url: str
    inference_fps: float
    reconnect_seconds: float
    max_frame_age_seconds: float
    warmup_seconds: float


@dataclass(frozen=True)
class StudyAreaConfig:
    rect: Tuple[float, float, float, float]


@dataclass(frozen=True)
class PersonConfig:
    min_detection_confidence: float
    min_bbox_area_ratio: float
    max_bbox_area_ratio: float


@dataclass(frozen=True)
class PresenceConfig:
    confirm_seconds: float
    allowed_missed_seconds: float
    max_center_jump: float


@dataclass(frozen=True)
class PrecheckConfig:
    duration_seconds: float
    timeout_seconds: float
    min_valid_frame_ratio: float
    min_brightness: float


@dataclass(frozen=True)
class CalibrationConfig:
    duration_seconds: float
    timeout_seconds: float
    min_valid_samples: int
    image_dir: Path


@dataclass(frozen=True)
class AudioConfig:
    command: Tuple[str, ...]
    camera_test_text: str


@dataclass(frozen=True)
class SpeechConfig:
    enabled: bool
    input_source: str
    model_path: Path
    sample_rate: int
    block_size: int
    device: Optional[Union[int, str]]
    command_cooldown_seconds: float


@dataclass(frozen=True)
class IdentityConfig:
    detector_model: Path
    recognizer_model: Path
    detection_threshold: float
    nms_threshold: float
    min_face_size_px: int
    match_threshold: float
    ambiguity_margin: float
    confirmation_frames: int
    confirmation_window_seconds: float
    enrollment_samples: int
    sample_interval_seconds: float
    command_timeout_seconds: float
    save_debug_frames: bool
    debug_dir: Path
    debug_face_limit: int
    debug_rejected_limit: int


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int
    session_hours: int


@dataclass(frozen=True)
class AppConfig:
    timezone: str
    video: VideoConfig
    study_area: StudyAreaConfig
    person: PersonConfig
    presence: PresenceConfig
    precheck: PrecheckConfig
    calibration: CalibrationConfig
    audio: AudioConfig
    speech: SpeechConfig
    identity: IdentityConfig
    web: WebConfig
    log_level: str


def _require(mapping: Mapping[str, object], key: str) -> object:
    if key not in mapping:
        raise ValueError("missing configuration key: {}".format(key))
    return mapping[key]


def _as_mapping(value: object, key: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError("{} must be a mapping".format(key))
    return value


def _positive(value: object, key: str) -> float:
    number = float(value)
    if number <= 0:
        raise ValueError("{} must be positive".format(key))
    return number


def _non_negative(value: object, key: str) -> float:
    number = float(value)
    if number < 0:
        raise ValueError("{} must be zero or positive".format(key))
    return number


def _ratio(value: object, key: str) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError("{} must be between 0 and 1".format(key))
    return number


def _positive_int(value: object, key: str) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError("{} must be a positive integer".format(key))
    return number


def _boolean(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError("{} must be true or false".format(key))
    return value


def _rect(value: object) -> Tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("study_area.rect must contain four numbers")
    result = tuple(float(item) for item in value)
    x_min, y_min, x_max, y_max = result
    if not (0 <= x_min < x_max <= 1 and 0 <= y_min < y_max <= 1):
        raise ValueError("study_area.rect must be normalized and ordered")
    return result


def load_config(path: Path) -> AppConfig:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required") from exc

    with path.resolve().open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")

    video = _as_mapping(_require(raw, "video"), "video")
    study_area = _as_mapping(_require(raw, "study_area"), "study_area")
    person = _as_mapping(_require(raw, "person"), "person")
    presence = _as_mapping(_require(raw, "presence"), "presence")
    precheck = _as_mapping(
        raw.get(
            "precheck",
            {
                "duration_seconds": 5,
                "timeout_seconds": 15,
                "min_valid_frame_ratio": 0.8,
                "min_brightness": 45,
            },
        ),
        "precheck",
    )
    calibration = _as_mapping(
        raw.get(
            "calibration",
            {
                "duration_seconds": 10,
                "timeout_seconds": 25,
                "min_valid_samples": 12,
                "image_dir": "data/calibrations",
            },
        ),
        "calibration",
    )
    audio = _as_mapping(_require(raw, "audio"), "audio")
    speech = _as_mapping(
        raw.get(
            "speech",
            {
                "enabled": False,
                "model_path": (
                    "models/"
                    "sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23"
                ),
                "sample_rate": 48000,
                "block_size": 8000,
                "device": None,
                "command_cooldown_seconds": 2,
            },
        ),
        "speech",
    )
    identity = _as_mapping(_require(raw, "identity"), "identity")
    web = _as_mapping(_require(raw, "web"), "web")
    logging_config = _as_mapping(raw.get("logging", {}), "logging")
    speech_input_source = str(
        speech.get("input_source", "macbook")
    ).strip().lower()
    if speech_input_source not in {"macbook", "camera"}:
        raise ValueError(
            "speech.input_source must be macbook or camera"
        )

    min_area = _ratio(
        _require(person, "min_bbox_area_ratio"),
        "person.min_bbox_area_ratio",
    )
    max_area = _ratio(
        _require(person, "max_bbox_area_ratio"),
        "person.max_bbox_area_ratio",
    )
    if min_area >= max_area:
        raise ValueError("person min bbox area must be smaller than max bbox area")

    command = _require(audio, "command")
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) for item in command
    ):
        raise ValueError("audio.command must be a non-empty list of strings")
    if "{text}" not in command:
        raise ValueError("audio.command must contain a {text} argument")

    project_root = path.resolve().parent.parent

    precheck_duration = _positive(
        _require(precheck, "duration_seconds"),
        "precheck.duration_seconds",
    )
    precheck_timeout = _positive(
        _require(precheck, "timeout_seconds"),
        "precheck.timeout_seconds",
    )
    if precheck_timeout <= precheck_duration:
        raise ValueError("precheck timeout must be longer than duration")
    calibration_duration = _positive(
        _require(calibration, "duration_seconds"),
        "calibration.duration_seconds",
    )
    calibration_timeout = _positive(
        _require(calibration, "timeout_seconds"),
        "calibration.timeout_seconds",
    )
    if calibration_timeout <= calibration_duration:
        raise ValueError("calibration timeout must be longer than duration")

    return AppConfig(
        timezone=str(_require(raw, "timezone")),
        video=VideoConfig(
            stream_url=str(_require(video, "stream_url")),
            inference_fps=_positive(
                _require(video, "inference_fps"),
                "video.inference_fps",
            ),
            reconnect_seconds=_positive(
                _require(video, "reconnect_seconds"),
                "video.reconnect_seconds",
            ),
            max_frame_age_seconds=_positive(
                _require(video, "max_frame_age_seconds"),
                "video.max_frame_age_seconds",
            ),
            warmup_seconds=_non_negative(
                video.get("warmup_seconds", 1.5),
                "video.warmup_seconds",
            ),
        ),
        study_area=StudyAreaConfig(rect=_rect(_require(study_area, "rect"))),
        person=PersonConfig(
            min_detection_confidence=_ratio(
                _require(person, "min_detection_confidence"),
                "person.min_detection_confidence",
            ),
            min_bbox_area_ratio=min_area,
            max_bbox_area_ratio=max_area,
        ),
        presence=PresenceConfig(
            confirm_seconds=_positive(
                _require(presence, "confirm_seconds"),
                "presence.confirm_seconds",
            ),
            allowed_missed_seconds=_positive(
                _require(presence, "allowed_missed_seconds"),
                "presence.allowed_missed_seconds",
            ),
            max_center_jump=_ratio(
                _require(presence, "max_center_jump"),
                "presence.max_center_jump",
            ),
        ),
        precheck=PrecheckConfig(
            duration_seconds=precheck_duration,
            timeout_seconds=precheck_timeout,
            min_valid_frame_ratio=_ratio(
                _require(precheck, "min_valid_frame_ratio"),
                "precheck.min_valid_frame_ratio",
            ),
            min_brightness=_brightness(
                _require(precheck, "min_brightness"),
                "precheck.min_brightness",
            ),
        ),
        calibration=CalibrationConfig(
            duration_seconds=calibration_duration,
            timeout_seconds=calibration_timeout,
            min_valid_samples=_positive_int(
                _require(calibration, "min_valid_samples"),
                "calibration.min_valid_samples",
            ),
            image_dir=_project_path(
                project_root,
                _require(calibration, "image_dir"),
            ),
        ),
        audio=AudioConfig(
            command=tuple(command),
            camera_test_text=str(_require(audio, "camera_test_text")),
        ),
        speech=SpeechConfig(
            enabled=_boolean(_require(speech, "enabled"), "speech.enabled"),
            input_source=speech_input_source,
            model_path=_project_path(
                project_root,
                _require(speech, "model_path"),
            ),
            sample_rate=_positive_int(
                _require(speech, "sample_rate"),
                "speech.sample_rate",
            ),
            block_size=_positive_int(
                _require(speech, "block_size"),
                "speech.block_size",
            ),
            device=_speech_device(speech.get("device")),
            command_cooldown_seconds=_positive(
                _require(speech, "command_cooldown_seconds"),
                "speech.command_cooldown_seconds",
            ),
        ),
        identity=IdentityConfig(
            detector_model=_project_path(
                project_root,
                _require(identity, "detector_model"),
            ),
            recognizer_model=_project_path(
                project_root,
                _require(identity, "recognizer_model"),
            ),
            detection_threshold=_ratio(
                _require(identity, "detection_threshold"),
                "identity.detection_threshold",
            ),
            nms_threshold=_ratio(
                _require(identity, "nms_threshold"),
                "identity.nms_threshold",
            ),
            min_face_size_px=_positive_int(
                _require(identity, "min_face_size_px"),
                "identity.min_face_size_px",
            ),
            match_threshold=_ratio(
                _require(identity, "match_threshold"),
                "identity.match_threshold",
            ),
            ambiguity_margin=_ratio(
                _require(identity, "ambiguity_margin"),
                "identity.ambiguity_margin",
            ),
            confirmation_frames=_positive_int(
                _require(identity, "confirmation_frames"),
                "identity.confirmation_frames",
            ),
            confirmation_window_seconds=_positive(
                _require(identity, "confirmation_window_seconds"),
                "identity.confirmation_window_seconds",
            ),
            enrollment_samples=_positive_int(
                _require(identity, "enrollment_samples"),
                "identity.enrollment_samples",
            ),
            sample_interval_seconds=_positive(
                _require(identity, "sample_interval_seconds"),
                "identity.sample_interval_seconds",
            ),
            command_timeout_seconds=_positive(
                _require(identity, "command_timeout_seconds"),
                "identity.command_timeout_seconds",
            ),
            save_debug_frames=_boolean(
                _require(identity, "save_debug_frames"),
                "identity.save_debug_frames",
            ),
            debug_dir=_project_path(
                project_root,
                _require(identity, "debug_dir"),
            ),
            debug_face_limit=_positive_int(
                _require(identity, "debug_face_limit"),
                "identity.debug_face_limit",
            ),
            debug_rejected_limit=_positive_int(
                _require(identity, "debug_rejected_limit"),
                "identity.debug_rejected_limit",
            ),
        ),
        web=WebConfig(
            host=str(_require(web, "host")),
            port=_positive_int(_require(web, "port"), "web.port"),
            session_hours=_positive_int(
                _require(web, "session_hours"),
                "web.session_hours",
            ),
        ),
        log_level=str(logging_config.get("level", "INFO")).upper(),
    )


def _project_path(project_root: Path, value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _speech_device(value: object) -> Optional[Union[int, str]]:
    if value is None:
        return None
    if isinstance(value, (int, str)):
        return value
    raise ValueError("speech.device must be null, an integer, or a string")


def _brightness(value: object, key: str) -> float:
    number = float(value)
    if not 0 <= number <= 255:
        raise ValueError("{} must be between 0 and 255".format(key))
    return number


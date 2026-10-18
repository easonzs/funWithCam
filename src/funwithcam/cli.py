import argparse
import getpass
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .audio import AudioOutput
from .assistant_service import run_assistant
from .camera_test import run_camera_test
from .config import load_config
from .identity.commands import run_face_enrollment, run_face_verification
from .media_check import probe_media
from .speech import run_speech_test
from .storage.auth_repository import AuthRepository
from .storage.database import Database
from .web import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FunWithCam local study assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="initialize the SQLite database")
    init_db.add_argument(
        "--database",
        type=Path,
        default=Path("data/funwithcam.db"),
        help="SQLite database path",
    )

    audio_test = subparsers.add_parser("audio-test", help="play a test message")
    audio_test.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="configuration file path",
    )

    camera_test = subparsers.add_parser(
        "camera-test",
        help="detect stable presence and play one test message",
    )
    camera_test.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="configuration file path",
    )

    speech_test = subparsers.add_parser(
        "speech-test",
        help="print local microphone recognition results without changing state",
    )
    speech_test.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="configuration file path",
    )

    face_enroll = subparsers.add_parser(
        "face-enroll",
        help="capture and store one local face identity template",
    )
    _add_runtime_paths(face_enroll)
    face_enroll.add_argument("--role", choices=("child", "parent"), required=True)
    face_enroll.add_argument("--name", required=True)
    face_enroll.add_argument(
        "--profile",
        choices=("test", "formal"),
        default="test",
        help="test data is isolated from formal reminder identities",
    )

    face_verify = subparsers.add_parser(
        "face-verify",
        help="verify identity from multiple camera frames",
    )
    _add_runtime_paths(face_verify)
    face_verify.add_argument(
        "--profile",
        choices=("test", "formal"),
        default="test",
        help="identity profile set to use",
    )

    create_admin = subparsers.add_parser(
        "create-admin",
        help="create or replace the local parent Web login",
    )
    create_admin.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="configuration file path",
    )
    create_admin.add_argument(
        "--database",
        type=Path,
        default=Path("data/funwithcam.db"),
        help="SQLite database path",
    )
    create_admin.add_argument("--username", default="parent")
    create_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="read one password line from standard input",
    )

    admin_exists = subparsers.add_parser(
        "admin-exists",
        help="return success when a parent Web login exists",
    )
    admin_exists.add_argument(
        "--database",
        type=Path,
        default=Path("data/funwithcam.db"),
        help="SQLite database path",
    )
    admin_exists.add_argument("--username", default="parent")

    media_check = subparsers.add_parser(
        "media-check",
        help="verify that the camera stream contains usable media tracks",
    )
    media_check.add_argument(
        "--url",
        default="rtsp://go2rtc:8554/study_room",
        help="RTSP stream URL to inspect",
    )
    media_check.add_argument(
        "--timeout-seconds",
        type=float,
        default=15,
        help="maximum time to wait for stream metadata",
    )
    media_check.add_argument(
        "--require-audio",
        action="store_true",
        help="fail when the stream has no audio track",
    )

    web = subparsers.add_parser("web", help="run the family LAN Web service")
    _add_runtime_paths(web)

    assistant = subparsers.add_parser(
        "assistant-run",
        help="run camera identity, first briefing, and second reminders",
    )
    _add_runtime_paths(assistant)

    serve = subparsers.add_parser(
        "serve",
        help="run the Web service and reminder assistant in one process",
    )
    _add_runtime_paths(serve)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "init-db":
        database = Database(args.database)
        database.initialize()
        print("database initialized: {}".format(args.database.resolve()))
    elif args.command == "audio-test":
        config = load_config(args.config)
        logging.basicConfig(
            level=getattr(logging, config.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        AudioOutput.from_config(config.audio).speak(
            config.audio.camera_test_text
        )
    elif args.command == "camera-test":
        run_camera_test(args.config)
    elif args.command == "speech-test":
        run_speech_test(args.config)
    elif args.command == "face-enroll":
        profile_id = run_face_enrollment(
            args.config,
            args.database,
            args.role,
            args.name,
            args.profile,
        )
        print(
            "face profile saved: id={} role={} name={} profile={}".format(
                profile_id,
                args.role,
                args.name,
                args.profile,
            )
        )
    elif args.command == "face-verify":
        result = run_face_verification(
            args.config,
            args.database,
            args.profile,
        )
        print(
            "identity={} name={} confirmed={} score={} reason={}".format(
                result.identity,
                result.name or "-",
                result.confirmed,
                "-" if result.score is None else "{:.3f}".format(result.score),
                result.reason,
            )
        )
    elif args.command == "create-admin":
        config = load_config(args.config)
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
            if not password:
                raise SystemExit("标准输入中没有密码")
        else:
            password = getpass.getpass("家长密码（至少 8 个字符）: ")
            second = getpass.getpass("再次输入密码: ")
            if password != second:
                raise SystemExit("两次输入的密码不一致")
        database = Database(args.database)
        database.initialize()
        AuthRepository(database).set_password(
            args.username,
            password,
            datetime.now(ZoneInfo(config.timezone)),
        )
        print("parent login saved: username={}".format(args.username))
    elif args.command == "admin-exists":
        database = Database(args.database)
        database.initialize()
        if not AuthRepository(database).has_user(args.username):
            raise SystemExit(1)
        print("parent login exists: username={}".format(args.username))
    elif args.command == "media-check":
        if args.timeout_seconds <= 0:
            raise SystemExit("--timeout-seconds 必须大于 0")
        try:
            result = probe_media(args.url, args.timeout_seconds)
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        if not result.video_codecs:
            raise SystemExit("摄像头流中没有检测到视频轨道")
        if args.require_audio and not result.audio_codecs:
            raise SystemExit(
                "摄像头流有视频，但没有音频轨道；摄像头收音暂不可用"
            )
        print(
            "media stream ready: video={} audio={}".format(
                ",".join(result.video_codecs),
                ",".join(result.audio_codecs) or "none",
            )
        )
    elif args.command == "web":
        config = load_config(args.config)
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError("uvicorn is required") from exc
        uvicorn.run(
            create_app(args.config, args.database),
            host=config.web.host,
            port=config.web.port,
        )
    elif args.command == "assistant-run":
        run_assistant(args.config, args.database)
    elif args.command == "serve":
        config = load_config(args.config)
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError("uvicorn is required") from exc
        uvicorn.run(
            create_app(args.config, args.database, assistant_enabled=True),
            host=config.web.host,
            port=config.web.port,
        )


def _add_runtime_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="configuration file path",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/funwithcam.db"),
        help="SQLite database path",
    )


if __name__ == "__main__":
    main()


import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from funwithcam.config import load_config
from funwithcam.speech import (
    FINISH,
    PAUSE,
    QUERY_COURSES,
    QUERY_HOMEWORK,
    RESUME,
    SPEECH_COMMAND_GUIDE,
    SpeechCommandHandler,
    START,
    _course_message,
    _homework_message,
    parse_speech_command,
)
from funwithcam.storage.activity_repository import ActivityRepository
from funwithcam.storage.database import Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RejectingPrechecks:
    def request(self, *args, **kwargs):
        raise ValueError("not available")


class SpeechTest(unittest.TestCase):
    def test_parses_supported_commands(self):
        cases = {
            "我 要 开始 写作业": START,
            "我想休息一下": PAUSE,
            "继续学习": RESUME,
            "作业写完了": FINISH,
            "今天有什么作业？": QUERY_HOMEWORK,
            "今天几点上课": QUERY_COURSES,
        }

        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, parse_speech_command(text))

        self.assertIsNone(parse_speech_command("帮我打开窗户"))

    def test_every_documented_phrase_is_recognized(self):
        for item in SPEECH_COMMAND_GUIDE:
            for phrase in item["phrases"]:
                with self.subTest(command=item["command"], phrase=phrase):
                    self.assertEqual(
                        item["command"],
                        parse_speech_command(phrase),
                    )

    def test_builds_homework_and_course_answers(self):
        items = [
            {
                "item_type": "homework",
                "title": "数学练习册",
                "status": "pending",
                "scheduled_time": None,
            },
            {
                "item_type": "course",
                "title": "围棋课",
                "status": "pending",
                "scheduled_time": "18:30",
            },
        ]
        courses = [{"name": "英语课", "start_time": "19:00"}]

        self.assertEqual(
            "小明，今天的作业有数学练习册。",
            _homework_message("小明", items),
        )
        self.assertEqual(
            "小明，今天的课程有18点30分 围棋课、19点 英语课。",
            _course_message("小明", items, courses),
        )

    def test_records_speech_input_output_and_ignored_text(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            config = load_config(
                PROJECT_ROOT / "config" / "config.example.yaml"
            )
            with patch("funwithcam.speech.AudioOutput") as audio_output:
                handler = SpeechCommandHandler(
                    config,
                    database_path,
                    RejectingPrechecks(),
                )
                self.assertTrue(handler.handle("开始学习"))
                self.assertFalse(handler.handle("帮我打开窗户"))

            database = Database(database_path)
            event_date = datetime.now(
                ZoneInfo(config.timezone)
            ).date().isoformat()
            events = ActivityRepository(database).list_for_date(event_date)

        self.assertEqual(3, len(events))
        self.assertEqual(
            {"speech_input", "speech_output"},
            {event["event_type"] for event in events},
        )
        self.assertIn("ignored", {event["status"] for event in events})
        self.assertIn("failed", {event["status"] for event in events})
        audio_output.return_value.speak.assert_called_once()


if __name__ == "__main__":
    unittest.main()

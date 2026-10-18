import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from funwithcam.config import load_config
from funwithcam.speech import (
    FINISH_CHECKIN,
    START,
    SpeechCommandHandler,
    match_speech_intent,
)
from funwithcam.storage.activity_repository import ActivityRepository
from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
from funwithcam.storage.interaction_repository import (
    DEFAULT_SPEECH_PHRASES,
    DialogueRepository,
    SpeechIntentRepository,
    TaskTypeRepository,
    complete_confirmation_prompt,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingPrechecks:
    def __init__(self):
        self.requests = []

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))


class InteractionRepositoryTest(unittest.TestCase):
    def test_confirmation_prompt_includes_spoken_reply_options(self):
        self.assertEqual(
            "你是要开始视频打卡吗？请说“确认打卡”，或者说“取消”。",
            complete_confirmation_prompt(
                "你是要开始视频打卡吗？请回答",
                ["确认打卡", "开始打卡"],
                ["取消", "不是"],
            ),
        )

    def test_speech_intent_configuration_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = SpeechIntentRepository(database)
            now = datetime.now(timezone.utc)

            repository.save(
                "start",
                ["开始作业", "现在开始"],
                [["开始", "准备"], ["作业", "学习"]],
                "always",
                "要开始吗？",
                ["确认开始"],
                ["取消"],
                True,
                now,
            )

            intent = repository.get("start")

        self.assertEqual(
            list(DEFAULT_SPEECH_PHRASES["start"]),
            intent["default_phrases"],
        )
        self.assertEqual(["现在开始"], intent["custom_phrases"])
        self.assertEqual(
            [*DEFAULT_SPEECH_PHRASES["start"], "现在开始"],
            intent["phrases"],
        )
        self.assertEqual(
            [["开始", "准备"], ["作业", "学习"]],
            intent["keyword_groups"],
        )
        self.assertEqual("always", intent["confirmation_mode"])
        self.assertEqual(["确认开始"], intent["confirm_phrases"])
        self.assertEqual(
            "要开始吗？请说“确认开始”，或者说“取消”。",
            intent["confirmation_prompt"],
        )

    def test_default_phrases_remain_when_custom_phrases_are_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = SpeechIntentRepository(database)
            now = datetime.now(timezone.utc)
            existing = repository.get("start")

            repository.save(
                "start",
                [],
                existing["keyword_groups"],
                existing["confirmation_mode"],
                existing["confirmation_prompt"],
                existing["confirm_phrases"],
                existing["cancel_phrases"],
                True,
                now,
            )
            intent = repository.get("start")

        self.assertEqual([], intent["custom_phrases"])
        self.assertEqual(
            list(DEFAULT_SPEECH_PHRASES["start"]),
            intent["phrases"],
        )

    def test_finish_checkin_intent_cannot_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = SpeechIntentRepository(database)
            intent = repository.get("finish_checkin")

            repository.save(
                "finish_checkin",
                ["录好了"],
                intent["keyword_groups"],
                "never",
                "",
                [],
                [],
                False,
                datetime.now(timezone.utc),
            )
            saved = repository.get("finish_checkin")

        self.assertTrue(saved["enabled"])
        self.assertEqual(["录好了"], saved["custom_phrases"])

    def test_dialogue_retries_expire_without_changing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = DialogueRepository(database)
            now = datetime.now(timezone.utc)
            dialogue = repository.start(
                now.date().isoformat(),
                "command_confirmation",
                "waiting_confirmation",
                {"command": "start"},
                "要开始吗？",
                ["确认", "取消"],
                now,
                20,
            )

            self.assertTrue(
                repository.mark_prompted(
                    dialogue["id"],
                    now + timedelta(seconds=20),
                    60,
                    2,
                )
            )
            self.assertFalse(
                repository.mark_prompted(
                    dialogue["id"],
                    now + timedelta(seconds=80),
                    60,
                    2,
                )
            )
            expired = repository.get(dialogue["id"])

        self.assertEqual("expired", expired["status"])


class SpeechInteractionTest(unittest.TestCase):
    def _handler(self, directory):
        config = load_config(
            PROJECT_ROOT / "config" / "config.example.yaml"
        )
        database_path = Path(directory) / "test.db"
        prechecks = RecordingPrechecks()
        audio_patch = patch("funwithcam.speech.AudioOutput")
        audio_output = audio_patch.start()
        self.addCleanup(audio_patch.stop)
        handler = SpeechCommandHandler(config, database_path, prechecks)
        return config, database_path, prechecks, audio_output, handler

    def test_exact_start_executes_but_composite_start_confirms(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, prechecks, _, handler = self._handler(
                directory
            )
            database = Database(database_path)
            dialogues = DialogueRepository(database)

            self.assertTrue(handler.handle("开始作业"))
            self.assertEqual(1, len(prechecks.requests))
            session_date = datetime.now(
                ZoneInfo(config.timezone)
            ).date().isoformat()
            self.assertIsNone(dialogues.get_active(session_date))

            self.assertTrue(handler.handle("准备做作业"))
            self.assertEqual(1, len(prechecks.requests))
            dialogue = dialogues.get_active(session_date)
            self.assertEqual(
                "command_confirmation",
                dialogue["dialogue_type"],
            )

            self.assertTrue(handler.handle("确认开始"))
            self.assertEqual(2, len(prechecks.requests))
            self.assertIsNone(dialogues.get_active(session_date))

    def test_invalid_confirmation_reply_waits_without_speaking_again(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, _, audio_output, handler = self._handler(
                directory
            )
            dialogues = DialogueRepository(Database(database_path))

            self.assertTrue(handler.handle("开始打卡"))
            session_date = datetime.now(
                ZoneInfo(config.timezone)
            ).date().isoformat()
            dialogue = dialogues.get_active(session_date)
            first_retry_at = dialogue["next_prompt_at"]
            self.assertEqual(
                "你是要开始视频打卡吗？请说“确认打卡”，或者说“取消”。",
                dialogue["prompt"],
            )
            audio_output.return_value.speak.reset_mock()

            self.assertTrue(handler.handle("请回答"))

            refreshed = dialogues.get(dialogue["id"])
            audio_output.return_value.speak.assert_not_called()
            self.assertEqual(1, refreshed["ask_count"])
            self.assertEqual(first_retry_at, refreshed["next_prompt_at"])

    def test_conflicting_intents_are_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            intents = SpeechIntentRepository(database).list_enabled()

            match = match_speech_intent("继续学习然后结束学习", intents)

        self.assertIsNone(match.command)
        self.assertEqual("conflict", match.quality)

    def test_finish_reviews_verbal_task_before_finishing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, _, _, handler = self._handler(directory)
            database = Database(database_path)
            family = FamilyRepository(database)
            handler._sessions = MagicMock()
            now = datetime.now(ZoneInfo(config.timezone))
            item_id = family.add_agenda(
                now.date(),
                "homework",
                "数学练习册",
                "数学",
                None,
                30,
                0,
                "",
                now,
            )
            skipped_item_id = family.add_agenda(
                now.date(),
                "homework",
                "语文抄写",
                "语文",
                None,
                20,
                0,
                "",
                now,
                "school_homework",
                False,
            )

            handler.handle("结束学习")
            handler.handle("检查完成情况")
            self.assertFalse(handler._sessions.finish.called)

            handler.handle("完成了")

            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            skipped_item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == skipped_item_id
            )
            self.assertEqual("done", item["status"])
            self.assertEqual(
                "child_confirmed",
                item["completion_evidence"],
            )
            self.assertEqual("pending", skipped_item["status"])
            self.assertEqual("none", skipped_item["completion_evidence"])
            handler._sessions.finish.assert_called_once()

    def test_video_task_cannot_be_completed_by_verbal_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, _, _, handler = self._handler(directory)
            database = Database(database_path)
            family = FamilyRepository(database)
            dialogues = DialogueRepository(database)
            handler._sessions = MagicMock()
            now = datetime.now(ZoneInfo(config.timezone))
            item_id = family.add_agenda(
                now.date(),
                "homework",
                "英语读书打卡",
                "英语",
                None,
                10,
                0,
                "",
                now,
                "english_reading",
            )

            handler.handle("结束学习")
            handler.handle("检查完成情况")
            handler.handle("完成了")

            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            self.assertEqual("pending", item["status"])
            self.assertEqual("none", item["completion_evidence"])
            self.assertIsNotNone(
                dialogues.get_active(now.date().isoformat())
            )
            self.assertFalse(handler._sessions.finish.called)

            handler.handle("稍后完成")
            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            self.assertEqual("later", item["completion_evidence"])
            handler._sessions.finish.assert_called_once()

    def test_parent_task_requires_parent_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, _, _, handler = self._handler(directory)
            database = Database(database_path)
            family = FamilyRepository(database)
            task_types = TaskTypeRepository(database)
            handler._sessions = MagicMock()
            now = datetime.now(ZoneInfo(config.timezone))
            task_types.save(
                "parent_review",
                "家长检查",
                "other",
                "parent",
                "请家长确认“{title}”。",
                True,
                None,
                True,
                now,
            )
            item_id = family.add_agenda(
                now.date(),
                "temporary",
                "检查书包",
                None,
                None,
                None,
                0,
                "",
                now,
                "parent_review",
            )

            handler.handle("结束学习")
            handler.handle("检查完成情况")
            handler.handle("完成了")

            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            self.assertEqual("pending", item["status"])
            self.assertEqual("none", item["completion_evidence"])

    def test_overdue_video_is_saved_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, _, _, handler = self._handler(directory)
            database = Database(database_path)
            family = FamilyRepository(database)
            dialogues = DialogueRepository(database)
            handler._recorder = MagicMock()
            handler._recorder.stop.return_value = 6.2
            now = datetime.now(ZoneInfo(config.timezone))
            item_id = family.add_agenda(
                now.date(),
                "homework",
                "英语单词打卡",
                "英语",
                None,
                10,
                0,
                "",
                now,
                "english_words",
            )
            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            handler._prepare_checkin(item, now)
            dialogue = dialogues.get_active(now.date().isoformat())
            context = dialogue["context"]
            context["started_at"] = (
                now - timedelta(seconds=6)
            ).isoformat()
            context["max_recording_seconds"] = 5
            dialogues.update(
                dialogue["id"],
                "recording",
                context,
                "录制中",
                ["打卡结束", "取消打卡"],
                now,
                None,
            )

            handler._finish_overdue_checkin(now)

            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            events = ActivityRepository(database).list_for_date(
                now.date().isoformat()
            )
            self.assertEqual("video", item["completion_evidence"])
            self.assertIn(
                "checkin_saved",
                {event["event_type"] for event in events},
            )
            self.assertIsNone(
                dialogues.get_active(now.date().isoformat())
            )

    def test_custom_finish_checkin_phrase_stops_and_saves_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            config, database_path, _, _, handler = self._handler(directory)
            database = Database(database_path)
            family = FamilyRepository(database)
            intents = SpeechIntentRepository(database)
            dialogues = DialogueRepository(database)
            now = datetime.now(ZoneInfo(config.timezone))
            finish_intent = intents.get(FINISH_CHECKIN)
            intents.save(
                FINISH_CHECKIN,
                ["录好了"],
                finish_intent["keyword_groups"],
                "never",
                "",
                [],
                [],
                True,
                now,
            )
            item_id = family.add_agenda(
                now.date(),
                "homework",
                "英语单词打卡",
                "英语",
                None,
                10,
                0,
                "",
                now,
                "english_words",
            )
            item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            handler._recorder = MagicMock()
            handler._recorder.stop.return_value = 8.4

            handler._prepare_checkin(item, now)
            self.assertTrue(handler.handle("开始打卡"))
            self.assertTrue(handler.handle("录好了"))

            saved_item = next(
                item
                for item in family.list_agenda(now.date())
                if item["id"] == item_id
            )
            handler._recorder.stop.assert_called_once()
            self.assertEqual(
                {"save": True},
                handler._recorder.stop.call_args.kwargs,
            )
            self.assertEqual("video", saved_item["completion_evidence"])
            self.assertIsNone(
                dialogues.get_active(now.date().isoformat())
            )


if __name__ == "__main__":
    unittest.main()

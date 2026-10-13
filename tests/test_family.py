import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from funwithcam.storage.auth_repository import AuthRepository
from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
from funwithcam.storage.interaction_repository import TaskTypeRepository


class AuthRepositoryTest(unittest.TestCase):
    def test_password_and_session_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = AuthRepository(database)
            now = datetime(2026, 10, 11, tzinfo=timezone.utc)
            self.assertFalse(repository.has_user("parent"))
            repository.set_password("parent", "long-password", now)
            self.assertTrue(repository.has_user("parent"))

            self.assertIsNone(
                repository.create_session("parent", "wrong-password", now, 24)
            )
            token = repository.create_session(
                "parent",
                "long-password",
                now,
                24,
            )
            session = repository.get_session(token, now)

        self.assertIsNotNone(token)
        self.assertEqual("parent", session.username)
        self.assertTrue(session.csrf_token)


class FamilyRepositoryTest(unittest.TestCase):
    def test_agenda_course_and_preferences_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = FamilyRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            today = date(2026, 10, 11)

            item_id = repository.add_agenda(
                today,
                "homework",
                "数学练习册",
                "数学",
                None,
                30,
                0,
                "",
                now,
            )
            repository.set_agenda_status(item_id, "done", now)
            repository.add_weekly_course(
                "英语课",
                today.isoweekday(),
                "19:00",
                "offline",
                10,
                20,
                now,
            )
            repository.save_daily_preferences(
                today,
                "rest_day",
                "custom",
                "10:30",
                True,
                False,
                now,
            )

            items = repository.list_agenda(today)
            courses = repository.list_courses_for_date(today)
            preferences = repository.get_daily_preferences(today)

        self.assertEqual("done", items[0]["status"])
        self.assertEqual("英语课", courses[0]["name"])
        self.assertEqual("10:30", preferences["second_reminder_time"])
        self.assertEqual(0, preferences["posture_reminders_enabled"])

    def test_default_settings_are_present(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()

            settings = FamilyRepository(database).get_settings()

        self.assertEqual("17:00", settings["first_briefing_weekday_time"])
        self.assertEqual("18:10", settings["second_reminder_weekday_time"])
        self.assertEqual("小朋友", settings["child_name"])
        self.assertEqual("15", settings["observation_interval_seconds"])
        self.assertEqual("3", settings["posture_trigger_count"])

    def test_child_name_can_be_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = FamilyRepository(database)

            repository.save_settings(
                {"child_name": " 小明 "},
                datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc),
            )

            settings = repository.get_settings()

        self.assertEqual("小明", settings["child_name"])

    def test_finish_check_can_inherit_and_override_task_type_default(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = FamilyRepository(database)
            task_types = TaskTypeRepository(database)
            now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
            today = date(2026, 10, 11)

            item_id = repository.add_agenda(
                today,
                "homework",
                "数学练习册",
                "数学",
                None,
                30,
                0,
                "",
                now,
            )
            inherited = repository.list_agenda(today)[0]

            task_types.save(
                "school_homework",
                "学校作业",
                "school_homework",
                "verbal",
                "“{title}”完成了吗？",
                False,
                None,
                True,
                now,
            )
            disabled_by_default = repository.list_agenda(today)[0]

            repository.set_finish_check_enabled(item_id, True, now)
            enabled_for_today = repository.list_agenda(today)[0]

            repository.set_finish_check_enabled(item_id, False, now)
            disabled_for_today = repository.list_agenda(today)[0]

        self.assertEqual(1, inherited["include_in_finish_check"])
        self.assertEqual(0, disabled_by_default["include_in_finish_check"])
        self.assertEqual(1, enabled_for_today["include_in_finish_check"])
        self.assertEqual(0, disabled_for_today["include_in_finish_check"])


if __name__ == "__main__":
    unittest.main()

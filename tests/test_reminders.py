import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from funwithcam.reminders import (
    build_briefing_message,
    build_reminder_plan,
    build_second_reminder_message,
)
from funwithcam.storage.database import Database
from funwithcam.storage.reminder_repository import ReminderRepository


TIMEZONE = ZoneInfo("Asia/Shanghai")
SETTINGS = {
    "first_briefing_weekday_time": "17:00",
    "first_briefing_rest_day_time": "09:00",
    "second_reminder_weekday_time": "18:10",
    "second_reminder_rest_day_time": "09:30",
}


class ReminderPlanTest(unittest.TestCase):
    def test_uses_weekday_defaults(self):
        target_date = date(2026, 10, 12)
        plan = build_reminder_plan(
            target_date,
            TIMEZONE,
            {
                "day_type": "auto",
                "second_reminder_mode": "default",
                "second_reminder_time": None,
                "homework_reminders_enabled": 1,
            },
            SETTINGS,
        )

        self.assertEqual("school_day", plan.day_type)
        self.assertEqual("17:00", plan.first_briefing_at.strftime("%H:%M"))
        self.assertEqual("18:10", plan.second_reminder_at.strftime("%H:%M"))

    def test_today_can_disable_second_reminder(self):
        plan = build_reminder_plan(
            date(2026, 10, 11),
            TIMEZONE,
            {
                "day_type": "rest_day",
                "second_reminder_mode": "disabled",
                "second_reminder_time": None,
                "homework_reminders_enabled": 1,
            },
            SETTINGS,
        )

        self.assertIsNone(plan.second_reminder_at)

    def test_briefing_contains_course_homework_and_next_time(self):
        target_date = date(2026, 10, 12)
        plan = build_reminder_plan(
            target_date,
            TIMEZONE,
            {
                "day_type": "school_day",
                "second_reminder_mode": "default",
                "second_reminder_time": None,
                "homework_reminders_enabled": 1,
            },
            SETTINGS,
        )
        message = build_briefing_message(
            [
                {
                    "item_type": "homework",
                    "title": "数学练习册",
                    "status": "pending",
                    "scheduled_time": None,
                    "preparation_minutes": 0,
                }
            ],
            [
                {
                    "name": "英语课",
                    "start_time": "19:00",
                    "preparation_minutes": 10,
                }
            ],
            plan,
            datetime(2026, 10, 12, 17, 0, tzinfo=TIMEZONE),
            "小明",
        )

        self.assertTrue(message.startswith("小明，"))
        self.assertIn("19点有英语课", message)
        self.assertIn("数学练习册", message)
        self.assertIn("18点10分再提醒你", message)

    def test_second_message_does_not_claim_voice_input_is_active(self):
        message = build_second_reminder_message(
            [
                {
                    "item_type": "homework",
                    "title": "语文背诵",
                    "status": "pending",
                }
            ],
            datetime(2026, 10, 12, 18, 10, tzinfo=TIMEZONE),
            "小明",
        )

        self.assertEqual(
            "小明，现在是18点10分。今天还有语文背诵，可以开始准备了。",
            message,
        )


class ReminderRepositoryTest(unittest.TestCase):
    def test_records_first_and_second_reminder_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = ReminderRepository(database)
            first = datetime(2026, 10, 12, 17, 5, tzinfo=TIMEZONE)
            second = datetime(2026, 10, 12, 18, 10, tzinfo=TIMEZONE)

            repository.mark_briefed(
                "2026-10-12",
                first,
                first,
                "今日简报",
            )
            repository.mark_second_reminder(
                "2026-10-12",
                second,
                second,
                "played",
                "第二次提醒",
            )
            status = repository.get_daily_status("2026-10-12")
            with database.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM reminders"
                ).fetchone()[0]

        self.assertEqual(first.isoformat(), status["briefed_at"])
        self.assertEqual(second.isoformat(), status["second_reminder_at"])
        self.assertEqual(2, count)


if __name__ == "__main__":
    unittest.main()

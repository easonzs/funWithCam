import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from funwithcam.storage.database import Database
from funwithcam.storage.family_repository import FamilyRepository
from funwithcam.storage.interaction_repository import (
    ScheduleRepository,
    TaskTypeRepository,
)


def repo_pair(directory):
    database = Database(Path(directory) / "test.db")
    database.initialize()
    schedule = ScheduleRepository(database)
    task_types = TaskTypeRepository(database)
    family = FamilyRepository(database)
    return database, schedule, task_types, family


def make_template(task_types, now, task_key="english_words", label="英语单词打卡"):
    task_types.save(
        task_key,
        label,
        "checkin",
        "video",
        "{}还没有视频，要现在打卡吗？".format(label),
        True,
        180,
        True,
        now,
    )
    return task_key


class ScheduleRepositoryTest(unittest.TestCase):
    def test_get_returns_template_by_key(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, task_types, _ = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)

            found = task_types.get("english_words")
            self.assertIsNotNone(found)
            self.assertEqual("英语单词打卡", found["label"])
            self.assertIsNone(task_types.get("missing_key"))

    def test_dates_round_trip_and_add_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            database, schedule, task_types, _ = repo_pair(directory)
            now = datetime.now(timezone.utc)
            key = make_template(task_types, now)

            schedule.set_dates(key, ["2026-12-01", "2026-12-03"], now)
            self.assertEqual(["2026-12-01", "2026-12-03"], schedule.list_dates(key))

            schedule.add_date(key, "2026-12-05", now)
            schedule.remove_date(key, "2026-12-01")
            self.assertEqual(
                ["2026-12-03", "2026-12-05"],
                schedule.list_dates(key),
            )

    def test_active_for_date_hits_scheduled_date(self):
        with tempfile.TemporaryDirectory() as directory:
            _, schedule, task_types, _ = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)
            schedule.set_dates("english_words", ["2026-12-03"], now)

            active = schedule.active_for_date("2026-12-03")

            self.assertEqual(1, len(active))
            self.assertEqual("english_words", active[0]["task_key"])
            self.assertEqual("checkin", active[0]["item_type"])
            self.assertEqual("template", active[0]["source"])
            self.assertEqual("英语单词打卡", active[0]["title"])

    def test_override_disabled_supersedes_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            _, schedule, task_types, _ = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)
            schedule.set_dates("english_words", ["2026-12-03"], now)
            schedule.set_override(
                "english_words",
                "2026-12-03",
                disabled=True,
                override_title=None,
                notes="",
                now=now,
            )

            self.assertEqual([], schedule.active_for_date("2026-12-03"))

    def test_override_title_sets_days_title(self):
        with tempfile.TemporaryDirectory() as directory:
            _, schedule, task_types, _ = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)
            schedule.set_dates("english_words", ["2026-12-03"], now)
            schedule.set_override(
                "english_words",
                "2026-12-03",
                disabled=False,
                override_title="英语单词：复习U3",
                notes="带课本",
                now=now,
            )

            active = schedule.active_for_date("2026-12-03")

            self.assertEqual("英语单词：复习U3", active[0]["title"])
            self.assertEqual("带课本", active[0]["notes"])

    def test_list_agenda_merges_template_items(self):
        with tempfile.TemporaryDirectory() as directory:
            database, schedule, task_types, family = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)
            schedule.set_dates("english_words", ["2026-12-03"], now)

            items = family.list_agenda(datetime(2026, 12, 3).date())

            self.assertEqual(1, len(items))
            self.assertEqual("english_words", items[0]["task_type_key"])
            self.assertEqual("英语单词打卡", items[0]["title"])
            self.assertEqual("template", items[0]["source"])

    def test_template_item_recycled_when_date_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            database, schedule, task_types, family = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)
            schedule.set_dates("english_words", ["2026-12-03"], now)
            family.list_agenda(datetime(2026, 12, 3).date())
            self.assertEqual(1, len(family.list_agenda(datetime(2026, 12, 3).date())))

            schedule.set_dates("english_words", [], now)
            self.assertEqual(0, len(family.list_agenda(datetime(2026, 12, 3).date())))

    def test_user_finish_check_override_survives_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            database, schedule, task_types, family = repo_pair(directory)
            now = datetime.now(timezone.utc)
            make_template(task_types, now)
            schedule.set_dates("english_words", ["2026-12-03"], now)

            day = datetime(2026, 12, 3).date()
            items = family.list_agenda(day)
            item_id = items[0]["id"]
            self.assertEqual(
                1, items[0]["include_in_finish_check"],
                "模板默认开启结束时询问",
            )
            # 用户手动关闭「结束时询问」
            family.set_finish_check_enabled(item_id, False, now)
            self.assertEqual(
                0,
                family.list_agenda(day)[0]["include_in_finish_check"],
            )
            # 再次访问不因模板默认值覆盖用户的设置
            self.assertEqual(
                0,
                family.list_agenda(day)[0]["include_in_finish_check"],
            )


if __name__ == "__main__":
    unittest.main()
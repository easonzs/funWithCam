from datetime import date, datetime
from typing import Dict, List, Optional

from .database import Database


DEFAULT_SETTINGS = {
    "child_name": "小朋友",
    "first_briefing_weekday_time": "17:00",
    "first_briefing_rest_day_time": "09:00",
    "second_reminder_weekday_time": "18:10",
    "second_reminder_rest_day_time": "09:30",
    "observation_interval_seconds": "15",
    "posture_trigger_count": "3",
    "posture_trigger_window_minutes": "5",
    "posture_reminder_cooldown_minutes": "10",
    "light_trigger_count": "2",
    "light_trigger_window_minutes": "5",
    "light_reminder_cooldown_minutes": "10",
    "seat_leave_trigger_count": "3",
    "seat_leave_trigger_window_minutes": "10",
    "seat_leave_reminder_cooldown_minutes": "10",
    "dialogue_first_retry_seconds": "20",
    "dialogue_next_retry_seconds": "60",
    "dialogue_max_prompts": "3",
    "away_auto_pause_minutes": "2",
    "away_interrupted_minutes": "60",
}
REMINDER_RULE_SETTINGS = frozenset(
    {
        "observation_interval_seconds",
        "posture_trigger_count",
        "posture_trigger_window_minutes",
        "posture_reminder_cooldown_minutes",
        "light_trigger_count",
        "light_trigger_window_minutes",
        "light_reminder_cooldown_minutes",
        "seat_leave_trigger_count",
        "seat_leave_trigger_window_minutes",
        "seat_leave_reminder_cooldown_minutes",
        "dialogue_first_retry_seconds",
        "dialogue_next_retry_seconds",
        "dialogue_max_prompts",
        "away_auto_pause_minutes",
        "away_interrupted_minutes",
    }
)


class FamilyRepository:
    def __init__(self, database: Database):
        self._database = database

    def list_agenda(self, item_date: date) -> List[dict]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    agenda_items.*,
                    task_types.label AS task_type_label,
                    task_types.category AS task_category,
                    task_types.completion_mode,
                    task_types.finish_prompt,
                    task_types.include_in_finish_check
                        AS default_finish_check_enabled,
                    COALESCE(
                        agenda_items.finish_check_enabled,
                        task_types.include_in_finish_check,
                        0
                    ) AS include_in_finish_check,
                    task_types.max_recording_seconds
                FROM agenda_items
                LEFT JOIN task_types
                    ON task_types.task_key = agenda_items.task_type_key
                WHERE item_date = ?
                ORDER BY
                    CASE WHEN scheduled_time IS NULL THEN 1 ELSE 0 END,
                    scheduled_time,
                    id
                """,
                (item_date.isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_agenda(
        self,
        item_date: date,
        item_type: str,
        title: str,
        subject: Optional[str],
        scheduled_time: Optional[str],
        estimated_minutes: Optional[int],
        preparation_minutes: int,
        notes: str,
        now: datetime,
        task_type_key: Optional[str] = None,
        finish_check_enabled: Optional[bool] = None,
    ) -> int:
        if item_type not in ("homework", "course", "temporary"):
            raise ValueError("invalid agenda item type")
        if not title.strip():
            raise ValueError("title cannot be empty")
        if scheduled_time is not None:
            _validate_time(scheduled_time)
        if item_type == "homework" and task_type_key is None:
            task_type_key = "school_homework"
        if task_type_key is not None:
            with self._database.connection() as connection:
                task_type = connection.execute(
                    """
                    SELECT task_key FROM task_types
                    WHERE task_key = ? AND enabled = 1
                    """,
                    (task_type_key,),
                ).fetchone()
            if task_type is None:
                raise ValueError("invalid task type")
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO agenda_items (
                    item_date,
                    item_type,
                    title,
                    subject,
                    scheduled_time,
                    estimated_minutes,
                    preparation_minutes,
                    notes,
                    task_type_key,
                    finish_check_enabled,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_date.isoformat(),
                    item_type,
                    title.strip(),
                    _optional_text(subject),
                    scheduled_time or None,
                    estimated_minutes,
                    max(0, preparation_minutes),
                    notes.strip(),
                    task_type_key,
                    (
                        None
                        if finish_check_enabled is None
                        else int(finish_check_enabled)
                    ),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def set_agenda_status(
        self,
        item_id: int,
        status: str,
        now: datetime,
    ) -> None:
        if status not in ("pending", "done"):
            raise ValueError("invalid agenda status")
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE agenda_items
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, now.isoformat(), item_id),
            )
            connection.commit()

    def set_completion_evidence(
        self,
        item_id: int,
        evidence: str,
        now: datetime,
    ) -> None:
        allowed = {
            "none",
            "child_confirmed",
            "video",
            "parent_confirmed",
            "later",
            "skipped",
            "unanswered",
        }
        if evidence not in allowed:
            raise ValueError("invalid completion evidence")
        status = (
            "done"
            if evidence in {"child_confirmed", "video", "parent_confirmed"}
            else "pending"
        )
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE agenda_items
                SET status = ?, completion_evidence = ?, confirmed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    evidence,
                    (
                        now.isoformat()
                        if evidence
                        in {"child_confirmed", "video", "parent_confirmed"}
                        else None
                    ),
                    now.isoformat(),
                    item_id,
                ),
            )
            connection.commit()

    def set_finish_check_enabled(
        self,
        item_id: int,
        enabled: bool,
        now: datetime,
    ) -> None:
        with self._database.connection() as connection:
            updated = connection.execute(
                """
                UPDATE agenda_items
                SET finish_check_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(enabled), now.isoformat(), item_id),
            )
            if not updated.rowcount:
                raise ValueError("agenda item not found")
            connection.commit()

    def list_finish_check_items(self, item_date: date) -> List[dict]:
        return [
            item
            for item in self.list_agenda(item_date)
            if item["status"] == "pending"
            and item["item_type"] != "course"
            and bool(item.get("include_in_finish_check"))
        ]

    def delete_agenda(self, item_id: int) -> None:
        with self._database.connection() as connection:
            connection.execute("DELETE FROM agenda_items WHERE id = ?", (item_id,))
            connection.commit()

    def list_weekly_courses(self) -> List[dict]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM weekly_courses
                ORDER BY weekday, start_time, id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_courses_for_date(self, item_date: date) -> List[dict]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM weekly_courses
                WHERE weekday = ? AND enabled = 1
                ORDER BY start_time, id
                """,
                (item_date.isoweekday(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_weekly_course(
        self,
        name: str,
        weekday: int,
        start_time: str,
        delivery_type: str,
        preparation_minutes: int,
        travel_minutes: int,
        now: datetime,
    ) -> int:
        if not name.strip():
            raise ValueError("course name cannot be empty")
        if weekday not in range(1, 8):
            raise ValueError("weekday must be between 1 and 7")
        if delivery_type not in ("online", "offline"):
            raise ValueError("invalid delivery type")
        _validate_time(start_time)
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO weekly_courses (
                    name,
                    weekday,
                    start_time,
                    delivery_type,
                    preparation_minutes,
                    travel_minutes,
                    enabled,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    name.strip(),
                    weekday,
                    start_time,
                    delivery_type,
                    max(0, preparation_minutes),
                    max(0, travel_minutes),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def set_course_enabled(
        self,
        course_id: int,
        enabled: bool,
        now: datetime,
    ) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE weekly_courses
                SET enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(enabled), now.isoformat(), course_id),
            )
            connection.commit()

    def delete_weekly_course(self, course_id: int) -> None:
        with self._database.connection() as connection:
            connection.execute(
                "DELETE FROM weekly_courses WHERE id = ?",
                (course_id,),
            )
            connection.commit()

    def get_daily_preferences(self, preference_date: date) -> dict:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM daily_preferences
                WHERE preference_date = ?
                """,
                (preference_date.isoformat(),),
            ).fetchone()
        if row is not None:
            return dict(row)
        return {
            "preference_date": preference_date.isoformat(),
            "day_type": "auto",
            "second_reminder_mode": "default",
            "second_reminder_time": None,
            "homework_reminders_enabled": 1,
            "posture_reminders_enabled": 1,
        }

    def save_daily_preferences(
        self,
        preference_date: date,
        day_type: str,
        second_reminder_mode: str,
        second_reminder_time: Optional[str],
        homework_reminders_enabled: bool,
        posture_reminders_enabled: bool,
        now: datetime,
    ) -> None:
        if day_type not in ("auto", "school_day", "rest_day"):
            raise ValueError("invalid day type")
        if second_reminder_mode not in ("default", "custom", "disabled"):
            raise ValueError("invalid second reminder mode")
        if second_reminder_mode == "custom":
            _validate_time(second_reminder_time)
        else:
            second_reminder_time = None
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO daily_preferences (
                    preference_date,
                    day_type,
                    second_reminder_mode,
                    second_reminder_time,
                    homework_reminders_enabled,
                    posture_reminders_enabled,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(preference_date) DO UPDATE SET
                    day_type = excluded.day_type,
                    second_reminder_mode = excluded.second_reminder_mode,
                    second_reminder_time = excluded.second_reminder_time,
                    homework_reminders_enabled = excluded.homework_reminders_enabled,
                    posture_reminders_enabled = excluded.posture_reminders_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    preference_date.isoformat(),
                    day_type,
                    second_reminder_mode,
                    second_reminder_time,
                    int(homework_reminders_enabled),
                    int(posture_reminders_enabled),
                    now.isoformat(),
                ),
            )
            connection.commit()

    def get_settings(self) -> Dict[str, str]:
        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT setting_key, setting_value FROM family_settings"
            ).fetchall()
        values = dict(DEFAULT_SETTINGS)
        values.update({row["setting_key"]: row["setting_value"] for row in rows})
        return values

    def save_settings(self, values: Dict[str, str], now: datetime) -> None:
        unknown = set(values) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError("unknown family settings: {}".format(sorted(unknown)))
        values = dict(values)
        if "child_name" in values:
            values["child_name"] = values["child_name"].strip()
            if not values["child_name"]:
                raise ValueError("child name cannot be empty")
        for key, value in values.items():
            if key.endswith("_time"):
                _validate_time(value)
            elif key in REMINDER_RULE_SETTINGS:
                _validate_positive_integer(value, key)
        with self._database.connection() as connection:
            for key, value in values.items():
                connection.execute(
                    """
                    INSERT INTO family_settings (
                        setting_key,
                        setting_value,
                        updated_at
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, now.isoformat()),
                )
            connection.commit()


def _validate_time(value: Optional[str]) -> None:
    if value is None:
        raise ValueError("time is required")
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("time must use HH:MM") from exc


def _validate_positive_integer(value: str, key: str) -> None:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must be a positive integer".format(key)) from exc
    if number <= 0:
        raise ValueError("{} must be a positive integer".format(key))


def _optional_text(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    return value.strip()

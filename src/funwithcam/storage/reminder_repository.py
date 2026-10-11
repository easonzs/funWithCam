import json
from datetime import datetime
from typing import Optional

from funwithcam.domain.states import WorkflowState

from .database import Database


class ReminderRepository:
    def __init__(self, database: Database):
        self._database = database

    def get_daily_status(self, session_date: str) -> dict:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT briefed_at, second_reminder_at, disabled_at, workflow_state
                FROM daily_status
                WHERE session_date = ?
                """,
                (session_date,),
            ).fetchone()
        if row is None:
            return {
                "briefed_at": None,
                "second_reminder_at": None,
                "disabled_at": None,
                "workflow_state": WorkflowState.WAITING_FOR_FIRST_APPEARANCE.value,
            }
        return dict(row)

    def mark_briefed(
        self,
        session_date: str,
        first_seen_at: datetime,
        played_at: datetime,
        message: str,
    ) -> None:
        timestamp = played_at.isoformat()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO daily_status (
                    session_date,
                    workflow_state,
                    first_seen_at,
                    briefed_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_date) DO UPDATE SET
                    workflow_state = excluded.workflow_state,
                    first_seen_at = COALESCE(daily_status.first_seen_at, excluded.first_seen_at),
                    briefed_at = excluded.briefed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    session_date,
                    WorkflowState.WAITING_TO_START.value,
                    first_seen_at.isoformat(),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            self._insert_reminder(
                connection,
                session_date,
                "first_briefing",
                None,
                played_at,
                played_at,
                "played",
                {"message": message},
            )
            connection.commit()

    def mark_second_reminder(
        self,
        session_date: str,
        scheduled_at: datetime,
        recorded_at: datetime,
        status: str,
        message: Optional[str],
    ) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE daily_status
                SET second_reminder_at = ?, updated_at = ?
                WHERE session_date = ?
                """,
                (
                    recorded_at.isoformat(),
                    recorded_at.isoformat(),
                    session_date,
                ),
            )
            self._insert_reminder(
                connection,
                session_date,
                "second_reminder",
                scheduled_at,
                recorded_at if status == "played" else None,
                recorded_at,
                status,
                {} if message is None else {"message": message},
            )
            connection.commit()

    def _insert_reminder(
        self,
        connection,
        session_date: str,
        reminder_type: str,
        scheduled_at: Optional[datetime],
        played_at: Optional[datetime],
        recorded_at: datetime,
        status: str,
        payload: dict,
    ) -> None:
        timestamp = recorded_at.isoformat()
        connection.execute(
            """
            INSERT OR IGNORE INTO reminders (
                session_date,
                reminder_type,
                scheduled_at,
                played_at,
                status,
                payload_json,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_date,
                reminder_type,
                None if scheduled_at is None else scheduled_at.isoformat(),
                None if played_at is None else played_at.isoformat(),
                status,
                json.dumps(payload, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )

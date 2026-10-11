from datetime import datetime
from typing import Optional

from funwithcam.domain.events import EventType
from funwithcam.domain.state_machine import WorkflowSnapshot, transition
from funwithcam.domain.states import WorkflowState

from .database import Database


class StudySessionRepository:
    def __init__(self, database: Database):
        self._database = database

    def get_latest(self, session_date: str) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM study_sessions
                WHERE session_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_date,),
            ).fetchone()
        return None if row is None else dict(row)

    def start(self, session_date: str, source: str, now: datetime) -> int:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = self._load_snapshot(connection, session_date)
            snapshot = transition(snapshot, EventType.START_REQUESTED)
            snapshot = transition(snapshot, EventType.PRECHECK_PASSED)
            if self._active_session(connection, session_date) is not None:
                raise ValueError("已有进行中的学习记录")

            cursor = connection.execute(
                """
                INSERT INTO study_sessions (
                    session_date,
                    state,
                    started_at,
                    start_source,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_date,
                    snapshot.state.value,
                    timestamp,
                    source,
                    timestamp,
                    timestamp,
                ),
            )
            session_id = int(cursor.lastrowid)
            self._open_interval(connection, session_id, "STUDYING", timestamp)
            self._save_snapshot(connection, session_date, snapshot, timestamp)
            reminder_update = connection.execute(
                """
                UPDATE daily_status
                SET second_reminder_at = ?, updated_at = ?
                WHERE session_date = ? AND second_reminder_at IS NULL
                """,
                (timestamp, timestamp, session_date),
            )
            if reminder_update.rowcount:
                connection.execute(
                    """
                    INSERT INTO reminders (
                        session_date,
                        study_session_id,
                        reminder_type,
                        status,
                        cancellation_reason,
                        payload_json,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, 'second_reminder', 'cancelled',
                              'study_started', '{}', ?, ?)
                    """,
                    (session_date, session_id, timestamp, timestamp),
                )
            connection.commit()
        return session_id

    def pause(self, session_date: str, now: datetime) -> None:
        self._change_state(
            session_date,
            now,
            EventType.PAUSE_REQUESTED,
            "PAUSED",
        )

    def resume(self, session_date: str, now: datetime) -> None:
        self._change_state(
            session_date,
            now,
            EventType.RESUME_REQUESTED,
            "STUDYING",
            pass_precheck=True,
        )

    def finish(self, session_date: str, now: datetime) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = self._load_snapshot(connection, session_date)
            snapshot = transition(snapshot, EventType.FINISH_REQUESTED)
            snapshot = transition(snapshot, EventType.FINISH_CONFIRMED)
            session = self._require_active_session(connection, session_date)
            self._close_interval(connection, int(session["id"]), timestamp)
            connection.execute(
                """
                UPDATE study_sessions
                SET state = ?, finished_at = ?, completion_status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    snapshot.state.value,
                    timestamp,
                    "completed",
                    timestamp,
                    session["id"],
                ),
            )
            self._save_snapshot(connection, session_date, snapshot, timestamp)
            connection.commit()

    def _change_state(
        self,
        session_date: str,
        now: datetime,
        event_type: EventType,
        interval_type: str,
        pass_precheck: bool = False,
    ) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = self._load_snapshot(connection, session_date)
            snapshot = transition(snapshot, event_type)
            if pass_precheck:
                snapshot = transition(snapshot, EventType.PRECHECK_PASSED)
            session = self._require_active_session(connection, session_date)
            session_id = int(session["id"])
            self._close_interval(connection, session_id, timestamp)
            self._open_interval(connection, session_id, interval_type, timestamp)
            connection.execute(
                """
                UPDATE study_sessions
                SET state = ?, updated_at = ?
                WHERE id = ?
                """,
                (snapshot.state.value, timestamp, session_id),
            )
            self._save_snapshot(connection, session_date, snapshot, timestamp)
            connection.commit()

    def _load_snapshot(self, connection, session_date: str) -> WorkflowSnapshot:
        row = connection.execute(
            """
            SELECT workflow_state, return_state
            FROM daily_status
            WHERE session_date = ?
            """,
            (session_date,),
        ).fetchone()
        if row is None:
            raise ValueError("请先完成今天的首次简报")
        return WorkflowSnapshot(
            state=WorkflowState(row["workflow_state"]),
            return_state=(
                None
                if row["return_state"] is None
                else WorkflowState(row["return_state"])
            ),
        )

    def _save_snapshot(
        self,
        connection,
        session_date: str,
        snapshot: WorkflowSnapshot,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            UPDATE daily_status
            SET workflow_state = ?, return_state = ?, updated_at = ?
            WHERE session_date = ?
            """,
            (
                snapshot.state.value,
                (
                    None
                    if snapshot.return_state is None
                    else snapshot.return_state.value
                ),
                timestamp,
                session_date,
            ),
        )

    def _active_session(self, connection, session_date: str):
        return connection.execute(
            """
            SELECT *
            FROM study_sessions
            WHERE session_date = ? AND finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_date,),
        ).fetchone()

    def _require_active_session(self, connection, session_date: str):
        session = self._active_session(connection, session_date)
        if session is None:
            raise ValueError("今天没有进行中的学习记录")
        return session

    def _open_interval(
        self,
        connection,
        session_id: int,
        interval_type: str,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO session_intervals (
                study_session_id,
                interval_type,
                started_at,
                created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (session_id, interval_type, timestamp, timestamp),
        )

    def _close_interval(
        self,
        connection,
        session_id: int,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            UPDATE session_intervals
            SET ended_at = ?
            WHERE study_session_id = ? AND ended_at IS NULL
            """,
            (timestamp, session_id),
        )

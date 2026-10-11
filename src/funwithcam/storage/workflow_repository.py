from datetime import datetime
from typing import Optional

from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState

from .database import Database


class WorkflowRepository:
    def __init__(self, database: Database):
        self._database = database

    def load(self, session_date: str) -> Optional[WorkflowSnapshot]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT workflow_state, return_state
                FROM daily_status
                WHERE session_date = ?
                """,
                (session_date,),
            ).fetchone()
        if row is None:
            return None
        return WorkflowSnapshot(
            state=WorkflowState(row["workflow_state"]),
            return_state=(
                None
                if row["return_state"] is None
                else WorkflowState(row["return_state"])
            ),
        )

    def save(
        self,
        session_date: str,
        snapshot: WorkflowSnapshot,
        updated_at: datetime,
    ) -> None:
        timestamp = updated_at.isoformat()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO daily_status (
                    session_date,
                    workflow_state,
                    return_state,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_date) DO UPDATE SET
                    workflow_state = excluded.workflow_state,
                    return_state = excluded.return_state,
                    updated_at = excluded.updated_at
                """,
                (
                    session_date,
                    snapshot.state.value,
                    (
                        None
                        if snapshot.return_state is None
                        else snapshot.return_state.value
                    ),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()


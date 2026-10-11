import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState
from funwithcam.storage.database import Database
from funwithcam.storage.study_session_repository import StudySessionRepository
from funwithcam.storage.workflow_repository import WorkflowRepository


class StudySessionRepositoryTest(unittest.TestCase):
    def test_start_pause_resume_and_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            workflow = WorkflowRepository(database)
            sessions = StudySessionRepository(database)
            started_at = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
            workflow.save(
                "2026-10-12",
                WorkflowSnapshot(WorkflowState.WAITING_TO_START),
                started_at,
            )

            session_id = sessions.start("2026-10-12", "web", started_at)
            sessions.pause(
                "2026-10-12",
                started_at + timedelta(minutes=20),
            )
            sessions.resume(
                "2026-10-12",
                started_at + timedelta(minutes=25),
            )
            sessions.finish(
                "2026-10-12",
                started_at + timedelta(minutes=45),
            )

            latest = sessions.get_latest("2026-10-12")
            snapshot = workflow.load("2026-10-12")
            with database.connection() as connection:
                intervals = connection.execute(
                    """
                    SELECT interval_type, started_at, ended_at
                    FROM session_intervals
                    WHERE study_session_id = ?
                    ORDER BY id
                    """,
                    (session_id,),
                ).fetchall()
                cancellation = connection.execute(
                    """
                    SELECT status, cancellation_reason
                    FROM reminders
                    WHERE study_session_id = ?
                    """,
                    (session_id,),
                ).fetchone()

        self.assertEqual("COMPLETED", latest["state"])
        self.assertEqual("completed", latest["completion_status"])
        self.assertEqual(WorkflowState.COMPLETED, snapshot.state)
        self.assertEqual(
            ["STUDYING", "PAUSED", "STUDYING"],
            [row["interval_type"] for row in intervals],
        )
        self.assertTrue(all(row["ended_at"] for row in intervals))
        self.assertEqual("cancelled", cancellation["status"])
        self.assertEqual("study_started", cancellation["cancellation_reason"])

    def test_completed_day_can_start_another_session(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            workflow = WorkflowRepository(database)
            sessions = StudySessionRepository(database)
            now = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
            workflow.save(
                "2026-10-12",
                WorkflowSnapshot(WorkflowState.WAITING_TO_START),
                now,
            )

            sessions.start("2026-10-12", "web", now)
            sessions.finish("2026-10-12", now + timedelta(minutes=20))
            second_id = sessions.start(
                "2026-10-12",
                "web",
                now + timedelta(minutes=30),
            )
            latest = sessions.get_latest("2026-10-12")

        self.assertEqual(second_id, latest["id"])


if __name__ == "__main__":
    unittest.main()

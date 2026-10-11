import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState
from funwithcam.storage.activity_repository import ActivityRepository
from funwithcam.storage.calibration_repository import CalibrationRepository
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

            sessions.request_start("2026-10-12", started_at)
            self.assertIsNone(sessions.get_latest("2026-10-12"))
            session_id = sessions.confirm_precheck(
                "2026-10-12",
                "web",
                started_at + timedelta(seconds=5),
            )
            sessions.pause(
                "2026-10-12",
                started_at + timedelta(minutes=20),
            )
            sessions.request_resume(
                "2026-10-12",
                started_at + timedelta(minutes=25),
            )
            sessions.confirm_precheck(
                "2026-10-12",
                "web",
                started_at + timedelta(minutes=25, seconds=5),
            )
            sessions.finish(
                "2026-10-12",
                started_at + timedelta(minutes=45),
            )

            latest = sessions.get_latest("2026-10-12")
            snapshot = workflow.load("2026-10-12")
            activities = ActivityRepository(database).list_for_date(
                "2026-10-12"
            )
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
        self.assertEqual(
            {"study_paused", "study_finished"},
            {event["event_type"] for event in activities},
        )

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

            sessions.request_start("2026-10-12", now)
            sessions.confirm_precheck(
                "2026-10-12",
                "web",
                now + timedelta(seconds=5),
            )
            sessions.finish("2026-10-12", now + timedelta(minutes=20))
            sessions.request_start(
                "2026-10-12",
                now + timedelta(minutes=30),
            )
            second_id = sessions.confirm_precheck(
                "2026-10-12",
                "web",
                now + timedelta(minutes=30, seconds=5),
            )
            latest = sessions.get_latest("2026-10-12")

        self.assertEqual(second_id, latest["id"])

    def test_list_recent_counts_only_studying_intervals(self):
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

            sessions.request_start("2026-10-12", now)
            sessions.confirm_precheck(
                "2026-10-12",
                "web",
                now + timedelta(seconds=5),
            )
            sessions.pause(
                "2026-10-12",
                now + timedelta(minutes=20),
            )
            sessions.request_resume(
                "2026-10-12",
                now + timedelta(minutes=25),
            )
            sessions.confirm_precheck(
                "2026-10-12",
                "web",
                now + timedelta(minutes=25, seconds=5),
            )
            sessions.finish(
                "2026-10-12",
                now + timedelta(minutes=45),
            )

            recent = sessions.list_recent()

        self.assertEqual(1, len(recent))
        self.assertEqual("2026-10-12", recent[0]["session_date"])
        self.assertAlmostEqual(39.8333, recent[0]["study_minutes"], places=3)

    def test_cancelled_precheck_does_not_create_session(self):
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

            sessions.request_start("2026-10-12", now)
            self.assertEqual(
                WorkflowState.PRECHECK,
                workflow.load("2026-10-12").state,
            )
            sessions.cancel_precheck(
                "2026-10-12",
                now + timedelta(seconds=5),
            )

            self.assertIsNone(sessions.get_latest("2026-10-12"))
            self.assertEqual(
                WorkflowState.WAITING_TO_START,
                workflow.load("2026-10-12").state,
            )

    def test_active_calibration_blocks_starting_study(self):
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
            CalibrationRepository(database).create_draft(now)

            with self.assertRaisesRegex(ValueError, "基准正在录入"):
                sessions.request_start("2026-10-12", now)

    def test_new_session_records_latest_calibration_version(self):
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
            with database.connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO calibration_versions (
                        calibration_json, quality_json, created_at
                    ) VALUES ('{}', '{}', ?)
                    """,
                    (now.isoformat(),),
                )
                version_id = int(cursor.lastrowid)
                connection.commit()

            sessions.request_start("2026-10-12", now)
            sessions.confirm_precheck(
                "2026-10-12",
                "web",
                now + timedelta(seconds=5),
            )
            latest = sessions.get_latest("2026-10-12")

        self.assertEqual(version_id, latest["calibration_version_id"])


if __name__ == "__main__":
    unittest.main()

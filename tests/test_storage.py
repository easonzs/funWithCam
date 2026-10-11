import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from funwithcam.domain.state_machine import WorkflowSnapshot
from funwithcam.domain.states import WorkflowState
from funwithcam.storage.database import Database
from funwithcam.storage.workflow_repository import WorkflowRepository


class DatabaseTest(unittest.TestCase):
    def test_initial_migration_creates_expected_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()

            with database.connection() as connection:
                tables = {
                    row["name"]
                    for row in connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    ).fetchall()
                }

            self.assertTrue(
                {
                    "daily_status",
                    "study_sessions",
                    "session_intervals",
                    "observation_events",
                    "reminders",
                    "config_versions",
                    "calibration_versions",
                    "face_profiles",
                }.issubset(tables)
            )

    def test_workflow_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = WorkflowRepository(database)
            snapshot = WorkflowSnapshot(
                state=WorkflowState.FINISH_CONFIRMING,
                return_state=WorkflowState.STUDYING,
            )

            repository.save(
                "2026-10-11",
                snapshot,
                datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(snapshot, repository.load("2026-10-11"))

    def test_database_initialization_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            database.initialize()

            with database.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations"
                ).fetchone()[0]
            self.assertEqual(3, count)


if __name__ == "__main__":
    unittest.main()


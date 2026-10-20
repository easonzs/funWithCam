import tempfile
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
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
                    "calibration_drafts",
                    "face_profiles",
                    "activity_events",
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
            with database.connection() as connection:
                again = connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations"
                ).fetchone()[0]
        self.assertGreaterEqual(count, 16)
        self.assertEqual(count, again)

    def test_concurrent_connection_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()

            def open_and_close_connection():
                with database.connection() as connection:
                    return connection.execute("SELECT 1").fetchone()[0]

            with ThreadPoolExecutor(max_workers=12) as executor:
                results = list(executor.map(
                    lambda _: open_and_close_connection(),
                    range(48),
                ))

        self.assertEqual([1] * 48, results)


if __name__ == "__main__":
    unittest.main()


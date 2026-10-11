import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from funwithcam.storage.activity_repository import ActivityRepository
from funwithcam.storage.database import Database


class ActivityRepositoryTest(unittest.TestCase):
    def test_records_lists_filters_and_limits_events(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = ActivityRepository(database)
            earlier = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
            later = datetime(2026, 10, 11, 17, 5, tzinfo=timezone.utc)

            repository.record(
                earlier,
                "recognition",
                "child_confirmed",
                "camera",
                "success",
                "首次识别到孩子",
                payload={"matching_frames": 3},
            )
            repository.record(
                later,
                "speech",
                "speech_input",
                "microphone",
                "info",
                "识别到语音口令",
                "开始学习",
                {"command": "start"},
            )

            events = repository.list_for_date("2026-10-11")
            speech = repository.list_for_date(
                "2026-10-11",
                category="speech",
            )
            limited = repository.list_for_date("2026-10-11", limit=1)

        self.assertEqual(["speech", "recognition"], [item["category"] for item in events])
        self.assertEqual("start", speech[0]["payload"]["command"])
        self.assertEqual(1, len(limited))

    def test_rejects_non_positive_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = ActivityRepository(database)

            with self.assertRaises(ValueError):
                repository.list_for_date("2026-10-11", limit=0)


if __name__ == "__main__":
    unittest.main()

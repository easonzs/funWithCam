import unittest
from datetime import datetime, timezone

from funwithcam.config import PersonConfig, PresenceConfig, StudyAreaConfig
from funwithcam.models import PersonObservation
from funwithcam.presence import PresenceMonitor


class PresenceMonitorTest(unittest.TestCase):
    def setUp(self):
        self.monitor = PresenceMonitor(
            StudyAreaConfig(rect=(0.2, 0.2, 0.8, 0.9)),
            PersonConfig(
                min_detection_confidence=0.6,
                min_bbox_area_ratio=0.05,
                max_bbox_area_ratio=0.5,
            ),
            PresenceConfig(
                confirm_seconds=5,
                allowed_missed_seconds=1,
                max_center_jump=0.2,
            ),
        )

    def observation(self, seconds, **overrides):
        values = {
            "observed_at": datetime(2026, 10, 11, tzinfo=timezone.utc),
            "monotonic_at": float(seconds),
            "detected": True,
            "confidence": 0.9,
            "center_x": 0.5,
            "center_y": 0.5,
            "bbox_area_ratio": 0.2,
        }
        values.update(overrides)
        return PersonObservation(**values)

    def test_confirms_after_stable_presence(self):
        result = None
        for seconds in range(6):
            result = self.monitor.update(self.observation(seconds))
        self.assertIsNotNone(result)
        self.assertTrue(result.confirmed)

    def test_long_miss_resets_candidate(self):
        self.monitor.update(self.observation(0))
        self.monitor.update(self.observation(1))
        self.monitor.update(self.observation(3, detected=False))
        result = self.monitor.update(self.observation(4))
        self.assertEqual(0, result.stable_seconds)
        self.assertFalse(result.confirmed)


if __name__ == "__main__":
    unittest.main()


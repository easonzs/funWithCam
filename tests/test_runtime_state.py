import threading
import unittest

from funwithcam.runtime_state import (
    calibration_active,
    set_assistant_paused,
    set_assistant_running,
    set_calibration_active,
    wait_for_assistant_pause,
)


class RuntimeStateTest(unittest.TestCase):
    def tearDown(self):
        set_calibration_active(False)
        set_assistant_running(False)

    def test_calibration_waits_for_running_assistant_to_pause(self):
        set_assistant_running(True)
        self.assertFalse(wait_for_assistant_pause(0.01))
        set_assistant_paused(True)
        self.assertTrue(wait_for_assistant_pause(0.1))

    def test_no_assistant_does_not_block_calibration(self):
        set_calibration_active(True)
        self.assertTrue(calibration_active())
        self.assertTrue(wait_for_assistant_pause(0.01))


if __name__ == "__main__":
    unittest.main()

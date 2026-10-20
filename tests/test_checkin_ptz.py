import unittest
from unittest.mock import MagicMock, patch

from funwithcam.checkin_ptz import (
    CameraPtz,
    CameraPosition,
    CheckinPtzConfig,
    CheckinPtzController,
)


def make_config(**overrides):
    values = {
        "enabled": True,
        "tilt_steps": 3,
        "step_delay_seconds": 0,
        "restore_tolerance": 2,
        "restore_max_attempts": 3,
    }
    values.update(overrides)
    return CheckinPtzConfig(**values)


class CameraPtzTest(unittest.TestCase):
    def test_move_uses_motor_operation_codes(self):
        ptz = CameraPtz("http://go2rtc:1984", "study_room")
        with patch("funwithcam.checkin_ptz.urlopen") as urlopen:
            ptz.move("down")
        request = urlopen.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertIn(
            "src=study_room", request.full_url
        )
        self.assertIn("operation=4", request.full_url)

    def test_read_position_parses_response(self):
        ptz = CameraPtz("http://go2rtc:1984", "study_room")
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"{}"
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        with patch(
            "funwithcam.checkin_ptz.urlopen",
            return_value=response,
        ) as urlopen, patch(
            "funwithcam.checkin_ptz.json.load",
            return_value={"angle": 50, "elevation": 60, "ret": 0},
        ):
            position = ptz.read_position()

        request = urlopen.call_args.args[0]
        self.assertEqual("GET", request.method)
        self.assertIn("read=1", request.full_url)
        self.assertEqual(CameraPosition(50, 60), position)

    def test_read_position_raises_on_missing_fields(self):
        ptz = CameraPtz("http://go2rtc:1984", "study_room")
        response = MagicMock()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        with patch(
            "funwithcam.checkin_ptz.urlopen",
            return_value=response,
        ), patch(
            "funwithcam.checkin_ptz.json.load",
            return_value={"angle": 50},
        ):
            with self.assertRaises(RuntimeError):
                ptz.read_position()


class CheckinPtzControllerTest(unittest.TestCase):
    def test_begin_records_baseline_and_tilts_down(self):
        config = make_config(tilt_steps=3)
        controller = CheckinPtzController(config, "http://go2rtc:1984", "study_room")
        controller._ptz = MagicMock()
        controller._ptz.read_position.return_value = CameraPosition(50, 60)

        result = controller.begin()

        self.assertTrue(result.success)
        self.assertEqual("tilted", result.details)
        self.assertEqual(CameraPosition(50, 60), controller._baseline)
        self.assertEqual(
            3,
            controller._ptz.move.call_count,
        )
        controller._ptz.move.assert_called_with("down")

    def test_begin_degrades_silently_when_ptz_unavailable(self):
        config = make_config(tilt_steps=3)
        controller = CheckinPtzController(config, "http://go2rtc:1984", "study_room")
        controller._ptz = MagicMock()
        controller._ptz.read_position.side_effect = RuntimeError("no stream")

        result = controller.begin()

        self.assertFalse(result.success)
        self.assertEqual("unavailable", result.details)
        self.assertIsNone(controller._baseline)

    def test_begin_not_enabled_when_config_disabled(self):
        controller = CheckinPtzController(
            make_config(enabled=False), "http://go2rtc:1984", "study_room"
        )
        controller._ptz = MagicMock()

        result = controller.begin()

        self.assertTrue(result.success)
        self.assertEqual("not_enabled", result.details)
        controller._ptz.move.assert_not_called()

    def test_restore_returns_to_baseline_within_tolerance(self):
        controller = CheckinPtzController(
            make_config(tilt_steps=2, restore_tolerance=2),
            "http://go2rtc:1984",
            "study_room",
        )
        controller._ptz = MagicMock()
        controller._baseline = CameraPosition(50, 60)
        # 第一次读回：仰角 59，已在容差 2 内 → 复位完成，无需移动。
        controller._ptz.read_position.return_value = CameraPosition(50, 59)

        result = controller.restore()

        self.assertTrue(result.success)
        self.assertEqual("restored", result.details)
        controller._ptz.move.assert_not_called()

    def test_restore_error_driven_reaches_baseline(self):
        controller = CheckinPtzController(
            make_config(tilt_steps=1, restore_tolerance=2),
            "http://go2rtc:1984",
            "study_room",
        )
        controller._ptz = MagicMock()
        controller._baseline = CameraPosition(50, 60)
        # 误差驱动：仰角高于基准则向下，第二轮读回即到位。
        controller._ptz.read_position.side_effect = [
            CameraPosition(50, 63),
            CameraPosition(50, 59),
        ]

        result = controller.restore()

        self.assertTrue(result.success)
        self.assertEqual("restored", result.details)
        controller._ptz.move.assert_called_once_with("down")

    def test_restore_fails_after_max_attempts(self):
        controller = CheckinPtzController(
            make_config(tilt_steps=1, restore_tolerance=0, restore_max_attempts=3),
            "http://go2rtc:1984",
            "study_room",
        )
        controller._ptz = MagicMock()
        controller._baseline = CameraPosition(50, 60)
        # 一直回不到基准仰角 60 → 3 轮后判定失败。
        controller._ptz.read_position.return_value = CameraPosition(50, 62)

        result = controller.restore()

        self.assertFalse(result.success)
        self.assertEqual("restore_failed", result.details)
        # 每轮先读、再 down，3 轮共 3 次移动。
        self.assertEqual(
            3,
            controller._ptz.move.call_count,
        )
        self.assertEqual(
            {"down"},
            {call.args[0] for call in controller._ptz.move.call_args_list},
        )

    def test_restore_not_enabled_when_config_disabled(self):
        controller = CheckinPtzController(
            make_config(enabled=False), "http://go2rtc:1984", "study_room"
        )
        controller._baseline = CameraPosition(50, 60)

        result = controller.restore()

        self.assertTrue(result.success)
        self.assertEqual("not_enabled", result.details)


if __name__ == "__main__":
    unittest.main()

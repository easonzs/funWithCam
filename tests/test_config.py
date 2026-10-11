import tempfile
import unittest
from pathlib import Path

from funwithcam.config import load_config


VALID_CONFIG = """
timezone: Asia/Shanghai
video:
  stream_url: rtsp://127.0.0.1:8554/study_room
  inference_fps: 4
  reconnect_seconds: 3
  max_frame_age_seconds: 2
  warmup_seconds: 1.5
study_area:
  rect: [0.2, 0.15, 0.8, 0.95]
person:
  min_detection_confidence: 0.6
  min_bbox_area_ratio: 0.03
  max_bbox_area_ratio: 0.65
presence:
  confirm_seconds: 5
  allowed_missed_seconds: 1
  max_center_jump: 0.2
precheck:
  duration_seconds: 5
  timeout_seconds: 15
  min_valid_frame_ratio: 0.8
  min_brightness: 45
audio:
  command: ["say", "{text}"]
  camera_test_text: "测试成功"
speech:
  enabled: false
  model_path: models/sherpa-zh
  sample_rate: 48000
  block_size: 8000
  device:
  command_cooldown_seconds: 2
identity:
  detector_model: models/yunet.onnx
  recognizer_model: models/sface.onnx
  detection_threshold: 0.9
  nms_threshold: 0.3
  min_face_size_px: 80
  match_threshold: 0.45
  ambiguity_margin: 0.08
  confirmation_frames: 3
  confirmation_window_seconds: 3
  enrollment_samples: 9
  sample_interval_seconds: 0.7
  command_timeout_seconds: 60
  save_debug_frames: true
  debug_dir: data/face_debug
  debug_face_limit: 30
  debug_rejected_limit: 10
web:
  host: 0.0.0.0
  port: 8000
  session_hours: 24
logging:
  level: DEBUG
"""


class ConfigTest(unittest.TestCase):
    def test_loads_valid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(VALID_CONFIG, encoding="utf-8")

            config = load_config(path)

        self.assertEqual(4, config.video.inference_fps)
        self.assertEqual(1.5, config.video.warmup_seconds)
        self.assertEqual((0.2, 0.15, 0.8, 0.95), config.study_area.rect)
        self.assertEqual(5, config.precheck.duration_seconds)
        self.assertEqual(45, config.precheck.min_brightness)
        self.assertEqual(3, config.identity.confirmation_frames)
        self.assertEqual("yunet.onnx", config.identity.detector_model.name)
        self.assertTrue(config.identity.save_debug_frames)
        self.assertEqual("face_debug", config.identity.debug_dir.name)
        self.assertFalse(config.speech.enabled)
        self.assertEqual("sherpa-zh", config.speech.model_path.name)
        self.assertEqual(8000, config.web.port)
        self.assertEqual("DEBUG", config.log_level)

    def test_rejects_invalid_area_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                VALID_CONFIG.replace(
                    "rect: [0.2, 0.15, 0.8, 0.95]",
                    "rect: [0.8, 0.15, 0.2, 0.95]",
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_config(path)

    def test_uses_precheck_defaults_for_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            start = VALID_CONFIG.index("precheck:")
            end = VALID_CONFIG.index("audio:")
            path.write_text(
                VALID_CONFIG[:start] + VALID_CONFIG[end:],
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(5, config.precheck.duration_seconds)
        self.assertEqual(15, config.precheck.timeout_seconds)
        self.assertEqual(0.8, config.precheck.min_valid_frame_ratio)
        self.assertEqual(45, config.precheck.min_brightness)


if __name__ == "__main__":
    unittest.main()


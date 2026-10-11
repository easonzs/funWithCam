import unittest

import numpy

from funwithcam.precheck import (
    FrameCheck,
    calculate_brightness,
    summarize_checks,
)


class PrecheckTest(unittest.TestCase):
    def test_passes_stable_bright_single_person_frames(self):
        result = summarize_checks(
            [
                FrameCheck(80, True, "eligible", 1, True),
                FrameCheck(90, True, "eligible", 1, True),
                FrameCheck(100, True, "eligible", 1, True),
                FrameCheck(85, True, "eligible", 1, True),
            ],
            attempts=5,
            min_valid_frame_ratio=0.8,
            min_brightness=45,
        )

        self.assertTrue(result.passed)
        self.assertEqual("passed", result.reason)
        self.assertEqual(0.8, result.valid_frame_ratio)

    def test_rejects_missing_frames_darkness_and_outside_area(self):
        cases = (
            (
                [FrameCheck(80, True, "eligible", 1, True)],
                5,
                "video_unavailable",
            ),
            (
                [FrameCheck(20, True, "eligible", 1, True)] * 5,
                5,
                "too_dark",
            ),
            (
                [
                    FrameCheck(
                        80,
                        False,
                        "outside_study_area",
                        1,
                        True,
                    )
                ]
                * 5,
                5,
                "outside_study_area",
            ),
        )
        for checks, attempts, expected in cases:
            with self.subTest(expected=expected):
                result = summarize_checks(checks, attempts, 0.8, 45)
                self.assertFalse(result.passed)
                self.assertEqual(expected, result.reason)

    def test_rejects_any_multiple_face_frame(self):
        result = summarize_checks(
            [FrameCheck(80, True, "eligible", 1, True)] * 4
            + [FrameCheck(80, True, "eligible", 2, False)],
            attempts=5,
            min_valid_frame_ratio=0.8,
            min_brightness=45,
        )

        self.assertFalse(result.passed)
        self.assertEqual("multiple_people", result.reason)

    def test_rejects_person_who_is_not_verified_as_child(self):
        result = summarize_checks(
            [FrameCheck(80, True, "eligible", 1, False)] * 5,
            attempts=5,
            min_valid_frame_ratio=0.8,
            min_brightness=45,
        )

        self.assertFalse(result.passed)
        self.assertEqual("child_not_verified", result.reason)

    def test_calculates_brightness_inside_study_area(self):
        frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
        frame[1:3, 1:3] = 100

        brightness = calculate_brightness(frame, (0.25, 0.25, 0.75, 0.75))

        self.assertAlmostEqual(100, brightness, places=3)

if __name__ == "__main__":
    unittest.main()

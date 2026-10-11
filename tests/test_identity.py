import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from funwithcam.identity.debug_frames import FaceDebugFrames
from funwithcam.identity.matcher import (
    FaceTemplate,
    FrameMatch,
    IdentityMatcher,
    MultiFrameVerifier,
    average_embeddings,
)
from funwithcam.identity.repository import FaceProfileRepository
from funwithcam.identity.vision import FaceDetectionResult
from funwithcam.storage.database import Database


class IdentityMatcherTest(unittest.TestCase):
    def test_matches_child_above_threshold(self):
        matcher = IdentityMatcher(
            [
                FaceTemplate(1, "child", "child", (1.0, 0.0)),
                FaceTemplate(2, "parent", "parent_1", (0.0, 1.0)),
            ],
            match_threshold=0.8,
            ambiguity_margin=0.1,
        )

        result = matcher.match((0.99, 0.01))

        self.assertEqual("child", result.identity)
        self.assertEqual("child", result.name)

    def test_returns_unknown_when_child_and_parent_are_too_close(self):
        matcher = IdentityMatcher(
            [
                FaceTemplate(1, "child", "child", (1.0, 0.0)),
                FaceTemplate(2, "parent", "parent_1", (0.98, 0.2)),
            ],
            match_threshold=0.8,
            ambiguity_margin=0.1,
        )

        result = matcher.match((1.0, 0.0))

        self.assertEqual("unknown", result.identity)
        self.assertEqual("ambiguous", result.reason)

    def test_requires_multiple_consistent_frames(self):
        verifier = MultiFrameVerifier(required_frames=3, window_seconds=2)
        match = FrameMatch("child", "child", 0.9, "matched")

        self.assertFalse(verifier.update(match, 1.0).confirmed)
        self.assertFalse(verifier.update(match, 1.5).confirmed)
        result = verifier.update(match, 2.0)

        self.assertTrue(result.confirmed)
        self.assertEqual("child", result.identity)

    def test_conflicting_known_identity_prevents_confirmation(self):
        verifier = MultiFrameVerifier(required_frames=2, window_seconds=3)
        verifier.update(FrameMatch("child", "child", 0.9, "matched"), 1.0)
        result = verifier.update(
            FrameMatch("parent", "parent_1", 0.9, "matched"),
            1.5,
        )

        self.assertFalse(result.confirmed)
        self.assertEqual("conflicting_frames", result.reason)

    def test_averages_and_normalizes_enrollment_samples(self):
        result = average_embeddings(((1.0, 0.0), (0.8, 0.2)))

        self.assertAlmostEqual(1.0, sum(value * value for value in result))


class FaceProfileRepositoryTest(unittest.TestCase):
    def test_test_and_formal_profiles_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = FaceProfileRepository(database)
            now = datetime(2026, 10, 11, tzinfo=timezone.utc)
            repository.replace("child", "child", "test", (1.0, 0.0), now)
            repository.replace("child", "child", "formal", (0.0, 1.0), now)

            test_profiles = repository.list_active("test")
            formal_profiles = repository.list_active("formal")

        self.assertEqual((1.0, 0.0), test_profiles[0].embedding)
        self.assertEqual((0.0, 1.0), formal_profiles[0].embedding)

    def test_reenrollment_replaces_existing_template(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            repository = FaceProfileRepository(database)
            now = datetime(2026, 10, 11, tzinfo=timezone.utc)
            first_id = repository.replace(
                "parent",
                "parent_1",
                "formal",
                (1.0, 0.0),
                now,
            )
            second_id = repository.replace(
                "parent",
                "parent_1",
                "formal",
                (0.0, 1.0),
                now,
            )

            profiles = repository.list_active("formal")

        self.assertEqual(first_id, second_id)
        self.assertEqual(1, len(profiles))
        self.assertEqual((0.0, 1.0), profiles[0].embedding)


class FaceDebugFramesTest(unittest.TestCase):
    def test_saves_full_aligned_rejected_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            debug = FaceDebugFrames(
                enabled=True,
                root=root,
                run_name="face-verify-test",
                face_limit=5,
                rejected_limit=2,
                started_at=datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc),
            )
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            aligned = np.zeros((112, 112, 3), dtype=np.uint8)
            face = np.array(
                [
                    50,
                    40,
                    80,
                    100,
                    70,
                    70,
                    110,
                    70,
                    90,
                    90,
                    75,
                    115,
                    105,
                    115,
                    0.99,
                ],
                dtype=np.float32,
            )
            ready = FaceDetectionResult(
                face=face,
                faces=(face,),
                confidence=0.99,
                face_size=(80, 100),
                reason="face_ready",
            )
            missing = FaceDetectionResult(
                face=None,
                faces=(),
                confidence=None,
                face_size=None,
                reason="no_face",
            )

            debug.save_face(
                frame,
                ready,
                aligned,
                category="matched",
                context="formal",
                identity="child",
                score=0.72,
                match_reason="matched",
            )
            debug.save_rejected(frame, missing, 1.0, "formal")
            debug.finish({"status": "completed"})

            files = {path.name for path in debug.run_dir.iterdir()}
            manifest = json.loads(
                (debug.run_dir / "manifest.json").read_text(encoding="utf-8")
            )

        self.assertIn("001_matched_face_ready_full.jpg", files)
        self.assertIn("001_matched_face_ready_aligned.jpg", files)
        self.assertIn("002_rejected_no_face_full.jpg", files)
        self.assertEqual(2, len(manifest["frames"]))
        self.assertEqual("child", manifest["frames"][0]["identity"])
        self.assertEqual("matched", manifest["frames"][0]["match_reason"])


if __name__ == "__main__":
    unittest.main()

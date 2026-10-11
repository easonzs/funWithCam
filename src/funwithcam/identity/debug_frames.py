import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .vision import FaceDetectionResult


class FaceDebugFrames:
    def __init__(
        self,
        enabled: bool,
        root: Path,
        run_name: str,
        face_limit: int,
        rejected_limit: int,
        started_at: datetime,
    ):
        self.run_dir: Optional[Path] = None
        self._face_limit = face_limit
        self._face_count = 0
        self._rejected_limit = rejected_limit
        self._rejected_count = 0
        self._last_rejected_at: Optional[float] = None
        self._next_index = 1
        self._records = []
        if enabled:
            timestamp = started_at.strftime("%Y%m%d_%H%M%S")
            self.run_dir = root / "{}_{}".format(run_name, timestamp)
            self.run_dir.mkdir(parents=True, exist_ok=True)

    def save_rejected(
        self,
        frame,
        detection: FaceDetectionResult,
        captured_monotonic_at: float,
        context: str,
    ) -> None:
        if self.run_dir is None or self._rejected_count >= self._rejected_limit:
            return
        if (
            self._last_rejected_at is not None
            and captured_monotonic_at - self._last_rejected_at < 0.5
        ):
            return
        self._last_rejected_at = captured_monotonic_at
        self._rejected_count += 1
        self._save(
            frame,
            detection,
            aligned=None,
            category="rejected",
            context=context,
            identity=None,
            score=None,
        )

    def save_face(
        self,
        frame,
        detection: FaceDetectionResult,
        aligned,
        category: str,
        context: str,
        identity: Optional[str] = None,
        score: Optional[float] = None,
        match_reason: Optional[str] = None,
    ) -> None:
        if self.run_dir is None or self._face_count >= self._face_limit:
            return
        self._face_count += 1
        self._save(
            frame,
            detection,
            aligned,
            category,
            context,
            identity,
            score,
            match_reason,
        )

    def finish(self, result: dict) -> None:
        if self.run_dir is None:
            return
        payload = {
            "result": result,
            "frames": self._records,
        }
        (self.run_dir / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save(
        self,
        frame,
        detection: FaceDetectionResult,
        aligned,
        category: str,
        context: str,
        identity: Optional[str],
        score: Optional[float],
        match_reason: Optional[str] = None,
    ) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-contrib-python is required") from exc

        index = self._next_index
        self._next_index += 1
        stem = "{:03d}_{}_{}".format(index, category, detection.reason)
        full_name = stem + "_full.jpg"
        aligned_name = None
        annotated = frame.copy()
        color = (70, 190, 80) if detection.face is not None else (40, 150, 240)
        for face in detection.faces:
            x, y, width, height = (int(value) for value in face[:4])
            cv2.rectangle(
                annotated,
                (x, y),
                (x + width, y + height),
                color,
                2,
            )
            for point_index in range(5):
                point_x = int(face[4 + point_index * 2])
                point_y = int(face[5 + point_index * 2])
                cv2.circle(annotated, (point_x, point_y), 2, color, -1)

        label_parts = [category, detection.reason, context]
        if detection.confidence is not None:
            label_parts.append("det={:.3f}".format(detection.confidence))
        if identity is not None:
            label_parts.append("id={}".format(identity))
        if score is not None:
            label_parts.append("match={:.3f}".format(score))
        if match_reason is not None:
            label_parts.append("reason={}".format(match_reason))
        cv2.putText(
            annotated,
            " | ".join(label_parts),
            (16, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
            cv2.LINE_AA,
        )

        if not cv2.imwrite(
            str(self.run_dir / full_name),
            annotated,
            [cv2.IMWRITE_JPEG_QUALITY, 92],
        ):
            raise RuntimeError("unable to save face debug frame")
        if aligned is not None:
            aligned_name = stem + "_aligned.jpg"
            if not cv2.imwrite(
                str(self.run_dir / aligned_name),
                aligned,
                [cv2.IMWRITE_JPEG_QUALITY, 95],
            ):
                raise RuntimeError("unable to save aligned face debug frame")

        self._records.append(
            {
                "index": index,
                "category": category,
                "context": context,
                "detection_reason": detection.reason,
                "detection_confidence": detection.confidence,
                "face_size": detection.face_size,
                "identity": identity,
                "match_score": score,
                "match_reason": match_reason,
                "full_frame": full_name,
                "aligned_face": aligned_name,
            }
        )

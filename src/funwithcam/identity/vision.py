from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .matcher import normalize_embedding


@dataclass(frozen=True)
class FaceDetectionResult:
    face: Optional[object]
    faces: Tuple[object, ...]
    confidence: Optional[float]
    face_size: Optional[Tuple[int, int]]
    reason: str


class FaceDetector:
    def __init__(
        self,
        model_path: Path,
        detection_threshold: float,
        nms_threshold: float,
        min_face_size_px: int,
    ):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-contrib-python is required") from exc
        if not model_path.is_file():
            raise FileNotFoundError("face detector model not found: {}".format(model_path))

        self._detector = cv2.FaceDetectorYN_create(
            str(model_path),
            "",
            (320, 320),
            detection_threshold,
            nms_threshold,
            5000,
        )
        self._min_face_size_px = min_face_size_px

    def detect_one(self, frame) -> FaceDetectionResult:
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(frame)
        if faces is None or len(faces) == 0:
            return FaceDetectionResult(
                face=None,
                faces=(),
                confidence=None,
                face_size=None,
                reason="no_face",
            )
        if len(faces) > 1:
            return FaceDetectionResult(
                face=None,
                faces=tuple(faces),
                confidence=None,
                face_size=None,
                reason="multiple_faces",
            )

        face = faces[0]
        face_width = int(face[2])
        face_height = int(face[3])
        confidence = float(face[14])
        if min(face_width, face_height) < self._min_face_size_px:
            return FaceDetectionResult(
                face=None,
                faces=(face,),
                confidence=confidence,
                face_size=(face_width, face_height),
                reason="face_too_small",
            )
        return FaceDetectionResult(
            face=face,
            faces=(face,),
            confidence=confidence,
            face_size=(face_width, face_height),
            reason="face_ready",
        )


class FaceRecognizer:
    def __init__(self, model_path: Path):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-contrib-python is required") from exc
        if not model_path.is_file():
            raise FileNotFoundError(
                "face recognizer model not found: {}".format(model_path)
            )
        self._recognizer = cv2.FaceRecognizerSF_create(str(model_path), "")

    def extract(self, frame, face) -> Tuple[float, ...]:
        return self.extract_aligned(self.align(frame, face))

    def align(self, frame, face):
        return self._recognizer.alignCrop(frame, face)

    def extract_aligned(self, aligned) -> Tuple[float, ...]:
        feature = self._recognizer.feature(aligned)
        return normalize_embedding(feature.reshape(-1))

from datetime import datetime

from .models import PersonObservation


class PosePresenceDetector:
    """Initial adapter; can be replaced by Pose Landmarker without changing callers."""

    def __init__(self, min_detection_confidence: float):
        try:
            import cv2
            import mediapipe as mp
        except ImportError as exc:
            raise RuntimeError(
                "mediapipe and opencv-contrib-python are required"
            ) from exc

        self._cv2 = cv2
        self._mp = mp
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            enable_segmentation=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_detection_confidence,
        )

    def detect(
        self,
        frame,
        observed_at: datetime,
        monotonic_at: float,
    ) -> PersonObservation:
        rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        result = self._pose.process(rgb)
        if result.pose_landmarks is None:
            return PersonObservation(
                observed_at=observed_at,
                monotonic_at=monotonic_at,
                detected=False,
            )

        landmarks = result.pose_landmarks.landmark
        pose_landmark = self._mp.solutions.pose.PoseLandmark
        shoulders = (
            landmarks[pose_landmark.LEFT_SHOULDER.value],
            landmarks[pose_landmark.RIGHT_SHOULDER.value],
        )
        confidence = sum(point.visibility for point in shoulders) / 2.0

        visible = [point for point in landmarks if point.visibility >= 0.50]
        if len(visible) < 4:
            return PersonObservation(
                observed_at=observed_at,
                monotonic_at=monotonic_at,
                detected=False,
                confidence=confidence,
            )

        x_values = [min(1.0, max(0.0, point.x)) for point in visible]
        y_values = [min(1.0, max(0.0, point.y)) for point in visible]
        bbox_area_ratio = (max(x_values) - min(x_values)) * (
            max(y_values) - min(y_values)
        )

        return PersonObservation(
            observed_at=observed_at,
            monotonic_at=monotonic_at,
            detected=True,
            confidence=confidence,
            center_x=sum(point.x for point in shoulders) / 2.0,
            center_y=sum(point.y for point in shoulders) / 2.0,
            bbox_area_ratio=bbox_area_ratio,
        )

    def close(self) -> None:
        self._pose.close()


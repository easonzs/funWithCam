import math
from typing import Optional, Tuple

from .config import PersonConfig, PresenceConfig, StudyAreaConfig
from .models import PersonObservation, PresenceResult


class PresenceMonitor:
    def __init__(
        self,
        study_area: StudyAreaConfig,
        person: PersonConfig,
        presence: PresenceConfig,
    ):
        self._rect = study_area.rect
        self._person = person
        self._presence = presence
        self.reset()

    def reset(self) -> None:
        self._candidate_since: Optional[float] = None
        self._last_valid_at: Optional[float] = None
        self._last_center: Optional[Tuple[float, float]] = None

    def update(self, observation: PersonObservation) -> PresenceResult:
        eligible, reason = self._is_eligible(observation)
        if not eligible:
            if (
                self._last_valid_at is None
                or observation.monotonic_at - self._last_valid_at
                > self._presence.allowed_missed_seconds
            ):
                self.reset()
            return PresenceResult(
                eligible=False,
                confirmed=False,
                stable_seconds=self._stable_seconds(observation.monotonic_at),
                reason=reason,
            )

        center = (float(observation.center_x), float(observation.center_y))
        if self._last_center is not None and self._last_valid_at is not None:
            jump = math.dist(center, self._last_center)
            gap = observation.monotonic_at - self._last_valid_at
            if (
                jump > self._presence.max_center_jump
                or gap > self._presence.allowed_missed_seconds
            ):
                self._candidate_since = observation.monotonic_at

        if self._candidate_since is None:
            self._candidate_since = observation.monotonic_at
        self._last_valid_at = observation.monotonic_at
        self._last_center = center

        stable_seconds = self._stable_seconds(observation.monotonic_at)
        return PresenceResult(
            eligible=True,
            confirmed=stable_seconds >= self._presence.confirm_seconds,
            stable_seconds=stable_seconds,
            reason="eligible",
        )

    def _is_eligible(self, observation: PersonObservation) -> Tuple[bool, str]:
        if not observation.detected:
            return False, "person_not_detected"
        if observation.confidence < self._person.min_detection_confidence:
            return False, "low_confidence"
        if observation.center_x is None or observation.center_y is None:
            return False, "missing_center"
        if observation.bbox_area_ratio is None:
            return False, "missing_bbox_area"

        x_min, y_min, x_max, y_max = self._rect
        if not (
            x_min <= observation.center_x <= x_max
            and y_min <= observation.center_y <= y_max
        ):
            return False, "outside_study_area"

        if not (
            self._person.min_bbox_area_ratio
            <= observation.bbox_area_ratio
            <= self._person.max_bbox_area_ratio
        ):
            return False, "outside_child_size_range"
        return True, "eligible"

    def _stable_seconds(self, monotonic_now: float) -> float:
        if self._candidate_since is None:
            return 0.0
        return max(0.0, monotonic_now - self._candidate_since)


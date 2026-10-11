from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class PersonObservation:
    observed_at: datetime
    monotonic_at: float
    detected: bool
    confidence: float = 0.0
    center_x: Optional[float] = None
    center_y: Optional[float] = None
    bbox_area_ratio: Optional[float] = None


@dataclass(frozen=True)
class PresenceResult:
    eligible: bool
    confirmed: bool
    stable_seconds: float
    reason: str


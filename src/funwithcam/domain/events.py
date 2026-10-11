from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping


class EventSource(StrEnum):
    VISION = "vision"
    IDENTITY = "identity"
    SPEECH = "speech"
    SCHEDULE = "schedule"
    WEB = "web"
    SYSTEM = "system"


class EventType(StrEnum):
    PERSON_CANDIDATE_DETECTED = "person_candidate_detected"
    CHILD_IDENTITY_CONFIRMED = "child_identity_confirmed"
    IDENTITY_REJECTED = "identity_rejected"
    BRIEFING_FINISHED = "briefing_finished"
    START_REQUESTED = "start_requested"
    PRECHECK_PASSED = "precheck_passed"
    PRECHECK_CANCELLED = "precheck_cancelled"
    PERSON_LEFT_SEAT = "person_left_seat"
    PERSON_RETURNED = "person_returned"
    AWAY_TIMEOUT = "away_timeout"
    PAUSE_REQUESTED = "pause_requested"
    RESUME_REQUESTED = "resume_requested"
    INTERRUPT_TIMEOUT = "interrupt_timeout"
    FINISH_REQUESTED = "finish_requested"
    FINISH_CONFIRMED = "finish_confirmed"
    FINISH_CANCELLED = "finish_cancelled"
    DISABLE_TODAY = "disable_today"


@dataclass(frozen=True)
class Event:
    type: EventType
    occurred_at: datetime
    source: EventSource
    payload: Mapping[str, object] = field(default_factory=dict)


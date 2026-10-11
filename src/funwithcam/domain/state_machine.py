from dataclasses import dataclass
from typing import Optional

from .events import EventType
from .states import WorkflowState


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowSnapshot:
    state: WorkflowState
    return_state: Optional[WorkflowState] = None


_DIRECT_TRANSITIONS = {
    (
        WorkflowState.WAITING_FOR_FIRST_APPEARANCE,
        EventType.PERSON_CANDIDATE_DETECTED,
    ): WorkflowState.IDENTITY_CONFIRMING,
    (
        WorkflowState.IDENTITY_CONFIRMING,
        EventType.CHILD_IDENTITY_CONFIRMED,
    ): WorkflowState.BRIEFED,
    (
        WorkflowState.IDENTITY_CONFIRMING,
        EventType.IDENTITY_REJECTED,
    ): WorkflowState.WAITING_FOR_FIRST_APPEARANCE,
    (
        WorkflowState.BRIEFED,
        EventType.BRIEFING_FINISHED,
    ): WorkflowState.WAITING_TO_START,
    (
        WorkflowState.PRECHECK,
        EventType.PRECHECK_PASSED,
    ): WorkflowState.STUDYING,
    (
        WorkflowState.STUDYING,
        EventType.PERSON_LEFT_SEAT,
    ): WorkflowState.AWAY_GRACE,
    (
        WorkflowState.AWAY_GRACE,
        EventType.PERSON_RETURNED,
    ): WorkflowState.STUDYING,
    (
        WorkflowState.AWAY_GRACE,
        EventType.AWAY_TIMEOUT,
    ): WorkflowState.PAUSED,
    (
        WorkflowState.STUDYING,
        EventType.PAUSE_REQUESTED,
    ): WorkflowState.PAUSED,
    (
        WorkflowState.PAUSED,
        EventType.INTERRUPT_TIMEOUT,
    ): WorkflowState.INTERRUPTED,
    (
        WorkflowState.FINISH_CONFIRMING,
        EventType.FINISH_CONFIRMED,
    ): WorkflowState.COMPLETED,
}

_FINISHABLE_STATES = {
    WorkflowState.STUDYING,
    WorkflowState.AWAY_GRACE,
    WorkflowState.PAUSED,
    WorkflowState.INTERRUPTED,
}

_RESUMABLE_STATES = {
    WorkflowState.PAUSED,
    WorkflowState.INTERRUPTED,
}

_DISABLEABLE_STATES = {
    WorkflowState.WAITING_FOR_FIRST_APPEARANCE,
    WorkflowState.IDENTITY_CONFIRMING,
    WorkflowState.BRIEFED,
    WorkflowState.WAITING_TO_START,
}


def transition(
    snapshot: WorkflowSnapshot,
    event_type: EventType,
) -> WorkflowSnapshot:
    if (
        snapshot.state == WorkflowState.WAITING_TO_START
        and event_type == EventType.START_REQUESTED
    ):
        return WorkflowSnapshot(
            state=WorkflowState.PRECHECK,
            return_state=WorkflowState.WAITING_TO_START,
        )

    if (
        snapshot.state in _RESUMABLE_STATES
        and event_type == EventType.RESUME_REQUESTED
    ):
        return WorkflowSnapshot(
            state=WorkflowState.PRECHECK,
            return_state=snapshot.state,
        )

    if (
        snapshot.state == WorkflowState.PRECHECK
        and event_type == EventType.PRECHECK_CANCELLED
        and snapshot.return_state is not None
    ):
        return WorkflowSnapshot(state=snapshot.return_state)

    if snapshot.state in _FINISHABLE_STATES and event_type == EventType.FINISH_REQUESTED:
        return WorkflowSnapshot(
            state=WorkflowState.FINISH_CONFIRMING,
            return_state=snapshot.state,
        )

    if (
        snapshot.state == WorkflowState.FINISH_CONFIRMING
        and event_type == EventType.FINISH_CANCELLED
        and snapshot.return_state is not None
    ):
        return WorkflowSnapshot(state=snapshot.return_state)

    if snapshot.state in _DISABLEABLE_STATES and event_type == EventType.DISABLE_TODAY:
        return WorkflowSnapshot(state=WorkflowState.DISABLED)

    next_state = _DIRECT_TRANSITIONS.get((snapshot.state, event_type))
    if next_state is None:
        raise InvalidTransition(
            "event {} is not valid while in {}".format(
                event_type.value,
                snapshot.state.value,
            )
        )
    return WorkflowSnapshot(state=next_state)


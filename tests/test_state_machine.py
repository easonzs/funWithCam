import unittest

from funwithcam.domain.events import EventType
from funwithcam.domain.state_machine import (
    InvalidTransition,
    WorkflowSnapshot,
    transition,
)
from funwithcam.domain.states import WorkflowState


class WorkflowStateMachineTest(unittest.TestCase):
    def test_main_flow_reaches_studying(self):
        snapshot = WorkflowSnapshot(WorkflowState.WAITING_FOR_FIRST_APPEARANCE)
        for event_type in (
            EventType.PERSON_CANDIDATE_DETECTED,
            EventType.CHILD_IDENTITY_CONFIRMED,
            EventType.BRIEFING_FINISHED,
            EventType.START_REQUESTED,
            EventType.PRECHECK_PASSED,
        ):
            snapshot = transition(snapshot, event_type)

        self.assertEqual(WorkflowState.STUDYING, snapshot.state)
        self.assertIsNone(snapshot.return_state)

    def test_rejected_identity_returns_to_waiting(self):
        snapshot = WorkflowSnapshot(WorkflowState.IDENTITY_CONFIRMING)
        result = transition(snapshot, EventType.IDENTITY_REJECTED)
        self.assertEqual(
            WorkflowState.WAITING_FOR_FIRST_APPEARANCE,
            result.state,
        )

    def test_cancelled_finish_returns_to_previous_state(self):
        snapshot = WorkflowSnapshot(WorkflowState.PAUSED)
        confirming = transition(snapshot, EventType.FINISH_REQUESTED)
        self.assertEqual(WorkflowState.FINISH_CONFIRMING, confirming.state)
        self.assertEqual(WorkflowState.PAUSED, confirming.return_state)

        restored = transition(confirming, EventType.FINISH_CANCELLED)
        self.assertEqual(WorkflowState.PAUSED, restored.state)
        self.assertIsNone(restored.return_state)

    def test_resume_runs_precheck_and_can_be_cancelled(self):
        snapshot = WorkflowSnapshot(WorkflowState.INTERRUPTED)
        precheck = transition(snapshot, EventType.RESUME_REQUESTED)
        self.assertEqual(WorkflowState.PRECHECK, precheck.state)
        self.assertEqual(WorkflowState.INTERRUPTED, precheck.return_state)

        restored = transition(precheck, EventType.PRECHECK_CANCELLED)
        self.assertEqual(WorkflowState.INTERRUPTED, restored.state)

    def test_invalid_transition_is_rejected(self):
        snapshot = WorkflowSnapshot(WorkflowState.WAITING_TO_START)
        with self.assertRaises(InvalidTransition):
            transition(snapshot, EventType.FINISH_CONFIRMED)


if __name__ == "__main__":
    unittest.main()


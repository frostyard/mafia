import pytest
from mafia.domain.enums import RunState
from mafia.domain.state_machine import ALLOWED_TRANSITIONS, InvalidTransitionError, require_transition


def test_allows_expected_transition() -> None:
    require_transition(RunState.INTAKE, RunState.GENERATING_SPEC)


def test_rejects_skipped_gate() -> None:
    with pytest.raises(InvalidTransitionError):
        require_transition(RunState.INTAKE, RunState.EXECUTING_PHASE)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.GROUNDING_PLAN, RunState.GENERATING_PLAN),
        (RunState.GENERATING_PLAN, RunState.REVIEWING_PLAN),
        (RunState.REVIEWING_PLAN, RunState.ADJUDICATING_PLAN),
        (RunState.ADJUDICATING_PLAN, RunState.PERSISTING_PLAN),
        (RunState.PERSISTING_PLAN, RunState.AWAITING_PLAN_DECISION),
    ],
)
def test_allows_observable_plan_substeps(current: RunState, target: RunState) -> None:
    require_transition(current, target)


def test_transition_map_covers_every_state() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(RunState)


@pytest.mark.parametrize(
    "current",
    [
        RunState.AWAITING_PLAN_DECISION,
        RunState.READY_FOR_PHASE,
        RunState.WAITING_FOR_MERGE,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.COMPLETED,
    ],
)
def test_allows_return_to_specification_decision(current: RunState) -> None:
    require_transition(current, RunState.AWAITING_SPEC_DECISION)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.INTAKE, RunState.GROUNDING_PR_REVIEW),
        (RunState.GROUNDING_PR_REVIEW, RunState.REVIEWING_PR),
        (RunState.REVIEWING_PR, RunState.CONSOLIDATING_PR_REVIEW),
        (
            RunState.CONSOLIDATING_PR_REVIEW,
            RunState.AWAITING_PR_REVIEW_DECISION,
        ),
        (RunState.AWAITING_PR_REVIEW_DECISION, RunState.POSTING_PR_REVIEW),
        (RunState.POSTING_PR_REVIEW, RunState.COMPLETED),
    ],
)
def test_allows_pull_request_review_lifecycle(
    current: RunState,
    target: RunState,
) -> None:
    require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.EXECUTING_PHASE, RunState.REVIEWING_IMPLEMENTATION),
        (RunState.REVIEWING_IMPLEMENTATION, RunState.ADJUDICATING_IMPLEMENTATION),
        (RunState.ADJUDICATING_IMPLEMENTATION, RunState.EXECUTING_PHASE),
        (RunState.ADJUDICATING_IMPLEMENTATION, RunState.REMEDIATING_IMPLEMENTATION),
        (RunState.REMEDIATING_IMPLEMENTATION, RunState.VERIFYING_REMEDIATION),
        (RunState.VERIFYING_REMEDIATION, RunState.EXECUTING_PHASE),
    ],
)
def test_allows_bounded_implementation_review_lifecycle(current: RunState, target: RunState) -> None:
    require_transition(current, target)


def test_verification_cannot_transition_back_to_remediation() -> None:
    with pytest.raises(InvalidTransitionError):
        require_transition(
            RunState.VERIFYING_REMEDIATION,
            RunState.REMEDIATING_IMPLEMENTATION,
        )

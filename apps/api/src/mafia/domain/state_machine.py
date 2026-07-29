from mafia.domain.enums import RunState


class InvalidTransitionError(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.INTAKE: frozenset(
        {
            RunState.GENERATING_SPEC,
            RunState.GROUNDING_PR_REVIEW,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.GENERATING_SPEC: frozenset(
        {RunState.AWAITING_SPEC_DECISION, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.AWAITING_SPEC_DECISION: frozenset(
        {RunState.GENERATING_SPEC, RunState.GROUNDING_PLAN, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.GROUNDING_PLAN: frozenset(
        {RunState.GENERATING_PLAN, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.GENERATING_PLAN: frozenset(
        {RunState.REVIEWING_PLAN, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.REVIEWING_PLAN: frozenset(
        {RunState.ADJUDICATING_PLAN, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.ADJUDICATING_PLAN: frozenset(
        {RunState.PERSISTING_PLAN, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.PERSISTING_PLAN: frozenset(
        {RunState.AWAITING_PLAN_DECISION, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.AWAITING_PLAN_DECISION: frozenset(
        {
            RunState.AWAITING_SPEC_DECISION,
            RunState.GROUNDING_PLAN,
            RunState.READY_FOR_PHASE,
            RunState.WAITING_FOR_MERGE,
            RunState.COMPLETED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.READY_FOR_PHASE: frozenset(
        {
            RunState.AWAITING_SPEC_DECISION,
            RunState.EXECUTING_PHASE,
            RunState.REGROUNDING,
            RunState.COMPLETED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.EXECUTING_PHASE: frozenset(
        {
            RunState.REVIEWING_IMPLEMENTATION,
            RunState.PR_OPEN,
            RunState.READY_FOR_PHASE,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.REVIEWING_IMPLEMENTATION: frozenset(
        {
            RunState.ADJUDICATING_IMPLEMENTATION,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.ADJUDICATING_IMPLEMENTATION: frozenset(
        {
            RunState.EXECUTING_PHASE,
            RunState.REMEDIATING_IMPLEMENTATION,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.REMEDIATING_IMPLEMENTATION: frozenset(
        {
            RunState.VERIFYING_REMEDIATION,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.VERIFYING_REMEDIATION: frozenset(
        {RunState.EXECUTING_PHASE, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.PR_OPEN: frozenset(
        {RunState.WAITING_FOR_MERGE, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.WAITING_FOR_MERGE: frozenset(
        {
            RunState.AWAITING_SPEC_DECISION,
            RunState.READY_FOR_PHASE,
            RunState.REGROUNDING,
            RunState.COMPLETED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.REGROUNDING: frozenset(
        {
            RunState.GENERATING_PLAN,
            RunState.READY_FOR_PHASE,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.GROUNDING_PR_REVIEW: frozenset(
        {RunState.REVIEWING_PR, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.REVIEWING_PR: frozenset(
        {RunState.CONSOLIDATING_PR_REVIEW, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.CONSOLIDATING_PR_REVIEW: frozenset(
        {RunState.AWAITING_PR_REVIEW_DECISION, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.AWAITING_PR_REVIEW_DECISION: frozenset(
        {
            RunState.POSTING_PR_REVIEW,
            RunState.COMPLETED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.POSTING_PR_REVIEW: frozenset(
        {RunState.COMPLETED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.FAILED: frozenset(
        {
            RunState.AWAITING_SPEC_DECISION,
            RunState.AWAITING_PR_REVIEW_DECISION,
            RunState.GROUNDING_PR_REVIEW,
            RunState.GENERATING_SPEC,
            RunState.GROUNDING_PLAN,
            RunState.READY_FOR_PHASE,
            RunState.EXECUTING_PHASE,
            RunState.WAITING_FOR_MERGE,
            RunState.CANCELLED,
        }
    ),
    RunState.CANCELLED: frozenset({RunState.AWAITING_SPEC_DECISION}),
    RunState.COMPLETED: frozenset({RunState.AWAITING_SPEC_DECISION}),
}


def require_transition(current: RunState, target: RunState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Cannot transition run from {current.value} to {target.value}")

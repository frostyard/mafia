from enum import StrEnum


class RunState(StrEnum):
    INTAKE = "intake"
    GENERATING_SPEC = "generating_spec"
    AWAITING_SPEC_DECISION = "awaiting_spec_decision"
    GROUNDING_PLAN = "grounding_plan"
    GENERATING_PLAN = "generating_plan"
    REVIEWING_PLAN = "reviewing_plan"
    ADJUDICATING_PLAN = "adjudicating_plan"
    PERSISTING_PLAN = "persisting_plan"
    AWAITING_PLAN_DECISION = "awaiting_plan_decision"
    READY_FOR_PHASE = "ready_for_phase"
    EXECUTING_PHASE = "executing_phase"
    PR_OPEN = "pr_open"
    WAITING_FOR_MERGE = "waiting_for_merge"
    REGROUNDING = "regrounding"
    GROUNDING_PR_REVIEW = "grounding_pr_review"
    REVIEWING_PR = "reviewing_pr"
    CONSOLIDATING_PR_REVIEW = "consolidating_pr_review"
    AWAITING_PR_REVIEW_DECISION = "awaiting_pr_review_decision"
    POSTING_PR_REVIEW = "posting_pr_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RequirementType(StrEnum):
    ISSUE = "issue"
    TEXT = "text"


class WorkflowType(StrEnum):
    SPECIFICATION = "specification"
    PULL_REQUEST_REVIEW = "pull_request_review"


class ArtifactKind(StrEnum):
    SPECIFICATION = "specification"
    PLAN = "plan"
    REVIEW = "review"
    REVIEW_LEDGER = "review_ledger"
    PHASE_RESULT = "phase_result"
    PULL_REQUEST_REVIEW = "pull_request_review"
    PULL_REQUEST_REVIEW_CONSOLIDATED = "pull_request_review_consolidated"


class DecisionType(StrEnum):
    ACCEPT = "accept"
    REFINE = "refine"
    RESET_SPECIFICATION = "reset_specification"
    POST_REVIEW = "post_review"
    FINISH_REVIEW = "finish_review"
    START_PHASE = "start_phase"
    CANCEL = "cancel"


class PhaseState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    EXECUTING = "executing"
    WAITING_FOR_MERGE = "waiting_for_merge"
    MERGED = "merged"
    FAILED = "failed"

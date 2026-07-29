from datetime import datetime
from typing import Any, Literal

from mafia.domain.enums import RequirementType, RunState, WorkflowType
from pydantic import BaseModel, Field, model_validator


class RunCreate(BaseModel):
    workflow_type: WorkflowType = WorkflowType.SPECIFICATION
    repository: str = Field(min_length=3, max_length=500)
    primary_model: str = Field(min_length=1, max_length=100)
    issue_number: int | None = Field(default=None, gt=0)
    requirement_text: str | None = Field(default=None, min_length=1, max_length=100_000)
    pull_request_number: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_workflow_input(self) -> "RunCreate":
        if self.workflow_type == WorkflowType.PULL_REQUEST_REVIEW:
            if (
                self.pull_request_number is None
                or self.issue_number is not None
                or self.requirement_text is not None
            ):
                raise ValueError(
                    "Pull-request review requires only pull_request_number"
                )
            return self
        if (
            (self.issue_number is None) == (self.requirement_text is None)
            or self.pull_request_number is not None
        ):
            raise ValueError("Provide exactly one of issue_number or requirement_text")
        return self


class RepositoryRead(BaseModel):
    id: str
    owner: str
    name: str
    remote_url: str
    default_branch: str | None
    last_fetched_sha: str | None

    model_config = {"from_attributes": True}


class RunRead(BaseModel):
    id: str
    repository: RepositoryRead
    workflow_type: WorkflowType
    requirement_type: RequirementType | None
    issue_number: int | None
    requirement_text: str | None
    pull_request_number: int | None
    primary_model: str
    reviewer_model: str
    thread_id: str
    state: RunState
    version: int
    active_spec_revision: int | None
    active_plan_revision: int | None
    active_review_revision: int | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ArtifactRead(BaseModel):
    id: str
    kind: str
    schema_version: int
    revision: int
    structured_data: dict[str, Any]
    rendered_markdown: str
    model: str
    source_snapshot_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PhaseRead(BaseModel):
    id: str
    ordinal: int
    title: str
    objective: str
    dependencies: list[int]
    details: dict[str, Any]
    status: str
    plan_revision: int
    source_sha: str
    branch_name: str | None
    commit_sha: str | None
    pr_number: int | None
    pr_url: str | None
    merge_sha: str | None

    model_config = {"from_attributes": True}


class EvidenceRead(BaseModel):
    id: str
    snapshot_id: str
    source_sha: str
    kind: str
    path_or_url: str
    line_start: int | None
    line_end: int | None
    excerpt_hash: str
    detail: dict[str, Any]
    created_at: datetime


class RunDetail(RunRead):
    artifacts: list[ArtifactRead]
    phases: list[PhaseRead]


class OperationRead(BaseModel):
    id: str
    phase_id: str | None
    operation_type: str
    status: str
    model: str | None
    attempt: int
    timeout_seconds: int | None
    detail: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime
    heartbeat_at: datetime
    progress_at: datetime
    completed_at: datetime | None
    elapsed_seconds: int


class ActivityEventRead(BaseModel):
    id: str
    event_type: str
    from_state: str | None
    to_state: str | None
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class RunActivity(BaseModel):
    run_id: str
    state: RunState
    version: int
    status_mode: Literal[
        "idle",
        "working",
        "decision",
        "external",
        "failed",
        "cancelled",
        "completed",
    ]
    status_message: str
    stalled: bool
    stall_reason: str | None
    stall_threshold_seconds: int
    can_cancel: bool
    can_retry: bool
    source_sha: str | None
    files_discovered: int | None
    citations_found: int
    operations: list[OperationRead]
    events: list[ActivityEventRead]


class ModelPair(BaseModel):
    primary_model: str
    reviewer_model: str


class ModelAvailability(BaseModel):
    pairs: list[ModelPair]
    required: list[str]
    available: list[str]
    missing: list[str]


class Capability(BaseModel):
    name: str
    available: bool
    detail: str


class Readiness(BaseModel):
    ready: bool
    capabilities: list[Capability]


class ErrorDetail(BaseModel):
    code: str
    message: str

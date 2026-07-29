import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from mafia.db.base import Base, TimestampMixin
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    PhaseState,
    RequirementType,
    RunState,
    WorkflowType,
)
from mafia.services.operator import current_actor
from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def enum_values(members: type[StrEnum]) -> list[str]:
    return [member.value for member in members]


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    remote_url: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    cache_path: Mapped[str | None] = mapped_column(Text)
    last_fetched_sha: Mapped[str | None] = mapped_column(String(64))

    runs: Mapped[list["Run"]] = relationship(back_populates="repository")
    __table_args__ = (UniqueConstraint("owner", "name"),)


class Run(Base, TimestampMixin):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="RESTRICT"), index=True)
    workflow_type: Mapped[WorkflowType] = mapped_column(
        Enum(
            WorkflowType,
            values_callable=enum_values,
            native_enum=False,
            length=40,
        ),
        default=WorkflowType.SPECIFICATION,
    )
    requirement_type: Mapped[RequirementType | None] = mapped_column(
        Enum(
            RequirementType,
            values_callable=enum_values,
            native_enum=False,
            length=20,
        )
    )
    requirement_text: Mapped[str | None] = mapped_column(Text)
    issue_number: Mapped[int | None] = mapped_column(Integer)
    pull_request_number: Mapped[int | None] = mapped_column(Integer)
    primary_model: Mapped[str] = mapped_column(String(100))
    reviewer_model: Mapped[str] = mapped_column(String(100))
    thread_id: Mapped[str] = mapped_column(String(255), unique=True, default=new_id)
    state: Mapped[RunState] = mapped_column(
        Enum(
            RunState,
            values_callable=enum_values,
            native_enum=False,
            length=40,
        ),
        default=RunState.INTAKE,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    active_spec_revision: Mapped[int | None] = mapped_column(Integer)
    active_plan_revision: Mapped[int | None] = mapped_column(Integer)
    active_review_revision: Mapped[int | None] = mapped_column(Integer)
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(Text)

    repository: Mapped[Repository] = relationship(back_populates="runs")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    phases: Mapped[list["Phase"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class SourceSnapshot(Base, TimestampMixin):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    git_sha: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(50))
    issue_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    instructions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    worktree_path: Mapped[str] = mapped_column(Text)


class Evidence(Base, TimestampMixin):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    path_or_url: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    excerpt_hash: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    source_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[ArtifactKind] = mapped_column(
        Enum(
            ArtifactKind,
            values_callable=enum_values,
            native_enum=False,
            length=40,
        )
    )
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    rendered_markdown: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))

    run: Mapped[Run] = relationship(back_populates="artifacts")
    __table_args__ = (UniqueConstraint("run_id", "kind", "revision"),)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    phase_id: Mapped[str | None] = mapped_column(ForeignKey("phases.id", ondelete="SET NULL"))
    decision_type: Mapped[DecisionType] = mapped_column(
        Enum(
            DecisionType,
            values_callable=enum_values,
            native_enum=False,
            length=30,
        )
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(100), default=current_actor)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Phase(Base, TimestampMixin):
    __tablename__ = "phases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    objective: Mapped[str] = mapped_column(Text)
    dependencies: Mapped[list[int]] = mapped_column(JSON, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[PhaseState] = mapped_column(
        Enum(
            PhaseState,
            values_callable=enum_values,
            native_enum=False,
            length=30,
        ),
        default=PhaseState.PENDING,
    )
    plan_revision: Mapped[int] = mapped_column(Integer)
    source_sha: Mapped[str] = mapped_column(String(64))
    branch_name: Mapped[str | None] = mapped_column(String(255))
    worktree_path: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(Text)
    merge_sha: Mapped[str | None] = mapped_column(String(64))
    review_cycle: Mapped[int] = mapped_column(Integer, default=1)
    implementation_review_attempts: Mapped[int] = mapped_column(Integer, default=0)
    remediation_attempts: Mapped[int] = mapped_column(Integer, default=0)
    verification_attempts: Mapped[int] = mapped_column(Integer, default=0)
    candidate_base_sha: Mapped[str | None] = mapped_column(String(64))
    candidate_diff_hash: Mapped[str | None] = mapped_column(String(64))

    run: Mapped[Run] = relationship(back_populates="phases")
    __table_args__ = (UniqueConstraint("run_id", "ordinal"),)


class Operation(Base, TimestampMixin):
    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    phase_id: Mapped[str | None] = mapped_column(ForeignKey("phases.id", ondelete="CASCADE"), index=True)
    operation_type: Mapped[str] = mapped_column(String(50))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    model: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    progress_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AuditEvent(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    from_state: Mapped[str | None] = mapped_column(String(40))
    to_state: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(100), default=current_actor)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AGUISnapshot(Base, TimestampMixin):
    __tablename__ = "agui_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(255))
    thread_id: Mapped[str] = mapped_column(String(255))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("scope", "thread_id"),)


class RepositoryLock(Base):
    __tablename__ = "repo_locks"

    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    phase_id: Mapped[str] = mapped_column(ForeignKey("phases.id", ondelete="CASCADE"), index=True)
    lease_token: Mapped[str] = mapped_column(String(36), default=new_id)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ix_events_run_created", AuditEvent.run_id, AuditEvent.created_at)
Index("ix_artifacts_run_kind_revision", Artifact.run_id, Artifact.kind, Artifact.revision)
Index("ix_operations_run_status", Operation.run_id, Operation.status)

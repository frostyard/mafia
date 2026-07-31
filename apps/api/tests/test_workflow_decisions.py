from collections.abc import AsyncGenerator, Awaitable, Callable
from types import ModuleType
from unittest.mock import AsyncMock

import pytest
from mafia.db.base import Base
from mafia.db.models import (
    Artifact,
    AuditEvent,
    Decision,
    PendingAction,
    Phase,
    Repository,
    Run,
    SourceSnapshot,
)
from mafia.domain.artifacts import ImplementationPlan, Specification
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    PendingActionKind,
    PhaseState,
    RequirementType,
    RunState,
)
from mafia.domain.schemas import DecisionSubmission
from mafia.services import operations, run_control
from mafia.services.artifacts import persist_artifact
from mafia.services.runs import get_run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_control, "SessionFactory", factory)
    monkeypatch.setattr(operations, "SessionFactory", factory)
    activity = ModuleType("mafia.services.activity")

    async def get_run_activity(run_id: str) -> object:
        return {"run_id": run_id}

    activity.get_run_activity = get_run_activity  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "mafia.services.activity", activity)
    try:
        yield factory
    finally:
        await engine.dispose()


async def add_run(session: AsyncSession, *, state: RunState = RunState.INTAKE) -> Run:
    repository = Repository(owner="octo", name="repo", remote_url="https://github.com/octo/repo.git")
    session.add(repository)
    await session.flush()
    run = Run(
        repository_id=repository.id,
        requirement_type=RequirementType.TEXT,
        requirement_text="Implement durable artifact decisions",
        primary_model="gpt-5.6-sol",
        reviewer_model="claude-opus-4.8",
        state=state,
    )
    session.add(run)
    await session.flush()
    return run


async def add_snapshot(session: AsyncSession, run: Run, reason: str) -> SourceSnapshot:
    snapshot = SourceSnapshot(
        run_id=run.id,
        git_sha="a" * 40,
        reason=reason,
        manifest={},
        instructions=[],
        worktree_path="/tmp/worktree",
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


def pending_action_id(run: Run) -> str:
    assert run.pending_action is not None
    return run.pending_action.id


def fail_launch(run_id: str, work: Callable[[], Awaitable[None]]) -> None:
    del run_id, work
    pytest.fail("Background work must not launch")


def specification() -> Specification:
    return Specification.model_validate(
        {
            "title": "Durable decisions",
            "problem_statement": "Decisions must survive restarts.",
            "context": "Run control stores workflow state.",
            "goals": ["Persist decisions"],
            "non_goals": [],
            "users": ["Operator"],
            "use_cases": ["Review a specification"],
            "requirements": [{"id": "REQ-1", "statement": "Persist the action", "priority": "must"}],
            "acceptance_criteria": [
                {"id": "AC-1", "requirement_ids": ["REQ-1"], "statement": "An action is available"}
            ],
            "constraints": [],
            "assumptions": [],
            "open_questions": [],
            "risks": [],
            "out_of_scope": [],
        }
    )


def plan(source_sha: str) -> ImplementationPlan:
    return ImplementationPlan.model_validate(
        {
            "specification_revision": 1,
            "source_sha": source_sha,
            "summary": "Implement the durable action.",
            "system_findings": [],
            "architecture_decisions": [],
            "phases": [
                {
                    "ordinal": 1,
                    "title": "Implementation",
                    "objective": "Persist the action",
                    "dependencies": [],
                    "scope": ["api"],
                    "likely_files": [],
                    "implementation_steps": ["Implement it"],
                    "tests": ["Test it"],
                    "migration_and_rollout": [],
                    "risks": [],
                    "acceptance_criteria": ["Action persists"],
                }
            ],
            "cross_phase_invariants": ["Actions remain durable"],
            "completion_definition": ["Merged"],
            "unresolved_assumptions": [],
        }
    )


class FakeGenerator:
    def __init__(self) -> None:
        self.plan_feedback: str | None = None

    async def specification(
        self, session: AsyncSession, run: Run, snapshot: SourceSnapshot, *, feedback: str | None = None
    ) -> Artifact:
        del feedback
        return await persist_artifact(
            session,
            run=run,
            kind=ArtifactKind.SPECIFICATION,
            data=specification(),
            markdown="# Specification",
            model=run.primary_model,
            snapshot=snapshot,
        )

    async def draft_plan(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        specification_artifact: Artifact,
        *,
        feedback: str | None = None,
    ) -> Artifact:
        del specification_artifact
        self.plan_feedback = feedback
        async with run_control.SessionFactory() as session:
            current_run = await session.get(Run, run.id)
            assert current_run is not None
            artifact = await persist_artifact(
                session,
                run=current_run,
                kind=ArtifactKind.PLAN,
                data=plan(snapshot.git_sha),
                markdown="# Draft",
                model=run.primary_model,
                snapshot=snapshot,
            )
            await session.commit()
            return artifact

    async def adversarial_review(
        self, run: Run, snapshot: SourceSnapshot, specification_artifact: Artifact, plan_artifact: Artifact
    ) -> Artifact:
        del specification_artifact, plan_artifact
        async with run_control.SessionFactory() as session:
            current_run = await session.get(Run, run.id)
            assert current_run is not None
            artifact = Artifact(
                run_id=run.id,
                source_snapshot_id=snapshot.id,
                kind=ArtifactKind.REVIEW,
                revision=1,
                structured_data={},
                rendered_markdown="# Review",
                model=run.reviewer_model,
            )
            session.add(artifact)
            await session.commit()
            return artifact

    async def adjudicate_plan(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        specification_artifact: Artifact,
        plan_artifact: Artifact,
        review_artifact: Artifact,
    ) -> object:
        del run, snapshot, specification_artifact, plan_artifact, review_artifact
        return type("Resolution", (), {"dispositions": []})()

    async def persist_final_plan(
        self, run: Run, snapshot: SourceSnapshot, review_artifact: Artifact, resolution: object
    ) -> tuple[Artifact, Artifact]:
        del review_artifact, resolution
        async with run_control.SessionFactory() as session:
            current_run = await session.get(Run, run.id)
            assert current_run is not None
            final = await persist_artifact(
                session,
                run=current_run,
                kind=ArtifactKind.PLAN,
                data=plan(snapshot.git_sha),
                markdown="# Plan",
                model=run.primary_model,
                snapshot=snapshot,
            )
            ledger = Artifact(
                run_id=run.id,
                source_snapshot_id=snapshot.id,
                kind=ArtifactKind.REVIEW_LEDGER,
                revision=1,
                structured_data={},
                rendered_markdown="# Ledger",
                model=run.primary_model,
            )
            session.add(ledger)
            await session.commit()
            return final, ledger


@pytest.mark.asyncio
async def test_specification_generation_persists_action_and_audit_event(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session)
        await session.commit()
        run_id = run.id

    async def capture(
        session: AsyncSession, run: Run, repository: Repository, *, reason: str
    ) -> SourceSnapshot:
        del repository
        return await add_snapshot(session, run, reason)

    monkeypatch.setattr(run_control, "capture_source_snapshot", capture)
    monkeypatch.setattr(run_control, "ArtifactGenerator", FakeGenerator)

    await run_control.advance_run(run_id)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        artifact = await session.scalar(select(Artifact).where(Artifact.run_id == run_id))
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run_id))
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id, AuditEvent.event_type == "specification.generated"
            )
        )
    assert run is not None and artifact is not None and action is not None and event is not None
    assert run.state == RunState.AWAITING_SPEC_DECISION
    assert action.kind == PendingActionKind.SPECIFICATION
    assert action.artifact_id == artifact.id
    assert action.revision == artifact.revision
    assert action.expected_run_version == run.version
    assert event.payload == {"artifact_id": artifact.id, "revision": artifact.revision}


@pytest.mark.asyncio
async def test_plan_generation_persists_action_and_audit_event(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.GROUNDING_PLAN)
        snapshot = await add_snapshot(session, run, "spec-r1")
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data=specification().model_dump(mode="json"),
            rendered_markdown="# Specification",
            model=run.primary_model,
        )
        session.add(artifact)
        run.active_spec_revision = 1
        await session.commit()
        run_id = run.id

    async def capture(
        session: AsyncSession, run: Run, repository: Repository, *, reason: str
    ) -> SourceSnapshot:
        del repository
        return await add_snapshot(session, run, reason)

    monkeypatch.setattr(run_control, "capture_source_snapshot", capture)
    monkeypatch.setattr(run_control, "ArtifactGenerator", FakeGenerator)

    await run_control.advance_run(run_id)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run_id))
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id, AuditEvent.event_type == "plan.review_completed"
            )
        )
    assert run is not None and action is not None and event is not None
    assert run.state == RunState.AWAITING_PLAN_DECISION
    assert action.kind == PendingActionKind.PLAN
    assert action.expected_run_version == run.version


@pytest.mark.asyncio
async def test_regrounding_uses_legacy_source_drift_feedback(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.REGROUNDING)
        snapshot = await add_snapshot(session, run, "spec-r1")
        session.add(
            Artifact(
                run_id=run.id,
                source_snapshot_id=snapshot.id,
                kind=ArtifactKind.SPECIFICATION,
                revision=1,
                structured_data=specification().model_dump(mode="json"),
                rendered_markdown="# Specification",
                model=run.primary_model,
            )
        )
        run.active_spec_revision = 1
        await session.commit()
        run_id = run.id

    async def capture(
        session: AsyncSession, run: Run, repository: Repository, *, reason: str
    ) -> SourceSnapshot:
        del repository
        return await add_snapshot(session, run, reason)

    generator = FakeGenerator()
    monkeypatch.setattr(run_control, "capture_source_snapshot", capture)
    monkeypatch.setattr(run_control, "ArtifactGenerator", lambda: generator)

    await run_control.advance_run(run_id)

    assert generator.plan_feedback == "Source drift requires an updated plan."


@pytest.mark.asyncio
async def test_specification_accept_consumes_action_before_launching_plan(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data=specification().model_dump(mode="json"),
            rendered_markdown="# Specification",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_spec_revision = artifact.revision
        run.pending_action = PendingAction(
            kind=PendingActionKind.SPECIFICATION,
            artifact_id=artifact.id,
            revision=artifact.revision,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()
        action_id = pending_action_id(run)

    launched: list[str] = []
    launched_work: list[Callable[[], Awaitable[None]]] = []

    def record_launch(run_id: str, callback: Callable[[], Awaitable[None]]) -> None:
        launched.append(run_id)
        launched_work.append(callback)

    monkeypatch.setattr(run_control, "launch_background_work", record_launch)
    guarded = AsyncMock()
    monkeypatch.setattr(run_control, "_run_guarded", guarded)

    await run_control.submit_decision(run.id, action_id, DecisionSubmission(action="accept"))

    async with session_factory() as session:
        accepted = await session.get(Run, run.id)
        decision = await session.scalar(select(Decision).where(Decision.run_id == run.id))
        action = await session.get(PendingAction, action_id)
    assert accepted is not None and decision is not None
    assert accepted.state == RunState.GROUNDING_PLAN
    assert decision.decision_type == DecisionType.ACCEPT
    assert action is None
    assert launched == [run.id]
    await launched_work[0]()
    guarded_call = guarded.await_args
    assert guarded_call is not None
    assert guarded_call.args[1] == "planning"


@pytest.mark.asyncio
async def test_specification_refine_consumes_action_before_launching_generation(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data=specification().model_dump(mode="json"),
            rendered_markdown="# Specification",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_spec_revision = 1
        run.pending_action = PendingAction(
            kind=PendingActionKind.SPECIFICATION,
            artifact_id=artifact.id,
            revision=1,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()
        action_id = pending_action_id(run)

    launched: list[str] = []

    def record_launch(run_id: str, work: Callable[[], Awaitable[None]]) -> None:
        del work
        launched.append(run_id)

    monkeypatch.setattr(run_control, "launch_background_work", record_launch)
    await run_control.submit_decision(
        run.id, action_id, DecisionSubmission(action="refine", feedback="Clarify the rollout.")
    )

    async with session_factory() as session:
        refined = await session.get(Run, run.id)
        decision = await session.scalar(select(Decision).where(Decision.run_id == run.id))
    assert refined is not None and decision is not None
    assert refined.state == RunState.GENERATING_SPEC
    assert decision.feedback == "Clarify the rollout."
    assert launched == [run.id]


@pytest.mark.asyncio
async def test_artifact_cancel_consumes_action_and_cancels_run(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data=specification().model_dump(mode="json"),
            rendered_markdown="# Specification",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_spec_revision = 1
        run.pending_action = PendingAction(
            kind=PendingActionKind.SPECIFICATION,
            artifact_id=artifact.id,
            revision=1,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()
        action_id = pending_action_id(run)

    monkeypatch.setattr(run_control, "launch_background_work", fail_launch)
    await run_control.submit_decision(run.id, action_id, DecisionSubmission(action="cancel"))

    async with session_factory() as session:
        cancelled = await session.get(Run, run.id)
        decision = await session.scalar(select(Decision).where(Decision.run_id == run.id))
        action = await session.get(PendingAction, action_id)
    assert cancelled is not None and decision is not None
    assert cancelled.state == RunState.CANCELLED
    assert decision.decision_type == DecisionType.CANCEL
    assert action is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["id", "version", "artifact"])
async def test_invalid_artifact_action_conflicts_without_side_effects(
    session_factory: async_sessionmaker[AsyncSession], mutation: str
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data=specification().model_dump(mode="json"),
            rendered_markdown="# Specification",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_spec_revision = 1
        run.pending_action = PendingAction(
            kind=PendingActionKind.SPECIFICATION,
            artifact_id=artifact.id,
            revision=1,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()
        action_id = pending_action_id(run)
        if mutation == "version":
            run.version += 1
        elif mutation == "artifact":
            wrong = Artifact(
                run_id=run.id,
                kind=ArtifactKind.PLAN,
                revision=1,
                structured_data={},
                rendered_markdown="# Plan",
                model=run.primary_model,
            )
            session.add(wrong)
            await session.flush()
            pending_action = run.pending_action
            assert pending_action is not None
            pending_action.artifact_id = wrong.id
        await session.commit()

    submitted_id = "missing" if mutation == "id" else action_id
    with pytest.raises(run_control.RunControlError):
        await run_control.submit_decision(run.id, submitted_id, DecisionSubmission(action="accept"))

    async with session_factory() as session:
        unchanged = await session.get(Run, run.id)
        action = await session.get(PendingAction, action_id)
        decisions = list(await session.scalars(select(Decision).where(Decision.run_id == run.id)))
    assert unchanged is not None and action is not None
    assert unchanged.state == RunState.AWAITING_SPEC_DECISION
    assert decisions == []


@pytest.mark.asyncio
async def test_persisted_plan_action_is_available_in_a_fresh_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_PLAN_DECISION)
        snapshot = await add_snapshot(session, run, "plan-r1")
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.PLAN,
            revision=1,
            structured_data=plan(snapshot.git_sha).model_dump(mode="json"),
            rendered_markdown="# Plan",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_plan_revision = artifact.revision
        run.pending_action = PendingAction(
            kind=PendingActionKind.PLAN,
            artifact_id=artifact.id,
            revision=artifact.revision,
            expected_run_version=run.version,
            payload={"prompt": "Accept the reviewed plan or refine it with feedback."},
        )
        await session.commit()
        run_id = run.id
        action_id = pending_action_id(run)

    async with session_factory() as session:
        reopened = await get_run(session, run_id)

    assert reopened.pending_action is not None
    assert reopened.pending_action.id == action_id
    assert reopened.pending_action.kind == PendingActionKind.PLAN


@pytest.mark.asyncio
async def test_plan_refine_consumes_action_before_launching_generation(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_PLAN_DECISION)
        snapshot = await add_snapshot(session, run, "plan-r1")
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.PLAN,
            revision=1,
            structured_data=plan(snapshot.git_sha).model_dump(mode="json"),
            rendered_markdown="# Plan",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_plan_revision = artifact.revision
        run.pending_action = PendingAction(
            kind=PendingActionKind.PLAN,
            artifact_id=artifact.id,
            revision=artifact.revision,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()
        action_id = pending_action_id(run)

    launched: list[str] = []

    def record_launch(run_id: str, work: Callable[[], Awaitable[None]]) -> None:
        del work
        launched.append(run_id)

    monkeypatch.setattr(run_control, "launch_background_work", record_launch)
    await run_control.submit_decision(
        run.id, action_id, DecisionSubmission(action="refine", feedback="Add a rollback phase.")
    )

    async with session_factory() as session:
        refined = await session.get(Run, run.id)
        decision = await session.scalar(select(Decision).where(Decision.run_id == run.id))
        action = await session.get(PendingAction, action_id)
    assert refined is not None and decision is not None
    assert refined.state == RunState.GROUNDING_PLAN
    assert decision.feedback == "Add a rollback phase."
    assert action is None
    assert launched == [run.id]


@pytest.mark.asyncio
async def test_plan_accept_marks_ready_phase(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_PLAN_DECISION)
        snapshot = await add_snapshot(session, run, "plan-r1")
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.PLAN,
            revision=1,
            structured_data=plan(snapshot.git_sha).model_dump(mode="json"),
            rendered_markdown="# Plan",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_plan_revision = artifact.revision
        run.pending_action = PendingAction(
            kind=PendingActionKind.PLAN,
            artifact_id=artifact.id,
            revision=artifact.revision,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()
        action_id = pending_action_id(run)

    monkeypatch.setattr(run_control, "source_validation_status", AsyncMock(return_value=(True, "repository")))
    await run_control.submit_decision(run.id, action_id, DecisionSubmission(action="accept"))

    async with session_factory() as session:
        accepted = await session.get(Run, run.id)
        phase = await session.scalar(select(Phase).where(Phase.run_id == run.id))
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert accepted is not None and phase is not None and action is not None
    assert accepted.state == RunState.READY_FOR_PHASE
    assert phase.status == PhaseState.READY
    assert action.kind == PendingActionKind.PHASE
    assert action.phase_id == phase.id


@pytest.mark.asyncio
async def test_plan_accept_completes_when_all_phases_are_already_merged(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_PLAN_DECISION)
        snapshot = await add_snapshot(session, run, "plan-r1")
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.PLAN,
            revision=1,
            structured_data=plan(snapshot.git_sha).model_dump(mode="json"),
            rendered_markdown="# Plan",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_plan_revision = artifact.revision
        run.pending_action = PendingAction(
            kind=PendingActionKind.PLAN,
            artifact_id=artifact.id,
            revision=artifact.revision,
            expected_run_version=run.version,
            payload={},
        )
        session.add(
            Phase(
                run_id=run.id,
                ordinal=1,
                title="Merged implementation",
                objective="Already complete",
                dependencies=[],
                details={},
                status=PhaseState.MERGED,
                plan_revision=1,
                source_sha=snapshot.git_sha,
                merge_sha="b" * 40,
            )
        )
        await session.commit()
        action_id = pending_action_id(run)

    await run_control.submit_decision(run.id, action_id, DecisionSubmission(action="accept"))

    async with session_factory() as session:
        completed = await session.get(Run, run.id)
        action = await session.get(PendingAction, action_id)
    assert completed is not None
    assert completed.state == RunState.COMPLETED
    assert action is None


@pytest.mark.asyncio
async def test_plan_accept_preserves_open_pull_request_gate(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    source_sha = "a" * 40
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_PLAN_DECISION)
        run.active_spec_revision = 1
        snapshot = await add_snapshot(session, run, "plan-r1")
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.PLAN,
            revision=1,
            structured_data=plan(source_sha).model_dump(mode="json"),
            rendered_markdown="# Plan",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.flush()
        run.active_plan_revision = 1
        run.pending_action = PendingAction(
            kind=PendingActionKind.PLAN,
            artifact_id=artifact.id,
            revision=1,
            expected_run_version=run.version,
            payload={},
        )
        session.add(
            Phase(
                run_id=run.id,
                ordinal=1,
                title="Existing pull request",
                objective="Preserve in-flight work",
                dependencies=[],
                details={},
                status=PhaseState.WAITING_FOR_MERGE,
                plan_revision=1,
                source_sha=source_sha,
                pr_number=42,
            )
        )
        await session.commit()
        action_id = pending_action_id(run)

    monkeypatch.setattr(run_control, "launch_background_work", fail_launch)
    await run_control.submit_decision(run.id, action_id, DecisionSubmission(action="accept"))

    async with session_factory() as session:
        accepted = await session.get(Run, run.id)
        phases = list(
            await session.scalars(select(Phase).where(Phase.run_id == run.id).order_by(Phase.ordinal))
        )
        action = await session.get(PendingAction, action_id)
    assert accepted is not None
    assert accepted.state == RunState.WAITING_FOR_MERGE
    assert [(phase.ordinal, phase.status) for phase in phases] == [(1, PhaseState.WAITING_FOR_MERGE)]
    assert action is None

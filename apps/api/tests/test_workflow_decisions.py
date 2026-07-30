from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from agent_framework import WorkflowContext
from mafia.db.base import Base
from mafia.db.models import Artifact, Decision, Phase, Repository, Run, SourceSnapshot
from mafia.domain.artifacts import (
    ArtifactDecisionRequest,
    ConsolidatedPullRequestReview,
    ImplementationPlan,
    PhaseDecisionRequest,
    PullRequestReview,
    PullRequestReviewDecisionRequest,
)
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    PhaseState,
    RequirementType,
    RunState,
    WorkflowType,
)
from mafia.services import operations
from mafia.services.commands import CommandResult
from mafia.services.execution import PhaseExecutionError
from mafia.services.pr_reviews import PullRequestReviewService
from mafia.workflows import run_workflow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class RecordingContext:
    def __init__(self) -> None:
        self.requests: list[tuple[ArtifactDecisionRequest, str]] = []
        self.outputs: list[str] = []

    async def request_info(
        self,
        request: ArtifactDecisionRequest,
        response_type: type[dict[str, Any]],
        *,
        request_id: str,
    ) -> None:
        assert response_type is dict
        self.requests.append((request, request_id))

    async def yield_output(self, output: str) -> None:
        self.outputs.append(output)


@pytest.fixture
async def workflow_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_workflow, "SessionFactory", factory)
    monkeypatch.setattr(operations, "SessionFactory", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("state", "kind", "revision"),
    [
        (RunState.AWAITING_SPEC_DECISION, ArtifactKind.SPECIFICATION, 1),
        (RunState.AWAITING_PLAN_DECISION, ArtifactKind.PLAN, 2),
    ],
)
@pytest.mark.asyncio
async def test_start_restores_missing_artifact_decision(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    state: RunState,
    kind: ArtifactKind,
    revision: int,
) -> None:
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            requirement_type=RequirementType.TEXT,
            requirement_text="Restore a decision",
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=state,
            active_spec_revision=revision if kind == ArtifactKind.SPECIFICATION else 1,
            active_plan_revision=revision if kind == ArtifactKind.PLAN else None,
        )
        session.add(run)
        await session.flush()
        artifact = Artifact(
            run_id=run.id,
            kind=kind,
            revision=revision,
            structured_data={},
            rendered_markdown="Artifact",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.commit()
        thread_id = run.thread_id
        artifact_id = artifact.id

    context = RecordingContext()
    executor = run_workflow.RunWorkflowExecutor(thread_id)
    await executor.start(
        "Start",
        cast(WorkflowContext[Any, str], context),
    )

    assert len(context.requests) == 1
    request, request_id = context.requests[0]
    assert request.artifact_id == artifact_id
    assert request.artifact_kind == kind.value
    assert request.revision == revision
    assert request_id.startswith(f"{kind.value}-{artifact_id}-restore-")


@pytest.mark.asyncio
async def test_accepting_revised_plan_preserves_open_pull_request_gate(
    workflow_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    source_sha = "a" * 40
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            requirement_type=RequirementType.TEXT,
            requirement_text="Adjust the specification",
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.AWAITING_PLAN_DECISION,
            active_spec_revision=2,
            active_plan_revision=3,
        )
        session.add(run)
        await session.flush()
        snapshot = SourceSnapshot(
            run_id=run.id,
            git_sha=source_sha,
            reason="plan-r3",
            manifest={},
            instructions=[],
            worktree_path="/tmp/worktree",
        )
        session.add(snapshot)
        await session.flush()
        plan = ImplementationPlan.model_validate(
            {
                "specification_revision": 2,
                "source_sha": source_sha,
                "summary": "Preserve the open phase, then continue.",
                "system_findings": [],
                "architecture_decisions": [],
                "phases": [
                    {
                        "ordinal": 1,
                        "title": "Existing pull request",
                        "objective": "Preserve in-flight work",
                        "dependencies": [],
                        "scope": ["existing"],
                        "likely_files": [],
                        "implementation_steps": ["Wait for merge"],
                        "tests": ["Use existing validation"],
                        "migration_and_rollout": [],
                        "risks": [],
                        "acceptance_criteria": ["The pull request merges"],
                    },
                    {
                        "ordinal": 2,
                        "title": "Follow-up",
                        "objective": "Implement the revised requirement",
                        "dependencies": [1],
                        "scope": ["follow-up"],
                        "likely_files": [],
                        "implementation_steps": ["Implement follow-up"],
                        "tests": ["Test follow-up"],
                        "migration_and_rollout": [],
                        "risks": [],
                        "acceptance_criteria": ["The follow-up works"],
                    },
                ],
                "cross_phase_invariants": ["Preserve compatibility"],
                "completion_definition": ["Both phases are merged"],
                "unresolved_assumptions": [],
            }
        )
        artifact = Artifact(
            run_id=run.id,
            source_snapshot_id=snapshot.id,
            kind=ArtifactKind.PLAN,
            revision=3,
            structured_data=plan.model_dump(mode="json"),
            rendered_markdown="Plan",
            model=run.primary_model,
        )
        session.add(artifact)
        session.add(
            Phase(
                run_id=run.id,
                ordinal=1,
                title="Existing pull request",
                objective="Preserve in-flight work",
                dependencies=[],
                details=plan.phases[0].model_dump(mode="json"),
                status=PhaseState.WAITING_FOR_MERGE,
                plan_revision=2,
                source_sha=source_sha,
                pr_number=42,
                pr_url="https://github.com/octo/repo/pull/42",
            )
        )
        await session.commit()
        thread_id = run.thread_id

    context = RecordingContext()
    executor = run_workflow.RunWorkflowExecutor(thread_id)
    await executor._accept_plan(  # pyright: ignore[reportPrivateUsage]
        run.id,
        artifact,
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        revised_run = await session.get(Run, run.id)
        phases = list(
            await session.scalars(
                select(Phase).where(Phase.run_id == run.id).order_by(Phase.ordinal)
            )
        )
    assert revised_run is not None
    assert revised_run.state == RunState.WAITING_FOR_MERGE
    assert [(phase.ordinal, phase.status) for phase in phases] == [
        (1, PhaseState.WAITING_FOR_MERGE),
        (2, PhaseState.PENDING),
    ]
    assert context.outputs == [
        "Plan accepted; pull request 42 must merge before the next phase can start."
    ]


@pytest.mark.asyncio
async def test_stale_artifact_decision_is_ignored_after_revision_changes(
    workflow_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            requirement_type=RequirementType.TEXT,
            requirement_text="Ignore a stale decision",
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.AWAITING_SPEC_DECISION,
            active_spec_revision=2,
        )
        session.add(run)
        await session.flush()
        stale_artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data={},
            rendered_markdown="Old specification",
            model=run.primary_model,
        )
        session.add(stale_artifact)
        await session.commit()
        run_id = run.id
        thread_id = run.thread_id
        artifact_id = stale_artifact.id

    context = RecordingContext()
    executor = run_workflow.RunWorkflowExecutor(thread_id)
    await executor.decide_artifact(
        ArtifactDecisionRequest(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_kind="specification",
            revision=1,
            prompt="Stale",
        ),
        {"action": "accept"},
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        unchanged_run = await session.get(Run, run_id)
        reset_decision = await session.scalar(
            select(Decision).where(
                Decision.run_id == run_id,
                Decision.decision_type == DecisionType.ACCEPT,
            )
        )
    assert unchanged_run is not None
    assert unchanged_run.state == RunState.AWAITING_SPEC_DECISION
    assert reset_decision is None
    assert context.outputs == [
        "Ignored a stale artifact decision because the workflow has moved on."
    ]


def phase_decision_request(phase: Phase) -> PhaseDecisionRequest:
    return PhaseDecisionRequest(
        run_id=phase.run_id,
        phase_id=phase.id,
        ordinal=phase.ordinal,
        title=phase.title,
        objective=phase.objective,
        prompt="Start phase?",
    )


async def ready_phase(
    session: AsyncSession,
    *,
    title: str = "Ready phase",
) -> tuple[Run, Phase]:
    repository = Repository(
        owner="octo",
        name=f"repo-{title}",
        remote_url="https://github.com/octo/repo.git",
    )
    session.add(repository)
    await session.flush()
    run = Run(
        repository_id=repository.id,
        requirement_type=RequirementType.TEXT,
        requirement_text="Decide phase",
        primary_model="gpt-5.6-sol",
        reviewer_model="claude-opus-4.8",
        state=RunState.READY_FOR_PHASE,
    )
    session.add(run)
    await session.flush()
    phase = Phase(
        run_id=run.id,
        ordinal=1,
        title=title,
        objective="Verify phase decisions",
        dependencies=[],
        details={},
        status=PhaseState.READY,
        plan_revision=1,
        source_sha="a" * 40,
    )
    session.add(phase)
    await session.commit()
    return run, phase


@pytest.mark.asyncio
async def test_ready_phase_start_executes_without_cancellation_output(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with workflow_session_factory() as session:
        run, phase = await ready_phase(session)

    execute_phase = AsyncMock()
    monkeypatch.setattr("mafia.services.execution.execute_phase", execute_phase)
    context = RecordingContext()

    await run_workflow.RunWorkflowExecutor(run.thread_id).decide_phase(
        phase_decision_request(phase),
        {"action": "start"},
        cast(WorkflowContext[Any, str], context),
    )

    execute_phase.assert_awaited_once_with(run.id, phase.id, context)
    assert "Run cancelled." not in context.outputs


@pytest.mark.asyncio
async def test_phase_start_that_becomes_stale_is_ignored(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with workflow_session_factory() as session:
        run, phase = await ready_phase(session)

    async def become_stale(*_: object) -> None:
        async with workflow_session_factory() as session:
            current_phase = await session.get(Phase, phase.id)
            assert current_phase is not None
            current_phase.status = PhaseState.EXECUTING
            await session.commit()
        raise PhaseExecutionError("Phase is not ready for execution")

    execute_phase = AsyncMock(side_effect=become_stale)
    monkeypatch.setattr("mafia.services.execution.execute_phase", execute_phase)
    context = RecordingContext()

    await run_workflow.RunWorkflowExecutor(run.thread_id).decide_phase(
        phase_decision_request(phase),
        {"action": "start"},
        cast(WorkflowContext[Any, str], context),
    )

    execute_phase.assert_awaited_once_with(run.id, phase.id, context)
    assert context.outputs == [
        "Ignored a stale phase decision because the phase is no longer ready."
    ]


@pytest.mark.asyncio
async def test_stale_phase_start_is_ignored(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with workflow_session_factory() as session:
        run, phase = await ready_phase(session)
        phase.status = PhaseState.EXECUTING
        await session.commit()

    execute_phase = AsyncMock()
    monkeypatch.setattr("mafia.services.execution.execute_phase", execute_phase)
    context = RecordingContext()

    await run_workflow.RunWorkflowExecutor(run.thread_id).decide_phase(
        phase_decision_request(phase),
        {"action": "start"},
        cast(WorkflowContext[Any, str], context),
    )

    assert context.outputs == [
        "Ignored a stale phase decision because the phase is no longer ready."
    ]
    execute_phase.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_phase_cancel_is_ignored(
    workflow_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with workflow_session_factory() as session:
        run, phase = await ready_phase(session)
        run.state = RunState.EXECUTING_PHASE
        await session.commit()

    context = RecordingContext()
    await run_workflow.RunWorkflowExecutor(run.thread_id).decide_phase(
        phase_decision_request(phase),
        {"action": "cancel"},
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        unchanged_run = await session.get(Run, run.id)
    assert unchanged_run is not None
    assert unchanged_run.state == RunState.EXECUTING_PHASE
    assert context.outputs == [
        "Ignored a stale phase decision because the phase is no longer ready."
    ]


@pytest.mark.asyncio
async def test_phase_decision_for_another_run_is_ignored(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with workflow_session_factory() as session:
        run, _ = await ready_phase(session, title="First phase")
        _, phase = await ready_phase(session, title="Second phase")

    execute_phase = AsyncMock()
    monkeypatch.setattr("mafia.services.execution.execute_phase", execute_phase)
    context = RecordingContext()
    request = phase_decision_request(phase).model_copy(update={"run_id": run.id})

    await run_workflow.RunWorkflowExecutor(run.thread_id).decide_phase(
        request,
        {"action": "start"},
        cast(WorkflowContext[Any, str], context),
    )

    assert context.outputs == [
        "Ignored a stale phase decision because the phase is no longer ready."
    ]
    execute_phase.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase_cancel_records_a_decision(
    workflow_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with workflow_session_factory() as session:
        run, phase = await ready_phase(session)

    context = RecordingContext()
    await run_workflow.RunWorkflowExecutor(run.thread_id).decide_phase(
        phase_decision_request(phase),
        {"action": "cancel"},
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        cancelled_run = await session.get(Run, run.id)
        decision = await session.scalar(
            select(Decision).where(
                Decision.run_id == run.id,
                Decision.phase_id == phase.id,
                Decision.decision_type == DecisionType.CANCEL,
            )
        )
    assert cancelled_run is not None
    assert cancelled_run.state == RunState.CANCELLED
    assert decision is not None


class PullRequestReviewContext:
    def __init__(self) -> None:
        self.requests: list[tuple[PullRequestReviewDecisionRequest, str]] = []
        self.outputs: list[str] = []
        self.events: list[object] = []

    async def request_info(
        self,
        request: PullRequestReviewDecisionRequest,
        response_type: type[dict[str, Any]],
        *,
        request_id: str,
    ) -> None:
        assert response_type is dict
        self.requests.append((request, request_id))

    async def yield_output(self, output: str) -> None:
        self.outputs.append(output)

    async def add_event(self, event: object) -> None:
        self.events.append(event)


def empty_review() -> PullRequestReview:
    return PullRequestReview(
        summary="No actionable defects found.",
        verdict="approve",
        findings=[],
        strengths=["The change is focused."],
        testing_assessment="The existing tests cover the change.",
    )


class FakePullRequestReviewService(PullRequestReviewService):
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.models: list[str] = []

    async def review(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        context: dict[str, Any],
        *,
        model: str,
    ) -> PullRequestReview:
        del run, snapshot, context
        self.models.append(model)
        return empty_review()

    async def persist_review(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        review: PullRequestReview,
        *,
        model: str,
    ) -> Artifact:
        async with self.factory() as session:
            revision = len(
                list(
                    await session.scalars(
                        select(Artifact).where(
                            Artifact.run_id == run.id,
                            Artifact.kind == ArtifactKind.PULL_REQUEST_REVIEW,
                        )
                    )
                )
            ) + 1
            artifact = Artifact(
                run_id=run.id,
                source_snapshot_id=snapshot.id,
                kind=ArtifactKind.PULL_REQUEST_REVIEW,
                revision=revision,
                structured_data=review.model_dump(mode="json"),
                rendered_markdown="Review",
                model=model,
            )
            session.add(artifact)
            await session.commit()
            return artifact

    async def consolidate(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        context: dict[str, Any],
        reviews: list[Artifact],
    ) -> Artifact:
        del context, reviews
        consolidated = ConsolidatedPullRequestReview(
            pull_request_number=run.pull_request_number or 0,
            head_sha=snapshot.git_sha,
            summary="No actionable defects found.",
            verdict="approve",
            findings=[],
            strengths=["The change is focused."],
            testing_assessment="The existing tests cover the change.",
            dispositions=[],
        )
        async with self.factory() as session:
            artifact = Artifact(
                run_id=run.id,
                source_snapshot_id=snapshot.id,
                kind=ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED,
                revision=1,
                structured_data=consolidated.model_dump(mode="json"),
                rendered_markdown="# Consolidated review",
                model=run.primary_model,
            )
            session.add(artifact)
            await session.commit()
            return artifact


@pytest.mark.asyncio
async def test_pull_request_review_runs_both_models_and_awaits_post_decision(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            workflow_type=WorkflowType.PULL_REQUEST_REVIEW,
            requirement_type=None,
            pull_request_number=42,
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.INTAKE,
        )
        session.add(run)
        await session.commit()
        run_id = run.id
        thread_id = run.thread_id

    async def capture(
        session: AsyncSession,
        run: Run,
        repository: Repository,
    ) -> tuple[SourceSnapshot, dict[str, Any]]:
        del repository
        context = {
            "number": 42,
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "changed_files": 1,
            "files": [{"filename": "app.py"}],
        }
        snapshot = SourceSnapshot(
            run_id=run.id,
            git_sha="b" * 40,
            reason="pr-review-42",
            issue_data=context,
            manifest={"files": ["app.py"]},
            instructions=[],
            worktree_path=str(tmp_path),
        )
        session.add(snapshot)
        await session.commit()
        return snapshot, context

    monkeypatch.setattr(run_workflow, "capture_pull_request_snapshot", capture)

    async def missing_repository_configuration(
        *args: object, **kwargs: object
    ) -> CommandResult:
        return CommandResult(
            argv=("git",),
            returncode=128,
            stdout="",
            stderr="fatal: path '.mafia.toml' does not exist in base",
        )

    monkeypatch.setattr(run_workflow, "run_command", missing_repository_configuration)
    service = FakePullRequestReviewService(workflow_session_factory)
    context = PullRequestReviewContext()
    executor = run_workflow.RunWorkflowExecutor(
        thread_id,
        review_service=service,
    )

    await executor.start(
        "Start",
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        reviewed_run = await session.get(Run, run_id)
    assert reviewed_run is not None
    assert reviewed_run.state == RunState.AWAITING_PR_REVIEW_DECISION
    assert reviewed_run.active_review_revision == 1
    assert set(service.models) == {"gpt-5.6-sol", "claude-opus-4.8"}
    assert context.requests[0][0].pull_request_number == 42


@pytest.mark.asyncio
async def test_pull_request_review_can_finish_without_posting(
    workflow_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    review = ConsolidatedPullRequestReview(
        pull_request_number=42,
        head_sha="b" * 40,
        summary="No actionable defects found.",
        verdict="approve",
        findings=[],
        strengths=[],
        testing_assessment="Tests are sufficient.",
        dispositions=[],
    )
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            workflow_type=WorkflowType.PULL_REQUEST_REVIEW,
            requirement_type=None,
            pull_request_number=42,
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.AWAITING_PR_REVIEW_DECISION,
            active_review_revision=1,
        )
        session.add(run)
        await session.flush()
        artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED,
            revision=1,
            structured_data=review.model_dump(mode="json"),
            rendered_markdown="# Review",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.commit()
        request = PullRequestReviewDecisionRequest(
            run_id=run.id,
            artifact_id=artifact.id,
            revision=1,
            pull_request_number=42,
            prompt="Finish?",
        )
        thread_id = run.thread_id

    context = PullRequestReviewContext()
    executor = run_workflow.RunWorkflowExecutor(thread_id)
    await executor.decide_pull_request_review(
        request,
        {"action": "finish"},
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        finished = await session.get(Run, request.run_id)
        decision = await session.scalar(
            select(Decision).where(
                Decision.run_id == request.run_id,
                Decision.decision_type == DecisionType.FINISH_REVIEW,
            )
        )
    assert finished is not None
    assert finished.state == RunState.COMPLETED
    assert decision is not None


@pytest.mark.asyncio
async def test_pull_request_review_can_post_consolidated_comment(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, object]] = []
    review = ConsolidatedPullRequestReview(
        pull_request_number=42,
        head_sha="b" * 40,
        summary="One consolidated review.",
        verdict="comment",
        findings=[],
        strengths=[],
        testing_assessment="Tests are sufficient.",
        dispositions=[],
    )
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            workflow_type=WorkflowType.PULL_REQUEST_REVIEW,
            requirement_type=None,
            pull_request_number=42,
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.AWAITING_PR_REVIEW_DECISION,
            active_review_revision=1,
        )
        session.add(run)
        await session.flush()
        artifact = Artifact(
            run_id=run.id,
            kind=ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED,
            revision=1,
            structured_data=review.model_dump(mode="json"),
            rendered_markdown="# Consolidated review",
            model=run.primary_model,
        )
        session.add(artifact)
        await session.commit()
        request = PullRequestReviewDecisionRequest(
            run_id=run.id,
            artifact_id=artifact.id,
            revision=1,
            pull_request_number=42,
            prompt="Post?",
        )
        thread_id = run.thread_id

    async def post_comment(
        identity: object,
        number: int,
        **values: object,
    ) -> str:
        posted.append({"identity": identity, "number": number, **values})
        return "https://github.com/octo/repo/pull/42#issuecomment-1"

    monkeypatch.setattr(run_workflow, "post_pull_request_comment", post_comment)
    context = PullRequestReviewContext()
    executor = run_workflow.RunWorkflowExecutor(thread_id)
    await executor.decide_pull_request_review(
        request,
        {"action": "post"},
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        finished = await session.get(Run, request.run_id)
        decision = await session.scalar(
            select(Decision).where(
                Decision.run_id == request.run_id,
                Decision.decision_type == DecisionType.POST_REVIEW,
            )
        )
    assert finished is not None
    assert finished.state == RunState.COMPLETED
    assert decision is not None
    assert posted[0]["number"] == 42
    assert posted[0]["markdown"] == "# Consolidated review"
    assert context.outputs == [
        "Pull-request review posted: "
        "https://github.com/octo/repo/pull/42#issuecomment-1"
    ]


@pytest.mark.asyncio
async def test_failed_phase_retry_starts_a_new_review_cycle(
    workflow_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mafia.services import lifecycle

    monkeypatch.setattr(lifecycle, "SessionFactory", workflow_session_factory)
    async with workflow_session_factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            requirement_type=RequirementType.TEXT,
            requirement_text="Retry the failed implementation review",
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.FAILED,
            failure_code="implementation_review_failed",
            failure_message="IMP-1 remains unresolved",
        )
        session.add(run)
        await session.flush()
        phase = Phase(
            run_id=run.id,
            ordinal=1,
            title="Bounded review",
            objective="Retry explicitly",
            dependencies=[],
            details={},
            status=PhaseState.FAILED,
            plan_revision=1,
            source_sha="a" * 40,
            review_cycle=3,
            implementation_review_attempts=1,
            remediation_attempts=1,
            verification_attempts=1,
            candidate_base_sha="a" * 40,
            candidate_diff_hash="b" * 64,
        )
        session.add(phase)
        await session.commit()
        run_id = run.id
        phase_id = phase.id
        thread_id = run.thread_id

    context = RecordingContext()
    await run_workflow.RunWorkflowExecutor(thread_id).start(
        "Retry",
        cast(WorkflowContext[Any, str], context),
    )

    async with workflow_session_factory() as session:
        retried_run = await session.get(Run, run_id)
        retried_phase = await session.get(Phase, phase_id)
    assert retried_run is not None
    assert retried_phase is not None
    assert retried_run.state == RunState.READY_FOR_PHASE
    assert retried_phase.status == PhaseState.READY
    assert retried_phase.review_cycle == 4
    assert retried_phase.implementation_review_attempts == 0
    assert retried_phase.remediation_attempts == 0
    assert retried_phase.verification_attempts == 0
    assert retried_phase.candidate_base_sha is None
    assert retried_phase.candidate_diff_hash is None

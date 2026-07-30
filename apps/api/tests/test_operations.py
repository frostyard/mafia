import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from mafia.db.base import Base
from mafia.db.models import (
    AGUISnapshot,
    Artifact,
    AuditEvent,
    Decision,
    Operation,
    Phase,
    Repository,
    Run,
    SourceSnapshot,
)
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    PhaseState,
    RequirementType,
    RunState,
)
from mafia.services import activity, operations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

OperationSessionFixture = tuple[async_sessionmaker[AsyncSession], str]


@pytest.fixture
async def operation_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[OperationSessionFixture]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(operations, "SessionFactory", factory)
    monkeypatch.setattr(activity, "SessionFactory", factory)
    async with factory() as session:
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
            requirement_text="Test observability",
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.GENERATING_PLAN,
        )
        session.add(run)
        await session.commit()
    try:
        yield factory, run.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_run_guard_covers_operation_gaps() -> None:
    async with operations.active_run_work("run-guard"):
        assert operations.has_active_work("run-guard") is True

    assert operations.has_active_work("run-guard") is False


@pytest.mark.asyncio
async def test_tracked_operation_persists_completion(
    operation_session_factory: OperationSessionFixture,
) -> None:
    factory, run_id = operation_session_factory

    async with operations.tracked_operation(
        run_id=run_id,
        operation_type="model.plan_generation",
        operation_key="snapshot-1",
        model="gpt-5.6-sol",
        timeout_seconds=900,
        detail={"source_sha": "abc123"},
    ) as operation:
        operation.set_result({"artifact_id": "artifact-1"})

    async with factory() as session:
        row = await session.scalar(select(Operation).where(Operation.run_id == run_id))
    assert row is not None
    assert row.status == "completed"
    assert row.model == "gpt-5.6-sol"
    assert row.result == {"artifact_id": "artifact-1"}
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_tracked_operation_persists_cancellation_when_caller_is_cancelled_again(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    operation_started = asyncio.Event()
    terminal_started = asyncio.Event()
    release_terminal = asyncio.Event()
    original_record_terminal = operations._record_terminal  # pyright: ignore[reportPrivateUsage]

    async def delayed_record_terminal(*args: object, **kwargs: object) -> None:
        terminal_started.set()
        await release_terminal.wait()
        await original_record_terminal(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(operations, "_record_terminal", delayed_record_terminal)

    async def run_operation() -> None:
        async with operations.tracked_operation(
            run_id=run_id,
            operation_type="model.plan_generation",
            operation_key="cancelled-snapshot",
        ):
            operation_started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run_operation())
    await operation_started.wait()
    task.cancel()
    await terminal_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release_terminal.set()

    row: Operation | None = None
    for _ in range(100):
        async with factory() as session:
            row = await session.scalar(
                select(Operation).where(
                    Operation.idempotency_key
                    == f"{run_id}:-:model.plan_generation:cancelled-snapshot"
                )
            )
        if row is not None and row.status == "cancelled":
            break
        await asyncio.sleep(0.01)

    assert row is not None
    assert row.status == "cancelled"
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_activity_detects_stale_heartbeat(
    operation_session_factory: OperationSessionFixture,
) -> None:
    factory, run_id = operation_session_factory
    stale = datetime.now(UTC) - timedelta(minutes=10)
    async with factory() as session:
        session.add(
            SourceSnapshot(
                run_id=run_id,
                git_sha="a" * 40,
                reason="plan-r1",
                manifest={"files": ["src/app.py"]},
                instructions=[],
                worktree_path="/tmp/worktree",
            )
        )
        session.add(
            Operation(
                idempotency_key=f"{run_id}:-:model.plan_generation:snapshot-1",
                run_id=run_id,
                operation_type="model.plan_generation",
                request_hash="b" * 64,
                status="running",
                model="gpt-5.6-sol",
                attempt=1,
                timeout_seconds=900,
                heartbeat_at=datetime.now(UTC),
                progress_at=stale,
                detail={},
            )
        )
        await session.commit()

    result = await activity.get_run_activity(run_id)

    assert result.stalled is True
    assert result.can_retry is True
    assert result.status_mode == "working"
    assert result.stall_reason is not None
    assert "backend is alive" in result.stall_reason
    assert result.source_sha == "a" * 40


@pytest.mark.asyncio
async def test_stalled_retry_closes_operation_and_fails_run(
    operation_session_factory: OperationSessionFixture,
) -> None:
    factory, run_id = operation_session_factory
    stale = datetime.now(UTC) - timedelta(minutes=10)
    async with factory() as session:
        operation = Operation(
            idempotency_key=f"{run_id}:-:model.plan_generation:snapshot-1",
            run_id=run_id,
            operation_type="model.plan_generation",
            request_hash="b" * 64,
            status="running",
            model="gpt-5.6-sol",
            attempt=1,
            timeout_seconds=900,
            heartbeat_at=stale,
            progress_at=stale,
            detail={},
        )
        session.add(operation)
        await session.commit()
        operation_id = operation.id

    result = await activity.prepare_retry(run_id)

    assert result.state == RunState.FAILED
    async with factory() as session:
        operation = await session.get(Operation, operation_id)
    assert operation is not None
    assert operation.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_timeout_keeps_working_state(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    monkeypatch.setattr(
        activity,
        "_wait_for_active_work",
        AsyncMock(side_effect=activity.RunControlError("still stopping")),
    )

    with pytest.raises(activity.RunControlError, match="still stopping"):
        await activity.cancel_run(run_id)

    async with factory() as session:
        run = await session.get(Run, run_id)
    assert run is not None
    assert run.state == RunState.GENERATING_PLAN


@pytest.mark.asyncio
async def test_cancel_preserves_naturally_completed_state(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        expected_version = run.version + 1

    async def finish_work(run_id: str) -> None:
        async with factory() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            await activity.transition_run(
                session,
                run.id,
                RunState.FAILED,
                expected_version=run.version,
                event_type="run.naturally_completed",
            )

    monkeypatch.setattr(activity, "_wait_for_active_work", finish_work)

    result = await activity.cancel_run(run_id)

    assert result.state == RunState.FAILED
    assert result.version == expected_version
    async with factory() as session:
        cancel_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id,
                AuditEvent.event_type == "run.cancel_requested",
            )
        )
    assert cancel_event is None


@pytest.mark.asyncio
async def test_stalled_retry_timeout_keeps_working_state(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    stale = datetime.now(UTC) - timedelta(minutes=10)
    async with factory() as session:
        session.add(
            Operation(
                idempotency_key=f"{run_id}:-:model.plan_generation:timeout",
                run_id=run_id,
                operation_type="model.plan_generation",
                request_hash="b" * 64,
                status="running",
                model="gpt-5.6-sol",
                attempt=1,
                timeout_seconds=900,
                heartbeat_at=stale,
                progress_at=stale,
                detail={},
            )
        )
        await session.commit()
    monkeypatch.setattr(
        activity,
        "_wait_for_active_work",
        AsyncMock(side_effect=activity.RunControlError("still stopping")),
    )

    with pytest.raises(activity.RunControlError, match="still stopping"):
        await activity.prepare_retry(run_id)

    async with factory() as session:
        run = await session.get(Run, run_id)
    assert run is not None
    assert run.state == RunState.GENERATING_PLAN


@pytest.mark.asyncio
async def test_stalled_retry_preserves_naturally_completed_state_without_retry_ready_event(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    stale = datetime.now(UTC) - timedelta(minutes=10)
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        expected_version = run.version + 1
        session.add(
            Operation(
                idempotency_key=f"{run_id}:-:model.plan_generation:natural-completion",
                run_id=run_id,
                operation_type="model.plan_generation",
                request_hash="b" * 64,
                status="running",
                model="gpt-5.6-sol",
                attempt=1,
                timeout_seconds=900,
                heartbeat_at=stale,
                progress_at=stale,
                detail={},
            )
        )
        await session.commit()

    async def finish_work(run_id: str) -> None:
        async with factory() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            await activity.transition_run(
                session,
                run.id,
                RunState.FAILED,
                expected_version=run.version,
                event_type="run.naturally_completed",
            )

    monkeypatch.setattr(activity, "_wait_for_active_work", finish_work)

    result = await activity.prepare_retry(run_id)

    assert result.state == RunState.FAILED
    assert result.version == expected_version
    async with factory() as session:
        retry_ready = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id,
                AuditEvent.event_type == "run.retry_ready",
            )
        )
    assert retry_ready is None


@pytest.mark.asyncio
async def test_reset_to_specification_rotates_thread_and_invalidates_future_work(
    operation_session_factory: OperationSessionFixture,
) -> None:
    factory, run_id = operation_session_factory
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.state = RunState.READY_FOR_PHASE
        run.active_spec_revision = 1
        run.active_plan_revision = 2
        specification = Artifact(
            run_id=run.id,
            kind=ArtifactKind.SPECIFICATION,
            revision=1,
            structured_data={},
            rendered_markdown="Specification",
            model=run.primary_model,
        )
        session.add(specification)
        for ordinal, phase_state in [
            (1, PhaseState.MERGED),
            (2, PhaseState.FAILED),
            (3, PhaseState.PENDING),
            (4, PhaseState.READY),
        ]:
            session.add(
                Phase(
                    run_id=run.id,
                    ordinal=ordinal,
                    title=f"Phase {ordinal}",
                    objective="Test reset behavior",
                    dependencies=[],
                    details={},
                    status=phase_state,
                    plan_revision=2,
                    source_sha="a" * 40,
                    pr_number=42 if ordinal == 2 else None,
                    pr_url=(
                        "https://github.com/octo/repo/pull/42"
                        if ordinal == 2
                        else None
                    ),
                    merge_sha="b" * 40 if phase_state == PhaseState.MERGED else None,
                )
            )
        old_thread_id = run.thread_id
        session.add(
            AGUISnapshot(
                scope="local-user",
                thread_id=old_thread_id,
                snapshot={},
            )
        )
        await session.commit()

    reset = await activity.reset_to_specification(run_id)

    assert reset.state == RunState.AWAITING_SPEC_DECISION
    assert reset.active_plan_revision is None
    assert reset.thread_id != old_thread_id
    async with factory() as session:
        phases = list(
            await session.scalars(
                select(Phase).where(Phase.run_id == run_id).order_by(Phase.ordinal)
            )
        )
        decision = await session.scalar(
            select(Decision).where(
                Decision.run_id == run_id,
                Decision.decision_type == DecisionType.RESET_SPECIFICATION,
            )
        )
        snapshot = await session.scalar(
            select(AGUISnapshot).where(AGUISnapshot.thread_id == old_thread_id)
        )
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id,
                AuditEvent.event_type == "specification.reset",
            )
        )
    assert [(phase.ordinal, phase.status) for phase in phases] == [
        (1, PhaseState.MERGED),
        (2, PhaseState.WAITING_FOR_MERGE),
    ]
    assert decision is not None
    assert snapshot is None
    assert event is not None
    assert event.payload["invalidated_phase_ordinals"] == [3, 4]
    assert event.payload["preserved_phase_ordinals"] == [1, 2]


@pytest.mark.asyncio
async def test_specification_reset_timeout_keeps_active_thread_and_state(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.active_spec_revision = 1
        original_thread_id = run.thread_id
        session.add(
            Artifact(
                run_id=run.id,
                kind=ArtifactKind.SPECIFICATION,
                revision=1,
                structured_data={},
                rendered_markdown="Specification",
                model=run.primary_model,
            )
        )
        await session.commit()
    monkeypatch.setattr(
        activity,
        "_wait_for_active_work",
        AsyncMock(side_effect=activity.RunControlError("still stopping")),
    )

    with pytest.raises(activity.RunControlError, match="still stopping"):
        await activity.reset_to_specification(run_id)

    async with factory() as session:
        run = await session.get(Run, run_id)
    assert run is not None
    assert run.state == RunState.GENERATING_PLAN
    assert run.thread_id == original_thread_id


@pytest.mark.asyncio
async def test_specification_reset_preserves_naturally_completed_state(
    operation_session_factory: OperationSessionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run_id = operation_session_factory
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.active_spec_revision = 1
        original_thread_id = run.thread_id
        expected_version = run.version + 1
        session.add(
            Artifact(
                run_id=run.id,
                kind=ArtifactKind.SPECIFICATION,
                revision=1,
                structured_data={},
                rendered_markdown="Specification",
                model=run.primary_model,
            )
        )
        await session.commit()

    async def finish_work(run_id: str) -> None:
        async with factory() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            await activity.transition_run(
                session,
                run.id,
                RunState.FAILED,
                expected_version=run.version,
                event_type="run.naturally_completed",
            )

    monkeypatch.setattr(activity, "_wait_for_active_work", finish_work)

    reset = await activity.reset_to_specification(run_id)

    assert reset.state == RunState.FAILED
    assert reset.version == expected_version
    assert reset.thread_id == original_thread_id
    async with factory() as session:
        reset_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id,
                AuditEvent.event_type == "specification.reset",
            )
        )
    assert reset_event is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_state",
    [RunState.FAILED, RunState.COMPLETED, RunState.CANCELLED],
)
async def test_reset_to_specification_continues_after_draining_nonworking_run(
    operation_session_factory: OperationSessionFixture,
    initial_state: RunState,
) -> None:
    factory, run_id = operation_session_factory
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.state = initial_state
        run.active_spec_revision = 1
        original_thread_id = run.thread_id
        session.add(
            Artifact(
                run_id=run.id,
                kind=ArtifactKind.SPECIFICATION,
                revision=1,
                structured_data={},
                rendered_markdown="Specification",
                model=run.primary_model,
            )
        )
        await session.commit()

    started = asyncio.Event()

    async def active_work() -> None:
        async with operations.active_run_work(run_id):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(active_work())
    await started.wait()

    reset = await activity.reset_to_specification(run_id)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert reset.state == RunState.AWAITING_SPEC_DECISION
    assert reset.thread_id != original_thread_id
    assert operations.has_active_work(run_id) is False
    async with factory() as session:
        cancellation_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.run_id == run_id,
                AuditEvent.event_type == "specification.reset_cancel_requested",
            )
        )
    assert cancellation_event is None


@pytest.mark.asyncio
async def test_reset_to_specification_cancels_active_work(
    operation_session_factory: OperationSessionFixture,
) -> None:
    factory, run_id = operation_session_factory
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.active_spec_revision = 1
        session.add(
            Artifact(
                run_id=run.id,
                kind=ArtifactKind.SPECIFICATION,
                revision=1,
                structured_data={},
                rendered_markdown="Specification",
                model=run.primary_model,
            )
        )
        await session.commit()

    started = asyncio.Event()

    async def active_work() -> None:
        async with operations.active_run_work(run_id):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(active_work())
    await started.wait()
    reset = await activity.reset_to_specification(run_id)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert reset.state == RunState.AWAITING_SPEC_DECISION
    assert operations.has_active_work(run_id) is False

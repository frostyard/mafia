import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from mafia.db.base import Base
from mafia.db.models import Decision, PendingAction, Phase, Repository, Run
from mafia.domain.enums import DecisionType, PendingActionKind, PhaseState, RequirementType, RunState
from mafia.domain.schemas import DecisionSubmission
from mafia.services import activity, operations, run_control
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def phase_action_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[async_sessionmaker[AsyncSession], Run, Phase]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_control, "SessionFactory", factory)
    monkeypatch.setattr(activity, "SessionFactory", factory)
    async with factory() as session:
        repository = Repository(owner="octo", name="repo", remote_url="https://github.com/octo/repo.git")
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            requirement_type=RequirementType.TEXT,
            requirement_text="Add a durable action",
            primary_model="gpt-5.6-sol",
            reviewer_model="claude-opus-4.8",
            state=RunState.READY_FOR_PHASE,
        )
        session.add(run)
        await session.flush()
        phase = Phase(
            run_id=run.id,
            ordinal=1,
            title="Implementation",
            objective="Persist the control-plane action",
            dependencies=[],
            details={},
            status=PhaseState.READY,
            plan_revision=1,
            source_sha="a" * 40,
        )
        session.add(phase)
        await session.commit()
        yield factory, run, phase
    await engine.dispose()


@pytest.mark.asyncio
async def test_ready_phase_requires_configuration_when_validation_is_unavailable(
    phase_action_session_factory: tuple[async_sessionmaker[AsyncSession], Run, Phase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run, phase = phase_action_session_factory
    monkeypatch.setattr(run_control, "source_validation_status", AsyncMock(return_value=(False, "missing")))

    await run_control.create_phase_pending_action(run.id, phase.id)

    async with factory() as session:
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert action is not None
    assert action.kind == PendingActionKind.CONFIGURATION_REQUIRED
    assert action.phase_id == phase.id
    assert action.payload == {
        "message": "Phase 1 cannot start until deterministic validation is configured for octo/repo.",
        "project_id": run.repository_id,
        "project_href": f"/projects/{run.repository_id}",
    }


@pytest.mark.asyncio
async def test_ready_phase_creates_start_action_when_validation_is_available(
    phase_action_session_factory: tuple[async_sessionmaker[AsyncSession], Run, Phase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run, phase = phase_action_session_factory
    monkeypatch.setattr(run_control, "source_validation_status", AsyncMock(return_value=(True, "repository")))

    await run_control.create_phase_pending_action(run.id, phase.id)

    async with factory() as session:
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert action is not None
    assert action.kind == PendingActionKind.PHASE
    assert action.phase_id == phase.id


@pytest.mark.asyncio
async def test_check_again_replaces_only_the_matching_configuration_action(
    phase_action_session_factory: tuple[async_sessionmaker[AsyncSession], Run, Phase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run, phase = phase_action_session_factory
    status = AsyncMock(side_effect=[(False, "missing"), (True, "host")])
    monkeypatch.setattr(run_control, "source_validation_status", status)
    await run_control.create_phase_pending_action(run.id, phase.id)
    async with factory() as session:
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert action is not None

    await run_control.submit_decision(run.id, action.id, DecisionSubmission(action="check_again"))

    async with factory() as session:
        replacement = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert replacement is not None
    assert replacement.id != action.id
    assert replacement.kind == PendingActionKind.PHASE
    assert replacement.phase_id == phase.id


@pytest.mark.asyncio
async def test_configuration_required_cancel_consumes_action_and_cancels_run(
    phase_action_session_factory: tuple[async_sessionmaker[AsyncSession], Run, Phase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run, phase = phase_action_session_factory
    monkeypatch.setattr(run_control, "source_validation_status", AsyncMock(return_value=(False, "missing")))
    await run_control.create_phase_pending_action(run.id, phase.id)
    async with factory() as session:
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert action is not None

    await run_control.submit_decision(run.id, action.id, DecisionSubmission(action="cancel"))

    async with factory() as session:
        cancelled = await session.get(Run, run.id)
        decision = await session.scalar(select(Decision).where(Decision.run_id == run.id))
    assert cancelled is not None and cancelled.state == RunState.CANCELLED
    assert decision is not None and decision.decision_type == DecisionType.CANCEL


@pytest.mark.asyncio
async def test_start_phase_records_decision_and_consumes_pending_action(
    phase_action_session_factory: tuple[async_sessionmaker[AsyncSession], Run, Phase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run, phase = phase_action_session_factory
    monkeypatch.setattr(run_control, "source_validation_status", AsyncMock(return_value=(True, "repository")))
    launched: list[str] = []

    def launch(run_id: str, work: Callable[[], Awaitable[None]]) -> None:
        del work
        launched.append(run_id)

    monkeypatch.setattr(run_control, "launch_background_work", launch)
    await run_control.create_phase_pending_action(run.id, phase.id)
    async with factory() as session:
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert action is not None

    await run_control.submit_decision(run.id, action.id, DecisionSubmission(action="start"))

    async with factory() as session:
        pending = await session.get(PendingAction, action.id)
        decision = await session.scalar(select(Decision).where(Decision.run_id == run.id))
    assert pending is None
    assert decision is not None
    assert decision.decision_type == DecisionType.START_PHASE
    assert launched == [run.id]


@pytest.mark.asyncio
async def test_phase_start_with_active_work_has_no_side_effects(
    phase_action_session_factory: tuple[async_sessionmaker[AsyncSession], Run, Phase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, run, phase = phase_action_session_factory
    monkeypatch.setattr(run_control, "source_validation_status", AsyncMock(return_value=(True, "repository")))
    await run_control.create_phase_pending_action(run.id, phase.id)
    async with factory() as session:
        action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert action is not None

    release = asyncio.Event()

    async def producer() -> None:
        await release.wait()

    operations.launch_background_work(run.id, producer)
    with pytest.raises(operations.ActiveWorkError):
        await run_control.submit_decision(run.id, action.id, DecisionSubmission(action="start"))
    async with factory() as session:
        unchanged = await session.get(Run, run.id)
        persisted_action = await session.get(PendingAction, action.id)
        decisions = list(await session.scalars(select(Decision).where(Decision.run_id == run.id)))
    assert unchanged is not None and unchanged.state == RunState.READY_FOR_PHASE
    assert persisted_action is not None
    assert decisions == []
    release.set()
    await asyncio.sleep(0)

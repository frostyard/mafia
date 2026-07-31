from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mafia.db.base import Base
from mafia.db.models import Decision, PendingAction, Phase, Repository, Run
from mafia.domain.enums import DecisionType, PendingActionKind, PhaseState, RequirementType, RunState
from mafia.domain.schemas import DecisionSubmission
from mafia.services import activity, run_control
from mafia.services.commands import CommandResult
from mafia.services.sandbox import SandboxResult
from mafia.workflows import run_workflow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class FailingEnvironment:
    kind = "test"

    def activity_snapshot(self) -> dict[str, object]:
        return {}

    async def run(
        self, command: str, *, timeout_seconds: float | None = None
    ) -> SandboxResult:
        raise NotImplementedError

    def read_file(self, path: str, line_start: int = 1, line_end: int = 500) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> str:
        raise NotImplementedError

    async def tool_run(
        self, command: str, timeout_seconds: int = 120
    ) -> dict[str, object]:
        raise NotImplementedError

    async def close(self) -> None:
        raise RuntimeError("container removal failed")

    def description(self) -> dict[str, object]:
        return {"environment": "test"}


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
async def test_analysis_worktree_is_restored_when_environment_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = AsyncMock(
        side_effect=[
            CommandResult(("git",), 0, "", ""),
            CommandResult(("git",), 0, "", ""),
        ]
    )
    monkeypatch.setattr(run_workflow, "run_command", run)

    with pytest.raises(RuntimeError, match="container removal failed"):
        await run_workflow.restore_analysis_worktree(
            FailingEnvironment(),
            tmp_path,
            "a" * 40,
        )

    assert run.await_args_list[0].args[0][-2:] == ("--hard", "a" * 40)
    assert run.await_args_list[1].args[0][-2:] == ("clean", "-fdx")


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

    await run_control.submit_decision(
        run.id, action.id, DecisionSubmission(action="check_again")
    )

    async with factory() as session:
        replacement = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
    assert replacement is not None
    assert replacement.id != action.id
    assert replacement.kind == PendingActionKind.PHASE
    assert replacement.phase_id == phase.id


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

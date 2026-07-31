import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from mafia.db.base import Base
from mafia.db.models import AuditEvent, Operation, Repository, Run
from mafia.domain.enums import RequirementType, RunState
from mafia.services import run_control
from mafia.services.operations import ActiveWorkError, has_active_work, launch_background_work
from mafia.services.runs import get_run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def run_control_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        session.add(
            Run(
                id="run-1",
                repository_id=repository.id,
                requirement_type=RequirementType.TEXT,
                requirement_text="Test run control",
                primary_model="gpt-5.6-sol",
                reviewer_model="claude-opus-4.8",
            )
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_launch_registers_before_worker_runs() -> None:
    release = asyncio.Event()

    async def wait_for_release() -> None:
        await release.wait()

    launch_background_work("run-launch-race", wait_for_release)

    with pytest.raises(ActiveWorkError, match="already has active work"):
        launch_background_work("run-launch-race", wait_for_release)

    release.set()
    await asyncio.sleep(0)
    assert has_active_work("run-launch-race") is False


@pytest.mark.asyncio
async def test_cancelling_request_does_not_cancel_launched_worker() -> None:
    worker_started = asyncio.Event()
    release_worker = asyncio.Event()

    async def worker() -> None:
        worker_started.set()
        await release_worker.wait()

    async def request() -> None:
        launch_background_work("run-disconnect", worker)
        await asyncio.Event().wait()

    request_task = asyncio.create_task(request())
    await worker_started.wait()
    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert has_active_work("run-disconnect") is True
    release_worker.set()
    await asyncio.sleep(0)
    assert has_active_work("run-disconnect") is False


@pytest.mark.asyncio
async def test_worker_failure_marks_run_failed(
    run_control_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_control, "SessionFactory", run_control_session_factory)

    await run_control.record_run_failure("run-1", "planning", RuntimeError("x" * 5_000))

    async with run_control_session_factory() as session:
        run = await get_run(session, "run-1")
        event = await session.scalar(select(AuditEvent).where(AuditEvent.run_id == "run-1"))
    assert run.state == RunState.FAILED
    assert run.failure_code == "planning_failed"
    assert run.failure_message == "x" * 4_000
    assert event is not None
    assert event.event_type == "planning.failed"


@pytest.mark.asyncio
async def test_start_run_rejects_a_non_intake_run(
    run_control_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_control, "SessionFactory", run_control_session_factory)
    async with run_control_session_factory() as session:
        run = await get_run(session, "run-1")
        run.state = RunState.GENERATING_SPEC
        await session.commit()

    with pytest.raises(run_control.RunControlError, match="Only an intake run"):
        await run_control.start_run("run-1")


@pytest.mark.asyncio
async def test_retry_run_rejects_a_run_that_has_not_failed(
    run_control_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_control, "SessionFactory", run_control_session_factory)

    with pytest.raises(run_control.RunControlError, match="Only a failed or stalled run"):
        await run_control.retry_run("run-1")


@pytest.mark.asyncio
async def test_retry_run_recovers_a_genuinely_stalled_working_run(
    run_control_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(run_control, "SessionFactory", run_control_session_factory)
    from mafia.services import activity

    monkeypatch.setattr(activity, "SessionFactory", run_control_session_factory)
    async with run_control_session_factory() as session:
        run = await get_run(session, "run-1")
        run.state = RunState.GENERATING_PLAN
        stale = datetime.now(UTC) - timedelta(hours=1)
        session.add(
            Operation(
                idempotency_key="run-1:-:model.plan_generation:stalled",
                run_id="run-1",
                operation_type="model.plan_generation",
                request_hash="a" * 64,
                status="running",
                attempt=1,
                heartbeat_at=stale,
                progress_at=stale,
                detail={},
            )
        )
        await session.commit()

    launched: list[Callable[[], Awaitable[None]]] = []

    def launch(_id: str, work: Callable[[], Awaitable[None]]) -> None:
        launched.append(work)

    monkeypatch.setattr(run_control, "launch_background_work", launch)
    result = await run_control.retry_run("run-1")

    async with run_control_session_factory() as session:
        recovered = await get_run(session, "run-1")
        operation = await session.scalar(select(Operation).where(Operation.run_id == "run-1"))
    assert result.state == RunState.FAILED
    assert recovered.state == RunState.FAILED
    assert operation is not None and operation.status == "cancelled"
    assert len(launched) == 1

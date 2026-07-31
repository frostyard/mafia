from collections.abc import AsyncGenerator

import pytest
from mafia.db.base import Base
from mafia.db.models import AuditEvent, PendingAction, Repository, Run
from mafia.domain.enums import PendingActionKind, RequirementType, RunState
from mafia.domain.schemas import RunDetail
from mafia.services.runs import PendingActionSpec, get_run, transition_with_pending_action
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def add_run(session: AsyncSession, *, state: RunState) -> Run:
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
        requirement_text="Add durable actions",
        primary_model="gpt-5.6-sol",
        reviewer_model="claude-opus-4.8",
        state=state,
    )
    session.add(run)
    await session.flush()
    return run


@pytest.mark.asyncio
async def test_run_has_only_one_pending_action(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        session.add(
            PendingAction(
                run_id=run.id,
                kind=PendingActionKind.SPECIFICATION,
                expected_run_version=run.version,
                payload={},
            )
        )
        await session.commit()
        session.add(
            PendingAction(
                run_id=run.id,
                kind=PendingActionKind.PLAN,
                expected_run_version=run.version,
                payload={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_run_detail_serializes_pending_action(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        run.pending_action = PendingAction(
            kind=PendingActionKind.SPECIFICATION,
            expected_run_version=run.version,
            revision=3,
            payload={"summary": "Review the specification"},
        )
        await session.commit()
        detail = RunDetail.model_validate(await get_run(session, run.id))

    assert detail.pending_action is not None
    assert detail.pending_action.kind == PendingActionKind.SPECIFICATION
    assert detail.pending_action.expected_run_version == 1
    assert detail.pending_action.revision == 3
    assert detail.pending_action.payload == {"summary": "Review the specification"}


@pytest.mark.asyncio
async def test_transition_replaces_pending_action_and_records_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run = await add_run(session, state=RunState.GENERATING_SPEC)
        run.pending_action = PendingAction(
            kind=PendingActionKind.CONFIGURATION_REQUIRED,
            expected_run_version=run.version,
            payload={},
        )
        await session.commit()

        transitioned = await transition_with_pending_action(
            session,
            run.id,
            RunState.AWAITING_SPEC_DECISION,
            expected_version=run.version,
            event_type="specification.ready",
            pending=PendingActionSpec(
                kind=PendingActionKind.SPECIFICATION,
                revision=1,
                payload={"artifact_id": "spec-1"},
            ),
        )
        event = await session.scalar(select(AuditEvent).where(AuditEvent.run_id == run.id))

    assert transitioned.state == RunState.AWAITING_SPEC_DECISION
    assert transitioned.version == 2
    assert transitioned.pending_action is not None
    assert transitioned.pending_action.kind == PendingActionKind.SPECIFICATION
    assert transitioned.pending_action.expected_run_version == 2
    assert transitioned.pending_action.revision == 1
    assert event is not None
    assert event.event_type == "specification.ready"

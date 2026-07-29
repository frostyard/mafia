from mafia.config import get_settings
from mafia.db.models import AuditEvent, Repository, Run
from mafia.domain.enums import RequirementType, RunState, WorkflowType
from mafia.domain.schemas import RunCreate
from mafia.domain.state_machine import require_transition
from mafia.services.repositories import (
    RepositoryIdentity,
    get_or_create_repository,
    parse_repository,
    require_repository_owner,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class RunNotFoundError(LookupError):
    pass


class ConcurrentUpdateError(RuntimeError):
    pass


class UnsupportedModelError(ValueError):
    pass


async def create_run(session: AsyncSession, request: RunCreate) -> Run:
    reviewer_model = get_settings().model_pairs.get(request.primary_model)
    if reviewer_model is None:
        raise UnsupportedModelError(
            f"Primary model {request.primary_model!r} is not configured"
        )
    identity = parse_repository(request.repository)
    repository = await get_or_create_repository(session, identity)
    run = Run(
        repository=repository,
        workflow_type=request.workflow_type,
        requirement_type=(
            (
                RequirementType.ISSUE
                if request.issue_number is not None
                else RequirementType.TEXT
            )
            if request.workflow_type == WorkflowType.SPECIFICATION
            else None
        ),
        requirement_text=request.requirement_text,
        issue_number=request.issue_number,
        pull_request_number=request.pull_request_number,
        primary_model=request.primary_model,
        reviewer_model=reviewer_model,
    )
    session.add(run)
    await session.flush()
    session.add(
        AuditEvent(
            run_id=run.id,
            event_type="run.created",
            to_state=RunState.INTAKE.value,
            payload={
                "repository": identity.slug,
                "workflow_type": request.workflow_type.value,
            },
        )
    )
    await session.commit()
    return await get_run(session, run.id)


async def get_run(session: AsyncSession, run_id: str) -> Run:
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.repository),
            selectinload(Run.artifacts),
            selectinload(Run.phases),
        )
    )
    if run is None:
        raise RunNotFoundError(run_id)
    require_repository_owner(
        RepositoryIdentity(run.repository.owner, run.repository.name)
    )
    return run


async def list_runs(session: AsyncSession) -> list[Run]:
    statement = select(Run).options(selectinload(Run.repository))
    repository_owner = get_settings().repository_owner
    if repository_owner is not None:
        statement = statement.join(Repository).where(
            Repository.owner.ilike(repository_owner)
        )
    result = await session.scalars(statement.order_by(Run.updated_at.desc()))
    return list(result)


async def transition_run(
    session: AsyncSession,
    run_id: str,
    target: RunState,
    *,
    expected_version: int,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> Run:
    run = await get_run(session, run_id)
    require_transition(run.state, target)
    current = run.state
    values: dict[str, object] = {
        "state": target,
        "version": expected_version + 1,
    }
    if current == RunState.FAILED and target != RunState.FAILED:
        values.update(failure_code=None, failure_message=None)
    updated_id = await session.scalar(
        update(Run)
        .where(Run.id == run_id, Run.version == expected_version)
        .values(**values)
        .returning(Run.id)
    )
    if updated_id is None:
        await session.rollback()
        raise ConcurrentUpdateError(f"Run {run_id} was changed by another request")
    session.add(
        AuditEvent(
            run_id=run_id,
            event_type=event_type,
            from_state=current.value,
            to_state=target.value,
            payload=payload or {},
        )
    )
    await session.commit()
    return await get_run(session, run_id)

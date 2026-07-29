from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from mafia.agents.copilot import CopilotAgentService
from mafia.config import get_settings
from mafia.db.models import Evidence, SourceSnapshot
from mafia.db.session import get_session
from mafia.domain.schemas import (
    EvidenceRead,
    ModelAvailability,
    ModelPair,
    Readiness,
    RunActivity,
    RunCreate,
    RunDetail,
    RunRead,
)
from mafia.services.activity import (
    RunControlError,
    cancel_run,
    get_run_activity,
    prepare_retry,
    reset_to_specification,
)
from mafia.services.lifecycle import reconcile_run
from mafia.services.operator import bind_request_operator
from mafia.services.prerequisites import readiness
from mafia.services.repositories import InvalidRepositoryError
from mafia.services.runs import (
    ConcurrentUpdateError,
    RunNotFoundError,
    UnsupportedModelError,
    create_run,
    get_run,
    list_runs,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]
Operator = Annotated[None, Depends(bind_request_operator)]


@router.get("/healthz", status_code=status.HTTP_204_NO_CONTENT)
async def healthz() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/readyz", response_model=Readiness)
async def readyz() -> Readiness:
    return await readiness()


@router.get("/api/models", response_model=ModelAvailability)
async def models_index() -> ModelAvailability:
    settings = get_settings()
    available = await CopilotAgentService().available_models()
    required = settings.required_models
    return ModelAvailability(
        pairs=[
            ModelPair(primary_model=primary, reviewer_model=reviewer)
            for primary, reviewer in settings.model_pairs.items()
        ],
        required=sorted(required),
        available=sorted(available & required),
        missing=sorted(required - available),
    )


@router.get("/api/runs", response_model=list[RunRead])
async def runs_index(session: Session) -> list[RunRead]:
    return [RunRead.model_validate(run) for run in await list_runs(session)]


@router.post("/api/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def runs_create(request: RunCreate, session: Session, _: Operator) -> RunRead:
    try:
        run = await create_run(session, request)
    except (InvalidRepositoryError, UnsupportedModelError) as error:
        code = (
            "invalid_repository"
            if isinstance(error, InvalidRepositoryError)
            else "unsupported_model"
        )
        raise HTTPException(
            status_code=422, detail={"code": code, "message": str(error)}
        ) from error
    return RunRead.model_validate(run)


@router.get("/api/runs/{run_id}", response_model=RunDetail)
async def runs_show(run_id: str, session: Session) -> RunDetail:
    try:
        return RunDetail.model_validate(await get_run(session, run_id))
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": run_id}) from error


@router.get("/api/runs/{run_id}/evidence", response_model=list[EvidenceRead])
async def runs_evidence(run_id: str, session: Session) -> list[EvidenceRead]:
    try:
        await get_run(session, run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": run_id},
        ) from error
    rows = await session.execute(
        select(Evidence, SourceSnapshot.git_sha)
        .join(SourceSnapshot, Evidence.snapshot_id == SourceSnapshot.id)
        .where(SourceSnapshot.run_id == run_id)
        .order_by(Evidence.created_at, Evidence.path_or_url)
    )
    return [
        EvidenceRead(
            id=evidence.id,
            snapshot_id=evidence.snapshot_id,
            source_sha=source_sha,
            kind=evidence.kind,
            path_or_url=evidence.path_or_url,
            line_start=evidence.line_start,
            line_end=evidence.line_end,
            excerpt_hash=evidence.excerpt_hash,
            detail=evidence.detail,
            created_at=evidence.created_at,
        )
        for evidence, source_sha in rows
    ]


@router.get("/api/runs/{run_id}/activity", response_model=RunActivity)
async def runs_activity(run_id: str) -> RunActivity:
    try:
        return await get_run_activity(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": run_id},
        ) from error


@router.post("/api/runs/{run_id}/cancel", response_model=RunActivity)
async def runs_cancel(run_id: str, _: Operator) -> RunActivity:
    try:
        return await cancel_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": run_id},
        ) from error
    except (ConcurrentUpdateError, RunControlError) as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "run_control_conflict", "message": str(error)},
        ) from error


@router.post("/api/runs/{run_id}/retry", response_model=RunActivity)
async def runs_retry(run_id: str, _: Operator) -> RunActivity:
    try:
        return await prepare_retry(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": run_id},
        ) from error
    except (ConcurrentUpdateError, RunControlError) as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "run_control_conflict", "message": str(error)},
        ) from error


@router.post("/api/runs/{run_id}/reset-to-specification", response_model=RunRead)
async def runs_reset_to_specification(run_id: str, _: Operator) -> RunRead:
    try:
        run = await reset_to_specification(run_id)
    except RunNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": run_id},
        ) from error
    except (ConcurrentUpdateError, RunControlError) as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "run_control_conflict", "message": str(error)},
        ) from error
    return RunRead.model_validate(run)


@router.post("/api/runs/{run_id}/refresh")
async def runs_refresh(run_id: str, _: Operator) -> dict[str, object]:
    try:
        return await reconcile_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": run_id}) from error

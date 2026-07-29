import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from agent_framework import FileCheckpointStorage
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from mafia.agui.snapshots import SQLiteAGUIThreadSnapshotStore
from mafia.agui.workflow import DurableAgentFrameworkWorkflow
from mafia.api.auth import auth_router
from mafia.api.routes import router
from mafia.config import get_settings
from mafia.services.auth_middleware import AuthenticationMiddleware
from mafia.services.lifecycle import monitor_merges, recover_interrupted_runs
from mafia.services.operator import bind_request_operator
from mafia.services.repositories import InvalidRepositoryError
from mafia.workflows.run_workflow import build_run_workflow


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    settings.ensure_directories()
    await recover_interrupted_runs()
    stop = asyncio.Event()
    monitor = asyncio.create_task(monitor_merges(stop, settings.merge_poll_seconds))
    try:
        yield
    finally:
        stop.set()
        await monitor


def workflow_factory(thread_id: str):
    return build_run_workflow(thread_id, checkpoint_storage)


settings = get_settings()
checkpoint_storage = FileCheckpointStorage(
    settings.checkpoints_dir,
    allowed_checkpoint_types=[
        "mafia.domain.artifacts:ArtifactDecision",
        "mafia.domain.artifacts:ArtifactDecisionRequest",
        "mafia.domain.artifacts:PhaseDecision",
        "mafia.domain.artifacts:PhaseDecisionRequest",
        "mafia.domain.artifacts:PullRequestReviewDecision",
        "mafia.domain.artifacts:PullRequestReviewDecisionRequest",
    ],
)


def create_app() -> FastAPI:
    app = FastAPI(title="MAFIA", version="0.1.0", lifespan=lifespan)

    async def invalid_repository_handler(
        _request: Request,
        error: Exception,
    ) -> JSONResponse:
        if not isinstance(error, InvalidRepositoryError):
            raise error
        return JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "code": "repository_not_authorized",
                    "message": str(error),
                }
            },
        )
    app.add_exception_handler(InvalidRepositoryError, invalid_repository_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthenticationMiddleware, settings=settings)
    app.include_router(auth_router)
    app.include_router(router)
    snapshots = SQLiteAGUIThreadSnapshotStore()
    workflow = DurableAgentFrameworkWorkflow(
        workflow_factory=workflow_factory,
        name="mafia",
        description="Source-grounded specification, planning, review, and phased delivery workflow.",
        snapshot_store=snapshots,
        checkpoint_storage=checkpoint_storage,
    )
    add_agent_framework_fastapi_endpoint(
        app,
        workflow,
        "/ag-ui",
        snapshot_store=snapshots,
        snapshot_scope_resolver=lambda _: "local-user",
        dependencies=[Depends(bind_request_operator)],
        allow_origins=settings.allowed_origins,
    )
    return app


app = create_app()

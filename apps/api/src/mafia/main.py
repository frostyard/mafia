import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from mafia.api.auth import auth_router
from mafia.api.routes import router
from mafia.config import get_settings
from mafia.services.auth_middleware import AuthenticationMiddleware
from mafia.services.lifecycle import monitor_merges, recover_interrupted_runs
from mafia.services.repositories import InvalidRepositoryError


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


settings = get_settings()


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
    return app


app = create_app()

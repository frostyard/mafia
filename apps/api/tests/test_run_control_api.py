import asyncio
from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI
from mafia.api import routes
from mafia.config import Settings
from mafia.db.base import Base
from mafia.db.models import PendingAction, Repository, Run
from mafia.domain.enums import PendingActionKind, RequirementType, RunState
from mafia.domain.schemas import RunActivity
from mafia.services import activity as activity_service
from mafia.services import run_control
from mafia.services.auth_middleware import AuthenticationMiddleware
from mafia.services.operations import has_active_work
from mafia.services.run_control import RunControlError
from mafia.services.runs import ConcurrentUpdateError, RunNotFoundError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def activity() -> RunActivity:
    return RunActivity(
        run_id="run-1",
        state=RunState.INTAKE,
        version=1,
        status_mode="idle",
        status_message="Ready to start.",
        stalled=False,
        stall_reason=None,
        stall_threshold_seconds=60,
        can_cancel=False,
        can_retry=False,
        source_sha=None,
        files_discovered=None,
        citations_found=0,
        pending_action=None,
        operations=[],
        events=[],
    )


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    settings = Settings.model_validate(
        {
            "auth_mode": "github",
            "github_oauth_client_id": "client-id",
            "github_oauth_client_secret": "client-secret",
            "github_oauth_callback_url": "https://mafia.example/auth/callback",
            "github_session_secret": "s" * 32,
            "internal_secret": "i" * 32,
            "github_allowed_user_ids": {37492},
        }
    )
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, settings=settings)
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://mafia.example"
    ) as test_client:
        yield test_client


@pytest.fixture
def operator_headers() -> dict[str, str]:
    return {"X-Mafia-Internal-Secret": "i" * 32}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/runs/run-1/start", None),
        ("/api/runs/run-1/retry", None),
        ("/api/runs/run-1/decisions/action-1", {"action": "accept"}),
    ],
)
async def test_run_control_commands_require_operator_authorization(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    response = await client.post(path, json=payload)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_run_control_commands_delegate_to_committed_services(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    async def start(run_id: str) -> RunActivity:
        calls.append(("start", run_id))
        return activity()

    async def retry(run_id: str) -> RunActivity:
        calls.append(("retry", run_id))
        return activity()

    async def submit(run_id: str, action_id: str, payload: object) -> RunActivity:
        calls.append(("decision", run_id, action_id, payload))
        return activity()

    monkeypatch.setattr(routes, "start_run", start, raising=False)
    monkeypatch.setattr(routes, "retry_run", retry, raising=False)
    monkeypatch.setattr(routes, "submit_decision", submit, raising=False)

    start_response = await client.post("/api/runs/run-1/start", headers=operator_headers)
    retry_response = await client.post("/api/runs/run-1/retry", headers=operator_headers)
    decision_response = await client.post(
        "/api/runs/run-1/decisions/action-1",
        json={"action": "accept"},
        headers=operator_headers,
    )

    assert [response.status_code for response in (start_response, retry_response, decision_response)] == [
        200,
        200,
        200,
    ]
    assert decision_response.json()["pending_action"] is None
    assert calls[0:2] == [("start", "run-1"), ("retry", "run-1")]
    assert calls[2][0:3] == ("decision", "run-1", "action-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["start", "retry", "decisions/action-1"])
async def test_run_control_commands_preserve_not_found_envelope(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    async def missing(*_args: object) -> RunActivity:
        raise RunNotFoundError("missing")

    monkeypatch.setattr(routes, "start_run", missing, raising=False)
    monkeypatch.setattr(routes, "retry_run", missing, raising=False)
    monkeypatch.setattr(routes, "submit_decision", missing, raising=False)

    response = await client.post(
        f"/api/runs/missing/{path}",
        json={"action": "accept"} if path.startswith("decisions") else None,
        headers=operator_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "run_not_found", "message": "missing"}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConcurrentUpdateError("changed"), RunControlError("invalid")])
@pytest.mark.parametrize(
    "path,payload",
    [
        ("start", None),
        ("retry", None),
        ("decisions/action-1", {"action": "accept"}),
    ],
)
async def test_run_control_commands_preserve_conflict_envelope(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    async def conflicting(*_args: object) -> RunActivity:
        raise error

    monkeypatch.setattr(routes, "start_run", conflicting, raising=False)
    monkeypatch.setattr(routes, "retry_run", conflicting, raising=False)
    monkeypatch.setattr(routes, "submit_decision", conflicting, raising=False)

    response = await client.post(
        f"/api/runs/run-1/{path}",
        json=payload,
        headers=operator_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "run_control_conflict", "message": str(error)}


@pytest.mark.asyncio
async def test_decision_submission_rejects_malformed_payload(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/runs/run-1/decisions/action-1",
        json={"action": "refine"},
        headers=operator_headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_uses_persisted_configuration_action_for_decision_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
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
                requirement_text="Test persisted action status",
                primary_model="gpt-5.6-sol",
                reviewer_model="claude-opus-4.8",
                state=RunState.READY_FOR_PHASE,
            )
            session.add(run)
            await session.flush()
            session.add(
                PendingAction(
                    run_id=run.id,
                    kind=PendingActionKind.CONFIGURATION_REQUIRED,
                    expected_run_version=run.version,
                    payload={"message": "Configure validation before starting this phase."},
                )
            )
            await session.commit()

        monkeypatch.setattr(activity_service, "SessionFactory", session_factory)
        result = await activity_service.get_run_activity(run.id)
    finally:
        await engine.dispose()

    assert result.status_mode == "decision"
    assert result.status_message == "Configure validation before starting this phase."
    assert result.pending_action is not None
    assert result.pending_action.kind == PendingActionKind.CONFIGURATION_REQUIRED


@pytest.mark.asyncio
async def test_concurrent_start_returns_conflict_without_launching_a_second_worker(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    worker_started = asyncio.Event()
    release_worker = asyncio.Event()

    async def advance(run_id: str) -> None:
        assert run_id == "run-1"
        worker_started.set()
        await release_worker.wait()

    try:
        async with session_factory() as session:
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
                    requirement_text="Start once",
                    primary_model="gpt-5.6-sol",
                    reviewer_model="claude-opus-4.8",
                )
            )
            await session.commit()

        monkeypatch.setattr(run_control, "SessionFactory", session_factory)
        monkeypatch.setattr(activity_service, "SessionFactory", session_factory)
        monkeypatch.setattr(run_control, "advance_run", advance)

        winner = await client.post("/api/runs/run-1/start", headers=operator_headers)
        await worker_started.wait()
        loser = await client.post("/api/runs/run-1/start", headers=operator_headers)

        assert winner.status_code == 200
        assert loser.status_code == 409
        assert loser.json()["detail"]["code"] == "run_control_conflict"
        assert has_active_work("run-1") is True
    finally:
        release_worker.set()
        for _ in range(10):
            if not has_active_work("run-1"):
                break
            await asyncio.sleep(0)
        await engine.dispose()

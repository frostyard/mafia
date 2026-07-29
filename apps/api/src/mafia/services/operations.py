import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mafia.config import get_settings
from mafia.db.models import AuditEvent, Operation
from mafia.db.session import SessionFactory
from mafia.services.operator import current_actor
from sqlalchemy import select

logger = logging.getLogger(__name__)

OperationDetailProvider = Callable[[], dict[str, Any]]
_active_tasks: dict[str, dict[asyncio.Task[Any], int]] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _request_hash(detail: dict[str, Any]) -> str:
    payload = json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _bounded_detail(detail: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(detail, sort_keys=True, default=str)
    if len(encoded) <= 50_000:
        return detail
    return {"truncated": True, "preview": encoded[:50_000]}


@dataclass
class OperationHandle:
    id: str
    run_id: str
    operation_type: str
    detail_provider: OperationDetailProvider | None = None
    result: dict[str, Any] | None = field(default=None)
    progress_fingerprint: str = ""

    async def update_detail(self, values: dict[str, Any]) -> None:
        async with SessionFactory() as session:
            operation = await session.get(Operation, self.id)
            if operation is None:
                raise LookupError(self.id)
            now = _now()
            operation.detail = _bounded_detail({**operation.detail, **values})
            operation.heartbeat_at = now
            operation.progress_at = now
            self.progress_fingerprint = _request_hash(operation.detail)
            await session.commit()

    def set_result(self, result: dict[str, Any]) -> None:
        self.result = _bounded_detail(result)

    def current_detail(self) -> dict[str, Any]:
        if self.detail_provider is None:
            return {}
        return _bounded_detail(self.detail_provider())


async def _record_terminal(
    handle: OperationHandle,
    *,
    status: str,
    error: BaseException | None = None,
) -> None:
    completed_at = _now()
    async with SessionFactory() as session:
        operation = await session.get(Operation, handle.id)
        if operation is None:
            raise LookupError(handle.id)
        live_detail = handle.current_detail()
        operation.detail = _bounded_detail({**operation.detail, **live_detail})
        operation.status = status
        operation.completed_at = completed_at
        operation.heartbeat_at = completed_at
        operation.progress_at = completed_at
        operation.result = handle.result
        operation.error = (
            {"type": type(error).__name__, "message": str(error)[:4_000]}
            if error is not None
            else None
        )
        session.add(
            AuditEvent(
                run_id=handle.run_id,
                event_type=f"operation.{status}",
                payload={
                    "operation_id": handle.id,
                    "operation_type": handle.operation_type,
                    "error": str(error)[:1_000] if error is not None else None,
                },
            )
        )
        await session.commit()


def _log_background_failure(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Could not persist a cancelled operation's terminal state")


async def _record_cancelled_terminal(
    handle: OperationHandle,
    error: asyncio.CancelledError,
) -> None:
    terminal_task = asyncio.create_task(
        _record_terminal(handle, status="cancelled", error=error)
    )
    try:
        await asyncio.shield(terminal_task)
    except asyncio.CancelledError:
        terminal_task.add_done_callback(_log_background_failure)


async def _heartbeat(handle: OperationHandle) -> None:
    interval = get_settings().operation_heartbeat_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            async with SessionFactory() as session:
                operation = await session.get(Operation, handle.id)
                if operation is None or operation.status != "running":
                    return
                live_detail = handle.current_detail()
                if live_detail:
                    operation.detail = _bounded_detail({**operation.detail, **live_detail})
                now = _now()
                fingerprint = _request_hash(operation.detail)
                if fingerprint != handle.progress_fingerprint:
                    operation.progress_at = now
                    handle.progress_fingerprint = fingerprint
                operation.heartbeat_at = now
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not persist heartbeat for operation %s", handle.id)


async def _start_operation(
    *,
    run_id: str,
    operation_type: str,
    operation_key: str,
    phase_id: str | None,
    model: str | None,
    timeout_seconds: int | None,
    detail: dict[str, Any],
    detail_provider: OperationDetailProvider | None,
) -> OperationHandle:
    detail = {**detail, "operator": current_actor()}
    request_hash = _request_hash(detail)
    idempotency_key = f"{run_id}:{phase_id or '-'}:{operation_type}:{operation_key}"
    now = _now()
    async with SessionFactory() as session:
        operation = await session.scalar(
            select(Operation).where(Operation.idempotency_key == idempotency_key)
        )
        if operation is None:
            operation = Operation(
                idempotency_key=idempotency_key,
                run_id=run_id,
                phase_id=phase_id,
                operation_type=operation_type,
                request_hash=request_hash,
                status="running",
                model=model,
                attempt=1,
                timeout_seconds=timeout_seconds,
                started_at=now,
                heartbeat_at=now,
                progress_at=now,
                detail=_bounded_detail(detail),
            )
            session.add(operation)
            await session.flush()
        else:
            operation.request_hash = request_hash
            operation.status = "running"
            operation.model = model
            operation.attempt += 1
            operation.timeout_seconds = timeout_seconds
            operation.started_at = now
            operation.heartbeat_at = now
            operation.progress_at = now
            operation.completed_at = None
            operation.detail = _bounded_detail(detail)
            operation.result = None
            operation.error = None
        session.add(
            AuditEvent(
                run_id=run_id,
                event_type="operation.started",
                payload={
                    "operation_id": operation.id,
                    "operation_type": operation_type,
                    "model": model,
                    "attempt": operation.attempt,
                    **detail,
                },
            )
        )
        await session.commit()
        return OperationHandle(
            id=operation.id,
            run_id=run_id,
            operation_type=operation_type,
            detail_provider=detail_provider,
            progress_fingerprint=_request_hash(_bounded_detail(detail)),
        )


def _register_active_task(run_id: str, task: asyncio.Task[Any]) -> None:
    tasks = _active_tasks.setdefault(run_id, {})
    tasks[task] = tasks.get(task, 0) + 1


def _unregister_active_task(run_id: str, task: asyncio.Task[Any]) -> None:
    tasks = _active_tasks.get(run_id)
    if tasks is None:
        return
    remaining = tasks.get(task, 1) - 1
    if remaining > 0:
        tasks[task] = remaining
    else:
        tasks.pop(task, None)
    if not tasks:
        _active_tasks.pop(run_id, None)


@asynccontextmanager
async def active_run_work(run_id: str) -> AsyncGenerator[None]:
    current_task = asyncio.current_task()
    if current_task is None:
        yield
        return
    _register_active_task(run_id, current_task)
    try:
        yield
    finally:
        _unregister_active_task(run_id, current_task)


@asynccontextmanager
async def tracked_operation(
    *,
    run_id: str,
    operation_type: str,
    operation_key: str,
    phase_id: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
    detail: dict[str, Any] | None = None,
    detail_provider: OperationDetailProvider | None = None,
) -> AsyncGenerator[OperationHandle]:
    handle = await _start_operation(
        run_id=run_id,
        operation_type=operation_type,
        operation_key=operation_key,
        phase_id=phase_id,
        model=model,
        timeout_seconds=timeout_seconds,
        detail=detail or {},
        detail_provider=detail_provider,
    )
    current_task = asyncio.current_task()
    if current_task is not None:
        _register_active_task(run_id, current_task)
    heartbeat_task = asyncio.create_task(_heartbeat(handle))
    try:
        yield handle
    except asyncio.CancelledError as error:
        await _record_cancelled_terminal(handle, error)
        raise
    except TimeoutError as error:
        await _record_terminal(handle, status="timed_out", error=error)
        raise
    except BaseException as error:
        await _record_terminal(handle, status="failed", error=error)
        raise
    else:
        await _record_terminal(handle, status="completed")
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        if current_task is not None:
            _unregister_active_task(run_id, current_task)


def has_active_work(run_id: str) -> bool:
    return any(not task.done() for task in _active_tasks.get(run_id, {}))


def cancel_active_work(run_id: str) -> bool:
    tasks = [task for task in _active_tasks.get(run_id, {}) if not task.done()]
    for task in tasks:
        task.cancel()
    return bool(tasks)

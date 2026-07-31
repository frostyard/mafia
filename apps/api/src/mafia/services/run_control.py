import asyncio
import logging
from collections.abc import Awaitable, Callable

from mafia.db.models import AuditEvent
from mafia.db.session import SessionFactory
from mafia.domain.enums import RunState
from mafia.domain.schemas import RunActivity
from mafia.domain.state_machine import ALLOWED_TRANSITIONS
from mafia.services.operations import has_active_work, launch_background_work
from mafia.services.runs import get_run, transition_run

logger = logging.getLogger(__name__)


class RunControlError(RuntimeError):
    pass


async def record_run_failure(run_id: str, stage: str, error: BaseException) -> None:
    message = str(error)[:4_000]
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state == RunState.CANCELLED:
            return
        if RunState.FAILED not in ALLOWED_TRANSITIONS[run.state]:
            logger.warning("Could not mark run %s failed from state %s", run_id, run.state)
            return
        run.failure_code = f"{stage}_failed"
        run.failure_message = message
        await transition_run(
            session,
            run.id,
            RunState.FAILED,
            expected_version=run.version,
            event_type=f"{stage}.failed",
            payload={"error": message},
        )


async def record_run_status(
    run_id: str,
    event_type: str,
    message: str,
    payload: dict[str, object] | None = None,
) -> None:
    async with SessionFactory() as session:
        session.add(
            AuditEvent(
                run_id=run_id,
                event_type=event_type,
                payload={"message": message, **(payload or {})},
            )
        )
        await session.commit()


async def _run_guarded(
    run_id: str,
    stage: str,
    work: Callable[[], Awaitable[None]],
) -> None:
    try:
        await work()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await record_run_failure(run_id, stage, error)


async def start_run(run_id: str) -> RunActivity:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state != RunState.INTAKE:
            raise RunControlError("Only an intake run can be started")
    launch_background_work(
        run_id,
        lambda: _run_guarded(run_id, "workflow", lambda: advance_run(run_id)),
    )
    from mafia.services.activity import get_run_activity

    return await get_run_activity(run_id)


async def retry_run(run_id: str) -> RunActivity:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state != RunState.FAILED:
            raise RunControlError("Only a failed run can be retried")
    if has_active_work(run_id):
        raise RunControlError("The previous workflow attempt is still stopping")
    launch_background_work(
        run_id,
        lambda: _run_guarded(run_id, "retry", lambda: advance_run(run_id)),
    )
    from mafia.services.activity import get_run_activity

    return await get_run_activity(run_id)


async def advance_run(
    run_id: str,
    feedback: str | None = None,
    phase_id: str | None = None,
) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
    if run.state == RunState.INTAKE:
        await _advance_intake(run_id)
    elif run.state == RunState.FAILED:
        await _advance_failed(run_id, feedback=feedback, phase_id=phase_id)
    else:
        raise RunControlError(f"Run {run_id} cannot advance from {run.state.value}")


async def _advance_intake(run_id: str) -> None:
    raise RunControlError(f"Run {run_id} has no workflow implementation yet")


async def _advance_failed(
    run_id: str,
    *,
    feedback: str | None,
    phase_id: str | None,
) -> None:
    del feedback, phase_id
    raise RunControlError(f"Run {run_id} has no retry implementation yet")

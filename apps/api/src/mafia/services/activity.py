import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mafia.config import get_settings
from mafia.db.models import (
    Artifact,
    AuditEvent,
    Decision,
    Evidence,
    Operation,
    PendingAction,
    Phase,
    RepositoryLock,
    Run,
    SourceSnapshot,
)
from mafia.db.session import SessionFactory
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    OperationStatus,
    PendingActionKind,
    PhaseState,
    RunState,
)
from mafia.domain.schemas import ActivityEventRead, OperationRead, PendingActionRead, RunActivity
from mafia.domain.state_machine import require_transition
from mafia.services.operations import cancel_active_work, has_active_work
from mafia.services.repositories import RepositoryIdentity, require_repository_owner
from mafia.services.runs import ConcurrentUpdateError, get_run, transition_run
from sqlalchemy import delete, func, select, update

WORKING_STATES = frozenset(
    {
        RunState.GENERATING_SPEC,
        RunState.GROUNDING_PLAN,
        RunState.GENERATING_PLAN,
        RunState.REVIEWING_PLAN,
        RunState.ADJUDICATING_PLAN,
        RunState.PERSISTING_PLAN,
        RunState.EXECUTING_PHASE,
        RunState.REVIEWING_IMPLEMENTATION,
        RunState.ADJUDICATING_IMPLEMENTATION,
        RunState.REMEDIATING_IMPLEMENTATION,
        RunState.VERIFYING_REMEDIATION,
        RunState.PR_OPEN,
        RunState.REGROUNDING,
        RunState.GROUNDING_PR_REVIEW,
        RunState.REVIEWING_PR,
        RunState.CONSOLIDATING_PR_REVIEW,
        RunState.POSTING_PR_REVIEW,
    }
)
DECISION_STATES = frozenset(
    {
        RunState.AWAITING_SPEC_DECISION,
        RunState.AWAITING_PLAN_DECISION,
        RunState.READY_FOR_PHASE,
        RunState.AWAITING_PR_REVIEW_DECISION,
    }
)
PRESERVED_RESET_PHASE_STATES = frozenset(
    {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}
)


class RunControlError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _status(
    run: Run,
) -> tuple[
    Literal["idle", "working", "decision", "external", "failed", "cancelled", "completed"],
    str,
]:
    messages: dict[RunState, str] = {
        RunState.INTAKE: "Ready to start.",
        RunState.GENERATING_SPEC: "Working - generating the specification. No input required.",
        RunState.AWAITING_SPEC_DECISION: "Waiting for your specification decision.",
        RunState.GROUNDING_PLAN: "Working - grounding the plan in source truth. No input required.",
        RunState.GENERATING_PLAN: "Working - generating the draft plan. No input required.",
        RunState.REVIEWING_PLAN: "Working - adversarial review is running. No input required.",
        RunState.ADJUDICATING_PLAN: "Working - adjudicating review findings. No input required.",
        RunState.PERSISTING_PLAN: "Working - validating and persisting final artifacts.",
        RunState.AWAITING_PLAN_DECISION: "Waiting for your reviewed-plan decision.",
        RunState.READY_FOR_PHASE: "Waiting for your approval to start the next phase.",
        RunState.EXECUTING_PHASE: "Working - implementing and validating the approved phase.",
        RunState.REVIEWING_IMPLEMENTATION: (
            "Working - reviewing the exact staged implementation candidate."
        ),
        RunState.ADJUDICATING_IMPLEMENTATION: (
            "Working - adjudicating implementation review findings."
        ),
        RunState.REMEDIATING_IMPLEMENTATION: (
            "Working - applying the single bounded implementation remediation."
        ),
        RunState.VERIFYING_REMEDIATION: (
            "Working - verifying accepted findings are closed without serious regressions."
        ),
        RunState.PR_OPEN: "Working - finalizing pull request metadata.",
        RunState.WAITING_FOR_MERGE: "Waiting for the pull request to merge. No input required.",
        RunState.REGROUNDING: "Working - source changed; re-grounding the remaining plan.",
        RunState.GROUNDING_PR_REVIEW: "Working - fetching the exact pull-request source.",
        RunState.REVIEWING_PR: "Working - configured models are independently reviewing the pull request.",
        RunState.CONSOLIDATING_PR_REVIEW: "Working - adjudicating the independent review findings.",
        RunState.AWAITING_PR_REVIEW_DECISION: "Waiting for you to post or finish the review.",
        RunState.POSTING_PR_REVIEW: "Working - posting the consolidated review to GitHub.",
        RunState.FAILED: "The workflow failed and can be retried.",
        RunState.CANCELLED: "The workflow was cancelled.",
        RunState.COMPLETED: "The workflow completed.",
    }
    pending_action = run.pending_action
    if pending_action is not None:
        message_key = (
            "message"
            if pending_action.kind == PendingActionKind.CONFIGURATION_REQUIRED
            else "prompt"
        )
        message = pending_action.payload.get(message_key)
        if isinstance(message, str):
            return "decision", message
        return "decision", messages[run.state]
    if run.state in WORKING_STATES:
        mode = "working"
    elif run.state in DECISION_STATES:
        mode = "decision"
    elif run.state == RunState.WAITING_FOR_MERGE:
        mode = "external"
    elif run.state == RunState.FAILED:
        mode = "failed"
    elif run.state == RunState.CANCELLED:
        mode = "cancelled"
    elif run.state == RunState.COMPLETED:
        mode = "completed"
    else:
        mode = "idle"
    return mode, messages[run.state]


async def get_run_activity(run_id: str) -> RunActivity:
    settings = get_settings()
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        operations = list(
            await session.scalars(
                select(Operation)
                .where(Operation.run_id == run_id)
                .order_by(Operation.created_at.desc())
                .limit(50)
            )
        )
        events = list(
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.run_id == run_id)
                .order_by(AuditEvent.created_at.desc())
                .limit(100)
            )
        )
        snapshot = await session.scalar(
            select(SourceSnapshot)
            .where(SourceSnapshot.run_id == run_id)
            .order_by(SourceSnapshot.created_at.desc())
            .limit(1)
        )
        citations_found = 0
        if snapshot is not None:
            citations_found = int(
                await session.scalar(
                    select(func.count(Evidence.id)).where(
                        Evidence.snapshot_id == snapshot.id,
                        Evidence.kind == "citation",
                    )
                )
                or 0
            )
    threshold = timedelta(seconds=settings.operation_stall_seconds)
    running = [operation for operation in operations if operation.status == "running"]
    heartbeat_stale = any(
        now - _utc(operation.heartbeat_at) > threshold for operation in running
    )
    progress_stale = any(
        now - _utc(operation.progress_at) > threshold for operation in running
    )
    stalled = heartbeat_stale or progress_stale
    stall_reason = (
        "No operation heartbeat has been recorded within the stall threshold."
        if heartbeat_stale
        else (
            "The backend is alive, but the active operation has not reported progress "
            "within the stall threshold."
            if progress_stale
            else None
        )
    )
    mode, message = _status(run)
    operation_reads = [
        OperationRead(
            id=operation.id,
            phase_id=operation.phase_id,
            operation_type=operation.operation_type,
            status=OperationStatus(operation.status),
            model=operation.model,
            attempt=operation.attempt,
            timeout_seconds=operation.timeout_seconds,
            detail=operation.detail,
            result=operation.result,
            error=operation.error,
            created_at=operation.created_at,
            updated_at=operation.updated_at,
            started_at=operation.started_at,
            heartbeat_at=operation.heartbeat_at,
            progress_at=operation.progress_at,
            completed_at=operation.completed_at,
            elapsed_seconds=max(
                0,
                int(
                    (
                        _utc(operation.completed_at)
                        if operation.completed_at is not None
                        else now
                    )
                    .__sub__(_utc(operation.started_at))
                    .total_seconds()
                ),
            ),
        )
        for operation in operations
    ]
    return RunActivity(
        run_id=run.id,
        state=run.state,
        version=run.version,
        status_mode=mode,
        status_message=message,
        stalled=stalled,
        stall_reason=stall_reason,
        stall_threshold_seconds=settings.operation_stall_seconds,
        can_cancel=run.state in WORKING_STATES and has_active_work(run.id),
        can_retry=run.state == RunState.FAILED or stalled,
        source_sha=snapshot.git_sha if snapshot is not None else None,
        files_discovered=(
            len(snapshot.manifest.get("files", [])) if snapshot is not None else None
        ),
        citations_found=citations_found,
        pending_action=(
            PendingActionRead.model_validate(run.pending_action)
            if run.pending_action is not None
            else None
        ),
        operations=operation_reads,
        events=[ActivityEventRead.model_validate(event) for event in events],
    )


async def _wait_for_active_work(run_id: str, timeout_seconds: float = 10) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while has_active_work(run_id):
        if asyncio.get_running_loop().time() >= deadline:
            raise RunControlError("Active work did not stop before the retry timeout")
        await asyncio.sleep(0.05)


async def _close_running_operations(run_id: str, reason: str) -> None:
    completed_at = datetime.now(UTC)
    async with SessionFactory() as session:
        running = list(
            await session.scalars(
                select(Operation).where(
                    Operation.run_id == run_id,
                    Operation.status == "running",
                )
            )
        )
        for operation in running:
            operation.status = "cancelled"
            operation.completed_at = completed_at
            operation.heartbeat_at = completed_at
            operation.progress_at = completed_at
            operation.error = {"type": "OperationCancelled", "message": reason}
        await session.commit()


async def _stop_active_work(run_id: str, reason: str) -> None:
    cancel_active_work(run_id)
    await _wait_for_active_work(run_id)
    await _close_running_operations(run_id, reason)


async def cancel_run(run_id: str) -> RunActivity:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        require_repository_owner(
            RepositoryIdentity(run.repository.owner, run.repository.name)
        )
        if run.state not in WORKING_STATES:
            raise RunControlError("Only active work can be cancelled from the activity rail")
    await _stop_active_work(run_id, "The user cancelled active work.")
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state in WORKING_STATES:
            await transition_run(
                session,
                run.id,
                RunState.CANCELLED,
                expected_version=run.version,
                event_type="run.cancel_requested",
            )
    return await get_run_activity(run_id)


async def prepare_retry(run_id: str) -> RunActivity:
    async with SessionFactory() as session:
        retry_run = await get_run(session, run_id)
        require_repository_owner(
            RepositoryIdentity(
                retry_run.repository.owner,
                retry_run.repository.name,
            )
        )
    activity = await get_run_activity(run_id)
    if activity.state != RunState.FAILED and not activity.stalled:
        raise RunControlError("Only failed or stalled work can be retried")
    if activity.stalled:
        await _stop_active_work(
            run_id,
            "The stalled operation was replaced by a retry.",
        )
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            if run.state not in WORKING_STATES:
                return await get_run_activity(run_id)
            await transition_run(
                session,
                run.id,
                RunState.FAILED,
                expected_version=run.version,
                event_type="run.retry_requested",
                payload={"reason": "stalled"},
            )
    elif has_active_work(run_id):
        raise RunControlError("The previous workflow attempt is still stopping")
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        session.add(
            AuditEvent(
                run_id=run.id,
                event_type="run.retry_ready",
                payload={"version": run.version},
            )
        )
        await session.commit()
    return await get_run_activity(run_id)


async def reset_to_specification(run_id: str) -> Run:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        require_repository_owner(
            RepositoryIdentity(run.repository.owner, run.repository.name)
        )
        if run.active_spec_revision is None:
            raise RunControlError(
                "The run cannot return to specification refinement before a specification exists"
            )
        if run.state == RunState.AWAITING_SPEC_DECISION:
            return run
        was_working = run.state in WORKING_STATES
        stop_required = was_working or has_active_work(run_id)

    if stop_required:
        await _stop_active_work(
            run_id,
            "Active work was cancelled to return to specification refinement.",
        )
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            if run.state in WORKING_STATES:
                await transition_run(
                    session,
                    run.id,
                    RunState.CANCELLED,
                    expected_version=run.version,
                    event_type="specification.reset_cancel_requested",
                )
            elif was_working:
                return run

    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.active_spec_revision is None:
            raise RunControlError("The active specification disappeared during reset")
        specification = await session.scalar(
            select(Artifact).where(
                Artifact.run_id == run.id,
                Artifact.kind == ArtifactKind.SPECIFICATION,
                Artifact.revision == run.active_spec_revision,
            )
        )
        if specification is None:
            raise RunControlError("The active specification artifact is missing")
        require_transition(run.state, RunState.AWAITING_SPEC_DECISION)
        previous_state = run.state
        updated_id = await session.scalar(
            update(Run)
            .where(Run.id == run.id, Run.version == run.version)
            .values(
                state=RunState.AWAITING_SPEC_DECISION,
                version=run.version + 1,
                active_plan_revision=None,
                failure_code=None,
                failure_message=None,
            )
            .returning(Run.id)
        )
        if updated_id is None:
            await session.rollback()
            raise ConcurrentUpdateError(f"Run {run.id} was changed during specification reset")
        phases = list(await session.scalars(select(Phase).where(Phase.run_id == run.id)))
        invalidated_ordinals: list[int] = []
        preserved_ordinals: list[int] = []
        for phase in phases:
            has_pull_request = phase.pr_number is not None or phase.pr_url is not None
            has_merge = phase.status == PhaseState.MERGED or phase.merge_sha is not None
            if (
                phase.status in PRESERVED_RESET_PHASE_STATES
                or has_pull_request
                or has_merge
            ):
                phase.status = (
                    PhaseState.MERGED if has_merge else PhaseState.WAITING_FOR_MERGE
                )
                preserved_ordinals.append(phase.ordinal)
            else:
                invalidated_ordinals.append(phase.ordinal)
                await session.delete(phase)
        session.add(
            Decision(
                run_id=run.id,
                artifact_id=specification.id,
                decision_type=DecisionType.RESET_SPECIFICATION,
            )
        )
        await session.execute(delete(PendingAction).where(PendingAction.run_id == run.id))
        session.add(
            PendingAction(
                run_id=run.id,
                kind=PendingActionKind.SPECIFICATION,
                artifact_id=specification.id,
                revision=specification.revision,
                expected_run_version=run.version + 1,
                payload={"prompt": "Accept this specification or refine it with feedback."},
            )
        )
        session.add(
            AuditEvent(
                run_id=run.id,
                event_type="specification.reset",
                from_state=previous_state.value,
                to_state=RunState.AWAITING_SPEC_DECISION.value,
                payload={
                    "specification_revision": run.active_spec_revision,
                    "invalidated_phase_ordinals": sorted(invalidated_ordinals),
                    "preserved_phase_ordinals": sorted(preserved_ordinals),
                },
            )
        )
        await session.execute(
            delete(RepositoryLock).where(RepositoryLock.run_id == run.id)
        )
        await session.commit()
        return await get_run(session, run.id)

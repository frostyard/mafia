import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mafia.config import get_settings
from mafia.db.models import (
    Artifact,
    AuditEvent,
    Decision,
    Operation,
    PendingAction,
    Phase,
    Run,
    SourceSnapshot,
)
from mafia.db.session import SessionFactory
from mafia.domain.artifacts import ImplementationPlan
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    PendingActionKind,
    PhaseState,
    RunState,
    WorkflowType,
)
from mafia.domain.schemas import DecisionSubmission, RunActivity
from mafia.domain.state_machine import ALLOWED_TRANSITIONS, require_transition
from mafia.services.artifacts import ArtifactGenerator
from mafia.services.commands import run_command
from mafia.services.devcontainers import create_execution_environment
from mafia.services.github import post_pull_request_comment
from mafia.services.operations import (
    ActiveWorkError,
    has_active_work,
    launch_background_work,
    run_work_lock,
    tracked_operation,
)
from mafia.services.pr_reviews import PullRequestReviewService
from mafia.services.project_config import (
    repository_configuration_content_at_commit,
    resolve_project_configuration_content,
    run_deterministic_validation,
    source_validation_status,
)
from mafia.services.repositories import RepositoryIdentity
from mafia.services.runs import (
    PendingActionSpec,
    get_run,
    transition_run,
    transition_with_pending_action,
)
from mafia.services.sandbox import ExecutionEnvironment
from mafia.services.source import capture_pull_request_snapshot, capture_source_snapshot
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
PR_REVIEW_DECISION_PROMPT = (
    "Review the consolidated findings. Post them to the pull request or finish without posting."
)


class RunControlError(RuntimeError):
    pass


async def restore_analysis_worktree(
    environment: ExecutionEnvironment,
    worktree: Path,
    head_sha: str,
) -> None:
    try:
        await environment.close()
    finally:
        try:
            await run_command(("git", "-C", str(worktree), "reset", "--hard", head_sha))
        finally:
            await run_command(("git", "-C", str(worktree), "clean", "-fdx"))


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
    async with run_work_lock(run_id):
        return await _retry_run(run_id)


async def _retry_run(run_id: str) -> RunActivity:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        state = run.state
    if state != RunState.FAILED:
        from mafia.services.activity import WORKING_STATES, stop_active_work

        threshold = datetime.now(UTC) - timedelta(seconds=get_settings().operation_stall_seconds)
        async with SessionFactory() as session:
            stalled = await session.scalar(
                select(Operation.id)
                .where(
                    Operation.run_id == run_id,
                    Operation.status == "running",
                    (Operation.heartbeat_at < threshold) | (Operation.progress_at < threshold),
                )
                .limit(1)
            )
        if state not in WORKING_STATES or stalled is None:
            raise RunControlError("Only a failed or stalled run can be retried")
        await stop_active_work(run_id, "The stalled workflow was stopped before retrying.")
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            if run.state in WORKING_STATES:
                run.failure_code = "stalled"
                run.failure_message = "The workflow stalled and was stopped before retrying."
                await transition_run(
                    session,
                    run.id,
                    RunState.FAILED,
                    expected_version=run.version,
                    event_type="run.stalled_retry_requested",
                )
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
    if run.workflow_type == WorkflowType.PULL_REQUEST_REVIEW:
        await _advance_pull_request_review(run_id)
    elif run.state in {RunState.INTAKE, RunState.GENERATING_SPEC}:
        await _generate_specification(run_id, feedback=feedback)
    elif run.state == RunState.GROUNDING_PLAN:
        await _generate_plan(run_id, feedback=feedback)
    elif run.state == RunState.REGROUNDING:
        await _generate_plan(run_id, feedback="Source drift requires an updated plan.")
    elif run.state == RunState.FAILED:
        await _advance_failed(run_id, feedback=feedback, phase_id=phase_id)
    else:
        raise RunControlError(f"Run {run_id} cannot advance from {run.state.value}")


async def _advance_failed(
    run_id: str,
    *,
    feedback: str | None,
    phase_id: str | None,
) -> None:
    del feedback, phase_id
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        failed_phase = await session.scalar(
            select(Phase).where(Phase.run_id == run.id, Phase.status == PhaseState.FAILED)
        )
        accepted_specification = await session.scalar(
            select(Decision.id)
            .join(Artifact, Decision.artifact_id == Artifact.id)
            .where(
                Decision.run_id == run.id,
                Decision.decision_type == DecisionType.ACCEPT,
                Artifact.kind == ArtifactKind.SPECIFICATION,
            )
            .limit(1)
        )
    if failed_phase is not None:
        from mafia.services.lifecycle import recover_phase_pull_request

        if await recover_phase_pull_request(run_id, failed_phase.id):
            return
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            phase = await session.get(Phase, failed_phase.id)
            if phase is None:
                raise RunControlError("Failed phase disappeared during retry")
            phase.status = PhaseState.READY
            phase.review_cycle += 1
            phase.implementation_review_attempts = 0
            phase.remediation_attempts = 0
            phase.verification_attempts = 0
            phase.candidate_base_sha = None
            phase.candidate_diff_hash = None
            run.failure_code = None
            run.failure_message = None
            pending = await resolve_phase_pending_action(
                run, phase_id=phase.id, source_sha=phase.source_sha, ordinal=phase.ordinal, title=phase.title
            )
            await transition_with_pending_action(
                session,
                run.id,
                RunState.READY_FOR_PHASE,
                expected_version=run.version,
                event_type="phase.retry_ready",
                payload={"phase_id": phase.id, "review_cycle": phase.review_cycle},
                pending=pending,
            )
        return
    if run.workflow_type == WorkflowType.PULL_REQUEST_REVIEW:
        if await _restore_pull_request_review_action(run_id):
            return
        await _review_pull_request(run_id)
        return
    if accepted_specification is not None:
        await _generate_plan(run_id, feedback="Retry after an interrupted planning step.")
        return
    if await _restore_artifact_action(run_id):
        return
    await _generate_specification(run_id)


async def _advance_pull_request_review(run_id: str) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
    if run.state == RunState.FAILED:
        await _advance_failed(run_id, feedback=None, phase_id=None)
        return
    if run.state != RunState.INTAKE:
        raise RunControlError(f"Run {run_id} cannot advance from {run.state.value}")
    await _review_pull_request(run_id)


async def _review_pull_request(run_id: str) -> None:
    await _run_guarded(
        run_id,
        "pull_request_review",
        lambda: _review_pull_request_inner(run_id),
    )


async def _review_pull_request_inner(run_id: str) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.pull_request_number is None:
            raise RunControlError("Pull-request review run has no pull request number")
        run = await transition_run(
            session,
            run.id,
            RunState.GROUNDING_PR_REVIEW,
            expected_version=run.version,
            event_type="pull_request_review.grounding_started",
            payload={"pull_request_number": run.pull_request_number},
        )
        async with tracked_operation(
            run_id=run.id,
            operation_type="source.grounding",
            operation_key=f"pr-{run.pull_request_number}:attempt-{run.version}",
            detail={"reason": "pull_request_review", "pull_request_number": run.pull_request_number},
        ) as operation:
            snapshot, context = await capture_pull_request_snapshot(session, run, run.repository)
            snapshot_id = snapshot.id
            snapshot_sha = snapshot.git_sha
            operation.set_result(
                {
                    "source_sha": snapshot_sha,
                    "base_sha": context.get("base_sha"),
                    "changed_files": context.get("changed_files"),
                }
            )
    identity = RepositoryIdentity(run.repository.owner, run.repository.name)
    base_sha = context.get("base_sha")
    if not isinstance(base_sha, str):
        raise RunControlError("Pull-request snapshot has no base SHA")
    base_configuration = await repository_configuration_content_at_commit(
        ("-C", snapshot.worktree_path), base_sha, command_runner=run_command
    )
    project_configuration = resolve_project_configuration_content(
        identity,
        base_configuration,
    )
    validation_results: list[dict[str, object]] = []
    if project_configuration.validation_commands:
        environment = await create_execution_environment(
            Path(snapshot.worktree_path),
            execution_mode=project_configuration.execution_mode,
        )
        try:
            validation_results = await run_deterministic_validation(
                environment,
                project_configuration,
                run_id=run.id,
                stage=f"pull-request-{run.pull_request_number}",
            )
        finally:
            await restore_analysis_worktree(
                environment,
                Path(snapshot.worktree_path),
                snapshot.git_sha,
            )
    context["deterministic_validation"] = {
        "status": "passed" if validation_results else "not_configured",
        "source": project_configuration.validation_source,
        "configuration_sha256": project_configuration.validation_sha256,
        "commands": validation_results,
    }
    async with SessionFactory() as session:
        current_run = await get_run(session, run_id)
        current_run.project_configuration = project_configuration.snapshot()
        current_snapshot = await session.get(SourceSnapshot, snapshot_id)
        if current_snapshot is None:
            raise RunControlError("Pull-request source snapshot is missing")
        current_snapshot.issue_data = context
        await session.commit()
    run = await _transition_state(
        run_id,
        RunState.REVIEWING_PR,
        "pull_request_review.started",
        {
            "pull_request_number": run.pull_request_number,
            "head_sha": snapshot_sha,
            "models": [run.primary_model, run.reviewer_model],
        },
    )
    async with SessionFactory() as session:
        snapshot = await session.get(SourceSnapshot, snapshot_id)
        if snapshot is None:
            raise RunControlError("Pull-request source snapshot is missing")
    service = PullRequestReviewService()
    reviews = await asyncio.gather(
        service.review(run, snapshot, context, model=run.primary_model),
        service.review(run, snapshot, context, model=run.reviewer_model),
    )
    artifacts = [
        await service.persist_review(run, snapshot, review, model=model)
        for model, review in zip((run.primary_model, run.reviewer_model), reviews, strict=True)
    ]
    run = await _transition_state(
        run_id,
        RunState.CONSOLIDATING_PR_REVIEW,
        "pull_request_review.adjudication_started",
        {"review_artifacts": [artifact.id for artifact in artifacts], "adjudicator_model": run.primary_model},
    )
    consolidated = await service.consolidate(run, snapshot, context, artifacts)
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        run.active_review_revision = consolidated.revision
        await session.flush()
        await transition_with_pending_action(
            session,
            run.id,
            RunState.AWAITING_PR_REVIEW_DECISION,
            expected_version=run.version,
            event_type="pull_request_review.completed",
            payload={
                "artifact_id": consolidated.id,
                "revision": consolidated.revision,
                "pull_request_number": run.pull_request_number,
            },
            pending=PendingActionSpec(
                kind=PendingActionKind.PULL_REQUEST_REVIEW,
                artifact_id=consolidated.id,
                revision=consolidated.revision,
                payload={
                    "prompt": PR_REVIEW_DECISION_PROMPT,
                    "pull_request_number": run.pull_request_number,
                },
            ),
        )


async def _restore_pull_request_review_action(run_id: str) -> bool:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.active_review_revision is None or run.pull_request_number is None:
            return False
        artifact = await session.scalar(
            select(Artifact).where(
                Artifact.run_id == run.id,
                Artifact.kind == ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED,
                Artifact.revision == run.active_review_revision,
            )
        )
        if artifact is None:
            return False
        await transition_with_pending_action(
            session,
            run.id,
            RunState.AWAITING_PR_REVIEW_DECISION,
            expected_version=run.version,
            event_type="pull_request_review.post_retry_ready",
            pending=PendingActionSpec(
                kind=PendingActionKind.PULL_REQUEST_REVIEW,
                artifact_id=artifact.id,
                revision=artifact.revision,
                payload={
                    "prompt": PR_REVIEW_DECISION_PROMPT,
                    "pull_request_number": run.pull_request_number,
                },
            ),
        )
    return True


async def _restore_artifact_action(run_id: str) -> bool:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        for kind, revision, state, prompt in (
            (
                ArtifactKind.SPECIFICATION,
                run.active_spec_revision,
                RunState.AWAITING_SPEC_DECISION,
                "Accept this specification or refine it with feedback.",
            ),
            (
                ArtifactKind.PLAN,
                run.active_plan_revision,
                RunState.AWAITING_PLAN_DECISION,
                "Accept the reviewed plan or refine it with feedback.",
            ),
        ):
            if revision is None:
                continue
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run.id, Artifact.kind == kind, Artifact.revision == revision
                )
            )
            if artifact is None:
                continue
            await transition_with_pending_action(
                session,
                run.id,
                state,
                expected_version=run.version,
                event_type=f"{kind.value}.retry_ready",
                pending=PendingActionSpec(
                    kind=PendingActionKind.SPECIFICATION
                    if kind == ArtifactKind.SPECIFICATION
                    else PendingActionKind.PLAN,
                    artifact_id=artifact.id,
                    revision=artifact.revision,
                    payload={"prompt": prompt},
                ),
            )
            return True
    return False


async def _generate_specification(run_id: str, *, feedback: str | None = None) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state != RunState.GENERATING_SPEC:
            run = await transition_run(
                session,
                run.id,
                RunState.GENERATING_SPEC,
                expected_version=run.version,
                event_type="specification.started",
            )
        reason = f"spec-r{(run.active_spec_revision or 0) + 1}"
        async with tracked_operation(
            run_id=run.id,
            operation_type="source.grounding",
            operation_key=reason,
            detail={"reason": reason},
        ) as operation:
            snapshot = await capture_source_snapshot(session, run, run.repository, reason=reason)
            operation.set_result(
                {
                    "source_sha": snapshot.git_sha,
                    "files_discovered": len(snapshot.manifest.get("files", [])),
                    "manifests_found": len(snapshot.manifest.get("manifests", [])),
                    "instructions_found": len(snapshot.instructions),
                }
            )
        artifact = await ArtifactGenerator().specification(session, run, snapshot, feedback=feedback)
        run.active_spec_revision = artifact.revision
        await session.flush()
        await transition_with_pending_action(
            session,
            run.id,
            RunState.AWAITING_SPEC_DECISION,
            expected_version=run.version,
            event_type="specification.generated",
            payload={"artifact_id": artifact.id, "revision": artifact.revision},
            pending=PendingActionSpec(
                kind=PendingActionKind.SPECIFICATION,
                artifact_id=artifact.id,
                revision=artifact.revision,
                payload={"prompt": "Accept this specification or refine it with feedback."},
            ),
        )


async def _generate_plan(run_id: str, *, feedback: str | None = None) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state not in {RunState.GROUNDING_PLAN, RunState.REGROUNDING}:
            run = await transition_run(
                session,
                run.id,
                RunState.GROUNDING_PLAN,
                expected_version=run.version,
                event_type="plan.grounding_started",
            )
        reason = f"plan-r{(run.active_plan_revision or 0) + 1}"

    async with tracked_operation(
        run_id=run_id,
        operation_type="source.grounding",
        operation_key=reason,
        detail={"reason": reason},
    ) as operation:
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            snapshot = await capture_source_snapshot(session, run, run.repository, reason=reason)
        operation.set_result(
            {
                "source_sha": snapshot.git_sha,
                "files_discovered": len(snapshot.manifest.get("files", [])),
                "manifests_found": len(snapshot.manifest.get("manifests", [])),
                "instructions_found": len(snapshot.instructions),
            }
        )

    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        specification = await session.scalar(
            select(Artifact).where(
                Artifact.run_id == run.id,
                Artifact.kind == ArtifactKind.SPECIFICATION,
                Artifact.revision == run.active_spec_revision,
            )
        )
        if specification is None:
            raise RunControlError("Accepted specification artifact is missing")
        preserved_phases = list(
            await session.scalars(
                select(Phase)
                .where(
                    Phase.run_id == run.id,
                    Phase.status.in_({PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}),
                )
                .order_by(Phase.ordinal)
            )
        )
        if preserved_phases:
            completed_context = "\n".join(
                f"- Phase {phase.ordinal}: {phase.title} "
                f"(status {phase.status.value}, merge {phase.merge_sha or 'pending'}): "
                f"{phase.objective}"
                for phase in preserved_phases
            )
            feedback = (
                f"{feedback or ''}\n\nMerged phases and phases with an open pull request "
                "are immutable. Include each one at its existing ordinal without assigning "
                f"new work to it. Add new work only after those phases:\n{completed_context}"
            ).strip()

    run = await _transition_state(
        run_id,
        RunState.GENERATING_PLAN,
        "plan.generation_started",
        {"source_sha": snapshot.git_sha},
    )
    generator = ArtifactGenerator()
    draft_plan = await generator.draft_plan(run, snapshot, specification, feedback=feedback)
    run = await _transition_state(
        run_id,
        RunState.REVIEWING_PLAN,
        "plan.review_started",
        {
            "source_sha": snapshot.git_sha,
            "draft_plan_artifact_id": draft_plan.id,
            "reviewer_model": run.reviewer_model,
        },
    )
    review = await generator.adversarial_review(run, snapshot, specification, draft_plan)
    run = await _transition_state(
        run_id,
        RunState.ADJUDICATING_PLAN,
        "plan.adjudication_started",
        {"review_artifact_id": review.id, "primary_model": run.primary_model},
    )
    resolution = await generator.adjudicate_plan(run, snapshot, specification, draft_plan, review)
    run = await _transition_state(
        run_id,
        RunState.PERSISTING_PLAN,
        "plan.persistence_started",
        {"review_artifact_id": review.id, "dispositions": len(resolution.dispositions)},
    )
    final_plan, ledger = await generator.persist_final_plan(run, snapshot, review, resolution)
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        run.active_plan_revision = final_plan.revision
        await session.flush()
        await transition_with_pending_action(
            session,
            run.id,
            RunState.AWAITING_PLAN_DECISION,
            expected_version=run.version,
            event_type="plan.review_completed",
            payload={
                "plan_artifact_id": final_plan.id,
                "review_artifact_id": review.id,
                "ledger_artifact_id": ledger.id,
            },
            pending=PendingActionSpec(
                kind=PendingActionKind.PLAN,
                artifact_id=final_plan.id,
                revision=final_plan.revision,
                payload={"prompt": "Accept the reviewed plan or refine it with feedback."},
            ),
        )


async def _transition_state(
    run_id: str,
    target: RunState,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> Run:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state == RunState.CANCELLED:
            raise asyncio.CancelledError
        return await transition_run(
            session,
            run.id,
            target,
            expected_version=run.version,
            event_type=event_type,
            payload=payload,
        )


async def submit_decision(
    run_id: str,
    pending_action_id: str,
    payload: DecisionSubmission,
) -> RunActivity:
    async with run_work_lock(run_id):
        if has_active_work(run_id):
            raise ActiveWorkError(f"Run {run_id} already has active work")
        return await _submit_decision(run_id, pending_action_id, payload)


async def _submit_decision(
    run_id: str,
    pending_action_id: str,
    payload: DecisionSubmission,
) -> RunActivity:
    launch: tuple[str, str | None] | None = None
    launch_stage: str | None = None
    phase_id: str | None = None
    start_phase = False
    resolved_phase_action: PendingActionSpec | None = None
    post_review: tuple[RepositoryIdentity, int, str, str, str] | None = None
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        action = await session.get(PendingAction, pending_action_id)
        if action is not None and action.kind in {
            PendingActionKind.PHASE,
            PendingActionKind.CONFIGURATION_REQUIRED,
        }:
            phase_id, start_phase = await _submit_phase_decision(session, run, action, payload)
            await session.commit()
        elif action is not None and action.kind == PendingActionKind.PULL_REQUEST_REVIEW:
            post_review = await _submit_pull_request_review_decision(session, run, action, payload)
            await session.commit()
        else:
            expected_kind, active_revision = _validate_artifact_action(
                run, action, run_id, pending_action_id, payload
            )
            if action is None or action.artifact_id is None:
                raise RunControlError("Artifact decision action has no artifact")
            artifact = await session.get(Artifact, action.artifact_id)
            if (
                artifact is None
                or artifact.run_id != run.id
                or artifact.kind != expected_kind
                or artifact.revision != action.revision
                or artifact.revision != active_revision
            ):
                raise RunControlError("Artifact decision does not match the active artifact")

            if payload.action == "cancel":
                target = RunState.CANCELLED
                event_type = "run.cancelled"
            elif expected_kind == ArtifactKind.SPECIFICATION:
                target = RunState.GENERATING_SPEC if payload.action == "refine" else RunState.GROUNDING_PLAN
                event_type = (
                    "specification.refinement_started"
                    if payload.action == "refine"
                    else "specification.accepted"
                )
                launch = (run.id, payload.feedback if payload.action == "refine" else None)
                launch_stage = "specification" if payload.action == "refine" else "planning"
            else:
                resolved_phase_action = await _resolve_plan_phase_action(session, run, artifact, payload)
                target, event_type, launch = await _accept_plan(session, run, artifact, payload)
                launch_stage = "planning"

            session.add(
                Decision(
                    run_id=run.id,
                    artifact_id=artifact.id,
                    decision_type=DecisionType(payload.action),
                    feedback=payload.feedback,
                )
            )
            await session.delete(action)
            _, current = await _transition_without_commit(
                session, run.id, target, expected_version=run.version
            )
            session.add(
                AuditEvent(
                    run_id=run.id,
                    event_type=event_type,
                    from_state=current.value,
                    to_state=target.value,
                    payload={"revision": artifact.revision},
                )
            )
            if target == RunState.READY_FOR_PHASE:
                phase = await session.scalar(
                    select(Phase).where(Phase.run_id == run.id, Phase.status == PhaseState.READY)
                )
                if phase is None:
                    raise RunControlError("Accepted plan has no ready phase")
                phase_id = phase.id
                if resolved_phase_action is None:
                    raise RunControlError("Accepted plan has no resolved ready phase action")
                session.add(
                    _pending_action(
                        run.id,
                        PendingActionSpec(
                            kind=resolved_phase_action.kind,
                            phase_id=phase.id,
                            payload=resolved_phase_action.payload,
                        ),
                        expected_run_version=run.version + 1,
                    )
                )
            await session.commit()

    if launch is not None:
        launch_background_work(
            launch[0],
            lambda: _run_guarded(
                launch[0],
                launch_stage or "workflow",
                lambda: advance_run(launch[0], feedback=launch[1]),
            ),
        )
    elif start_phase and phase_id is not None:
        from mafia.services.execution import execute_phase

        launch_background_work(
            run_id,
            lambda: _run_guarded(run_id, "phase", lambda: execute_phase(run_id, phase_id)),
        )
    elif post_review is not None:
        identity, pull_request_number, artifact_id, markdown, post_run_id = post_review
        launch_background_work(
            post_run_id,
            lambda: _run_guarded(
                post_run_id,
                "pull_request_review_post",
                lambda: _post_pull_request_review(
                    post_run_id, identity, pull_request_number, artifact_id, markdown
                ),
            ),
        )
    from mafia.services.activity import get_run_activity

    return await get_run_activity(run_id)


async def _submit_pull_request_review_decision(
    session: AsyncSession,
    run: Run,
    action: PendingAction,
    payload: DecisionSubmission,
) -> tuple[RepositoryIdentity, int, str, str, str] | None:
    pull_request_number = action.payload.get("pull_request_number")
    artifact = await session.get(Artifact, action.artifact_id) if action.artifact_id else None
    if (
        action.run_id != run.id
        or action.expected_run_version != run.version
        or run.state != RunState.AWAITING_PR_REVIEW_DECISION
        or run.workflow_type != WorkflowType.PULL_REQUEST_REVIEW
        or not isinstance(pull_request_number, int)
        or pull_request_number != run.pull_request_number
        or artifact is None
        or artifact.run_id != run.id
        or artifact.kind != ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED
        or artifact.revision != action.revision
        or artifact.revision != run.active_review_revision
    ):
        raise RunControlError("Pending pull-request review action is stale")
    if payload.action not in {"post", "finish", "cancel"}:
        raise RunControlError("Pull-request review action requires Post, Finish, or Cancel")
    decision_type = {
        "post": DecisionType.POST_REVIEW,
        "finish": DecisionType.FINISH_REVIEW,
        "cancel": DecisionType.CANCEL,
    }[payload.action]
    session.add(Decision(run_id=run.id, artifact_id=artifact.id, decision_type=decision_type))
    await session.delete(action)
    if payload.action == "cancel":
        target, event_type, event_payload = RunState.CANCELLED, "run.cancelled", {}
    elif payload.action == "finish":
        target, event_type, event_payload = (
            RunState.COMPLETED,
            "pull_request_review.finished",
            {"posted": False},
        )
    else:
        target, event_type, event_payload = (
            RunState.POSTING_PR_REVIEW,
            "pull_request_review.post_started",
            {"pull_request_number": pull_request_number},
        )
    _, current = await _transition_without_commit(session, run.id, target, expected_version=run.version)
    session.add(
        AuditEvent(
            run_id=run.id,
            event_type=event_type,
            from_state=current.value,
            to_state=target.value,
            payload=event_payload,
        )
    )
    if payload.action != "post":
        return None
    return (
        RepositoryIdentity(run.repository.owner, run.repository.name),
        pull_request_number,
        artifact.id,
        artifact.rendered_markdown,
        run.id,
    )


async def _post_pull_request_review(
    run_id: str,
    identity: RepositoryIdentity,
    pull_request_number: int,
    artifact_id: str,
    markdown: str,
) -> None:
    async with tracked_operation(
        run_id=run_id,
        operation_type="github.pull_request_comment",
        operation_key=artifact_id,
        detail={
            "repository": identity.slug,
            "pull_request_number": pull_request_number,
            "artifact_id": artifact_id,
        },
    ) as operation:
        url = await post_pull_request_comment(
            identity,
            pull_request_number,
            run_id=run_id,
            artifact_id=artifact_id,
            markdown=markdown,
        )
        operation.set_result({"comment_url": url})
    async with SessionFactory() as final_session:
        current_run = await get_run(final_session, run_id)
        await transition_run(
            final_session,
            current_run.id,
            RunState.COMPLETED,
            expected_version=current_run.version,
            event_type="pull_request_review.posted",
            payload={"comment_url": url},
        )


async def _transition_without_commit(
    session: AsyncSession,
    run_id: str,
    target: RunState,
    *,
    expected_version: int,
) -> tuple[Run, RunState]:
    run = await get_run(session, run_id)
    require_transition(run.state, target)
    current = run.state
    updated_id = await session.scalar(
        update(Run)
        .where(Run.id == run_id, Run.version == expected_version)
        .values(state=target, version=expected_version + 1)
        .returning(Run.id)
    )
    if updated_id is None:
        await session.rollback()
        raise RunControlError("Run changed while consuming the pending action")
    return run, current


def _validate_artifact_action(
    run: Run,
    action: PendingAction | None,
    run_id: str,
    pending_action_id: str,
    payload: DecisionSubmission,
) -> tuple[ArtifactKind, int | None]:
    if action is None or action.id != pending_action_id:
        raise RunControlError("Pending action does not exist")
    if action.run_id != run_id or action.expected_run_version != run.version:
        raise RunControlError("Pending action is stale")
    if action.kind == PendingActionKind.SPECIFICATION:
        expected_kind = ArtifactKind.SPECIFICATION
        expected_state = RunState.AWAITING_SPEC_DECISION
        active_revision = run.active_spec_revision
    elif action.kind == PendingActionKind.PLAN:
        expected_kind = ArtifactKind.PLAN
        expected_state = RunState.AWAITING_PLAN_DECISION
        active_revision = run.active_plan_revision
    else:
        raise RunControlError("Pending action is not an artifact decision")
    if run.state != expected_state:
        raise RunControlError("Run is not awaiting this artifact decision")
    if payload.action not in {"accept", "refine", "cancel"}:
        raise RunControlError("Action is not valid for an artifact decision")
    return expected_kind, active_revision


async def _resolve_plan_phase_action(
    session: AsyncSession,
    run: Run,
    artifact: Artifact,
    payload: DecisionSubmission,
) -> PendingActionSpec | None:
    if payload.action == "refine":
        return None
    plan = ImplementationPlan.model_validate(artifact.structured_data)
    snapshot = await session.get(SourceSnapshot, artifact.source_snapshot_id)
    if snapshot is None:
        raise RunControlError("Plan source snapshot is missing")
    existing_phases = list(await session.scalars(select(Phase).where(Phase.run_id == run.id)))
    protected = {
        phase.ordinal: phase
        for phase in existing_phases
        if phase.status in {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}
    }
    if any(phase.status == PhaseState.WAITING_FOR_MERGE for phase in protected.values()):
        return None
    merged = {ordinal for ordinal, phase in protected.items() if phase.status == PhaseState.MERGED}
    ready = next(
        (
            item
            for item in sorted(plan.phases, key=lambda item: item.ordinal)
            if item.ordinal not in protected and set(item.dependencies).issubset(merged)
        ),
        None,
    )
    if ready is None:
        return None
    return await resolve_phase_pending_action(
        run,
        phase_id=None,
        source_sha=snapshot.git_sha,
        ordinal=ready.ordinal,
        title=ready.title,
    )


async def _accept_plan(
    session: AsyncSession,
    run: Run,
    artifact: Artifact,
    payload: DecisionSubmission,
) -> tuple[RunState, str, tuple[str, str | None] | None]:
    if payload.action == "refine":
        return RunState.GROUNDING_PLAN, "plan.refinement_started", (run.id, payload.feedback)
    plan = ImplementationPlan.model_validate(artifact.structured_data)
    snapshot = await session.get(SourceSnapshot, artifact.source_snapshot_id)
    if snapshot is None:
        raise RunControlError("Plan source snapshot is missing")
    existing_phases = {
        phase.ordinal: phase for phase in await session.scalars(select(Phase).where(Phase.run_id == run.id))
    }
    protected = {
        ordinal: phase
        for ordinal, phase in existing_phases.items()
        if phase.status in {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}
    }
    planned = {item.ordinal: item for item in plan.phases}
    missing = sorted(set(protected) - set(planned))
    if missing:
        raise RunControlError("The revised plan omitted immutable phases: " + ", ".join(map(str, missing)))
    for item in plan.phases:
        existing = existing_phases.get(item.ordinal)
        if existing is None:
            session.add(
                Phase(
                    run_id=run.id,
                    ordinal=item.ordinal,
                    title=item.title,
                    objective=item.objective,
                    dependencies=item.dependencies,
                    details=item.model_dump(mode="json"),
                    status=PhaseState.PENDING,
                    plan_revision=artifact.revision,
                    source_sha=snapshot.git_sha,
                )
            )
        elif existing.status not in {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}:
            existing.title = item.title
            existing.objective = item.objective
            existing.dependencies = item.dependencies
            existing.details = item.model_dump(mode="json")
            existing.status = PhaseState.PENDING
            existing.plan_revision = artifact.revision
            existing.source_sha = snapshot.git_sha
    for ordinal, existing in existing_phases.items():
        if ordinal not in planned and ordinal not in protected:
            await session.delete(existing)
    await session.flush()
    waiting = next(
        (phase for phase in protected.values() if phase.status == PhaseState.WAITING_FOR_MERGE),
        None,
    )
    if waiting is not None:
        return RunState.WAITING_FOR_MERGE, "plan.accepted_waiting_for_merge", None
    merged = {ordinal for ordinal, phase in protected.items() if phase.status == PhaseState.MERGED}
    candidates = list(
        await session.scalars(
            select(Phase)
            .where(Phase.run_id == run.id, Phase.status == PhaseState.PENDING)
            .order_by(Phase.ordinal)
        )
    )
    ready = next((phase for phase in candidates if set(phase.dependencies).issubset(merged)), None)
    if ready is None:
        return RunState.COMPLETED, "plan.accepted_complete", None
    ready.status = PhaseState.READY
    return RunState.READY_FOR_PHASE, "plan.accepted", None


async def resolve_phase_pending_action(
    run: Run,
    *,
    phase_id: str | None,
    source_sha: str,
    ordinal: int,
    title: str,
) -> PendingActionSpec:
    identity = RepositoryIdentity(run.repository.owner, run.repository.name)
    validation_available, _ = await source_validation_status(identity, run.repository.cache_path, source_sha)
    if validation_available:
        return PendingActionSpec(
            kind=PendingActionKind.PHASE,
            phase_id=phase_id,
            payload={"prompt": f"Start phase {ordinal}: {title}."},
        )
    return PendingActionSpec(
        kind=PendingActionKind.CONFIGURATION_REQUIRED,
        phase_id=phase_id,
        payload={
            "message": (
                f"Phase {ordinal} cannot start until deterministic validation is configured "
                f"for {identity.slug}."
            ),
            "project_id": run.repository_id,
            "project_href": f"/projects/{run.repository_id}",
        },
    )


def _pending_action(run_id: str, spec: PendingActionSpec, *, expected_run_version: int) -> PendingAction:
    return PendingAction(
        run_id=run_id,
        kind=spec.kind,
        phase_id=spec.phase_id,
        expected_run_version=expected_run_version,
        payload=spec.payload,
    )


async def create_phase_pending_action(run_id: str, phase_id: str) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        phase = await session.get(Phase, phase_id)
        if (
            phase is None
            or phase.run_id != run.id
            or run.state != RunState.READY_FOR_PHASE
            or phase.status != PhaseState.READY
        ):
            raise RunControlError("Phase is not ready for an action")
        pending = await resolve_phase_pending_action(
            run,
            phase_id=phase.id,
            source_sha=phase.source_sha,
            ordinal=phase.ordinal,
            title=phase.title,
        )
        await session.execute(delete(PendingAction).where(PendingAction.run_id == run.id))
        session.add(_pending_action(run.id, pending, expected_run_version=run.version))
        await session.commit()


async def _submit_phase_decision(
    session: AsyncSession,
    run: Run,
    action: PendingAction,
    payload: DecisionSubmission,
) -> tuple[str | None, bool]:
    phase = await session.get(Phase, action.phase_id) if action.phase_id is not None else None
    if (
        action.run_id != run.id
        or action.expected_run_version != run.version
        or run.state != RunState.READY_FOR_PHASE
        or phase is None
        or phase.run_id != run.id
        or phase.status != PhaseState.READY
    ):
        raise RunControlError("Pending phase action is stale")
    if action.kind == PendingActionKind.CONFIGURATION_REQUIRED:
        if payload.action == "check_again":
            pending = await resolve_phase_pending_action(
                run,
                phase_id=phase.id,
                source_sha=phase.source_sha,
                ordinal=phase.ordinal,
                title=phase.title,
            )
            await session.delete(action)
            await session.flush()
            session.add(_pending_action(run.id, pending, expected_run_version=run.version))
            return phase.id, False
        if payload.action != "cancel":
            raise RunControlError("Configuration action requires Check again or Cancel")
    elif payload.action == "start":
        session.add(Decision(run_id=run.id, phase_id=phase.id, decision_type=DecisionType.START_PHASE))
        await session.delete(action)
        return phase.id, True
    elif payload.action != "cancel":
        raise RunControlError("Phase action requires Start or Cancel")
    session.add(Decision(run_id=run.id, phase_id=phase.id, decision_type=DecisionType.CANCEL))
    await session.delete(action)
    _, current = await _transition_without_commit(
        session, run.id, RunState.CANCELLED, expected_version=run.version
    )
    session.add(
        AuditEvent(
            run_id=run.id,
            event_type="run.cancelled",
            from_state=current.value,
            to_state=RunState.CANCELLED.value,
        )
    )
    return phase.id, False

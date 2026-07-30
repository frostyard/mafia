import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_framework import (
    CheckpointStorage,
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowEvent,
    handler,
    response_handler,
)
from mafia.db.models import Artifact, Decision, Phase, Run, SourceSnapshot
from mafia.db.session import SessionFactory
from mafia.domain.artifacts import (
    ArtifactDecision,
    ArtifactDecisionRequest,
    ImplementationPlan,
    PhaseDecision,
    PhaseDecisionRequest,
    PullRequestReviewDecision,
    PullRequestReviewDecisionRequest,
)
from mafia.domain.enums import (
    ArtifactKind,
    DecisionType,
    PhaseState,
    RunState,
    WorkflowType,
)
from mafia.domain.state_machine import ALLOWED_TRANSITIONS
from mafia.services.artifacts import ArtifactGenerator
from mafia.services.commands import run_command
from mafia.services.devcontainers import create_execution_environment
from mafia.services.github import post_pull_request_comment
from mafia.services.operations import active_run_work, tracked_operation
from mafia.services.pr_reviews import PullRequestReviewService
from mafia.services.project_config import (
    ProjectConfigurationError,
    resolve_project_configuration_content,
    run_deterministic_validation,
    source_validation_status,
)
from mafia.services.repositories import RepositoryIdentity
from mafia.services.runs import get_run, transition_run
from mafia.services.sandbox import ExecutionEnvironment
from mafia.services.source import capture_pull_request_snapshot, capture_source_snapshot
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)


class WorkflowStreamInterruptedError(RuntimeError):
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
            await run_command(
                ("git", "-C", str(worktree), "reset", "--hard", head_sha)
            )
        finally:
            await run_command(("git", "-C", str(worktree), "clean", "-fdx"))


class RunWorkflowExecutor(Executor):
    def __init__(
        self,
        thread_id: str,
        generator: ArtifactGenerator | None = None,
        review_service: PullRequestReviewService | None = None,
    ) -> None:
        super().__init__(id=f"run-{thread_id}")
        self.thread_id = thread_id
        self.generator = generator or ArtifactGenerator()
        self.review_service = review_service or PullRequestReviewService()

    async def _run_for_thread(self) -> Run:
        async with SessionFactory() as session:
            run = await session.scalar(
                select(Run).where(Run.thread_id == self.thread_id).options(selectinload(Run.repository))
            )
            if run is None:
                raise LookupError(
                    f"No run is associated with AG-UI thread {self.thread_id}. "
                    "Create a run first and use its thread_id."
                )
            return run

    async def _record_failure(self, run_id: str, stage: str, error: Exception) -> None:
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            if run.state == RunState.CANCELLED:
                return
            run.failure_code = f"{stage}_failed"
            run.failure_message = str(error)
            await session.commit()
            if (
                run.state != RunState.FAILED
                and RunState.FAILED in ALLOWED_TRANSITIONS[run.state]
            ):
                await transition_run(
                    session,
                    run.id,
                    RunState.FAILED,
                    expected_version=run.version,
                    event_type=f"{stage}.failed",
                    payload={"error": str(error)},
                )

    def _record_stream_interruption(self, run_id: str, stage: str) -> None:
        task = asyncio.create_task(
            self._record_failure(
                run_id,
                stage,
                WorkflowStreamInterruptedError(
                    f"The workflow stream disconnected while {stage} was running. "
                    "Retry to resume from persisted artifacts."
                ),
            )
        )

        def log_failure(completed: asyncio.Task[None]) -> None:
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(
                    "Could not persist the interrupted %s workflow for run %s",
                    stage,
                    run_id,
                )

        task.add_done_callback(log_failure)

    @handler
    async def start(
        self,
        message: list[Message] | Message | str,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        del message
        run = await self._run_for_thread()
        if run.workflow_type == WorkflowType.PULL_REQUEST_REVIEW:
            await self._start_pull_request_review(run, ctx)
            return
        if run.state == RunState.INTAKE:
            await self._generate_specification(run.id, ctx)
            return
        if run.state == RunState.REGROUNDING:
            await self._generate_plan(run.id, ctx, "Source drift requires an updated plan.")
            return
        if run.state == RunState.AWAITING_SPEC_DECISION:
            await self._restore_artifact_decision(
                run,
                ArtifactKind.SPECIFICATION,
                run.active_spec_revision,
                "Accept this specification or refine it with feedback.",
                ctx,
            )
            return
        if run.state == RunState.AWAITING_PLAN_DECISION:
            await self._restore_artifact_decision(
                run,
                ArtifactKind.PLAN,
                run.active_plan_revision,
                "Accept the reviewed plan or refine it with feedback.",
                ctx,
            )
            return
        if run.state == RunState.FAILED:
            resume_plan = False
            failed_phase_id: str | None = None
            failed_phase_ordinal: int | None = None
            async with SessionFactory() as session:
                failed_run = await get_run(session, run.id)
                phase = await session.scalar(
                    select(Phase).where(
                        Phase.run_id == failed_run.id,
                        Phase.status == PhaseState.FAILED,
                    )
                )
                if phase is not None:
                    failed_phase_id = phase.id
                    failed_phase_ordinal = phase.ordinal
                else:
                    resume_plan = (
                        await session.scalar(
                            select(Decision.id)
                            .join(Artifact, Decision.artifact_id == Artifact.id)
                            .where(
                                Decision.run_id == failed_run.id,
                                Decision.decision_type == DecisionType.ACCEPT,
                                Artifact.kind == ArtifactKind.SPECIFICATION,
                            )
                            .limit(1)
                        )
                        is not None
                    )
            if failed_phase_id is not None:
                from mafia.services.lifecycle import recover_phase_pull_request

                if await recover_phase_pull_request(run.id, failed_phase_id):
                    await ctx.yield_output(
                        f"Recovered the pull request for phase {failed_phase_ordinal}; waiting for merge."
                    )
                    return
                async with SessionFactory() as session:
                    failed_run = await get_run(session, run.id)
                    phase = await session.get(Phase, failed_phase_id)
                    if phase is None:
                        raise LookupError(failed_phase_id)
                    phase.status = PhaseState.READY
                    phase.review_cycle += 1
                    phase.implementation_review_attempts = 0
                    phase.remediation_attempts = 0
                    phase.verification_attempts = 0
                    phase.candidate_base_sha = None
                    phase.candidate_diff_hash = None
                    failed_run.failure_code = None
                    failed_run.failure_message = None
                    await session.commit()
                    await transition_run(
                        session,
                        failed_run.id,
                        RunState.READY_FOR_PHASE,
                        expected_version=failed_run.version,
                        event_type="phase.retry_ready",
                        payload={
                            "phase_id": phase.id,
                            "review_cycle": phase.review_cycle,
                        },
                    )
                    await self._request_phase_start(phase, ctx)
                    return
            if resume_plan:
                await self._generate_plan(run.id, ctx, "Retry after an interrupted planning step.")
                return
            await self._generate_specification(run.id, ctx)
            return
        if run.state == RunState.READY_FOR_PHASE:
            async with SessionFactory() as session:
                phase = await session.scalar(
                    select(Phase).where(
                        Phase.run_id == run.id,
                        Phase.status == PhaseState.READY,
                    )
                )
                if phase is not None:
                    await self._request_phase_start(phase, ctx)
                    return
        await ctx.yield_output(f"Run {run.id} is currently {run.state.value}.")

    async def _start_pull_request_review(
        self,
        run: Run,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        if run.state in {RunState.INTAKE, RunState.FAILED}:
            if (
                run.state == RunState.FAILED
                and run.failure_code == "pull_request_review_post_failed"
                and run.active_review_revision is not None
            ):
                async with SessionFactory() as session:
                    failed_run = await get_run(session, run.id)
                    failed_run = await transition_run(
                        session,
                        failed_run.id,
                        RunState.AWAITING_PR_REVIEW_DECISION,
                        expected_version=failed_run.version,
                        event_type="pull_request_review.post_retry_ready",
                    )
                await self._restore_pull_request_review_decision(failed_run, ctx)
                return
            await self._review_pull_request(run.id, ctx)
            return
        if run.state == RunState.AWAITING_PR_REVIEW_DECISION:
            await self._restore_pull_request_review_decision(run, ctx)
            return
        await ctx.yield_output(f"Run {run.id} is currently {run.state.value}.")

    async def _restore_pull_request_review_decision(
        self,
        run: Run,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        if run.active_review_revision is None or run.pull_request_number is None:
            raise RuntimeError(f"Run {run.id} has no active pull-request review")
        async with SessionFactory() as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.kind
                    == ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED,
                    Artifact.revision == run.active_review_revision,
                )
            )
        if artifact is None:
            raise RuntimeError(
                f"Run {run.id} is waiting for pull-request review revision "
                f"{run.active_review_revision}, but the artifact is missing"
            )
        await ctx.request_info(
            PullRequestReviewDecisionRequest(
                run_id=run.id,
                artifact_id=artifact.id,
                revision=artifact.revision,
                pull_request_number=run.pull_request_number,
                prompt=(
                    "Review the consolidated findings. Post them to the pull request "
                    "or finish without posting."
                ),
            ),
            dict,
            request_id=f"pr-review-{artifact.id}-restore-{uuid4()}",
        )

    async def _review_pull_request(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        async with active_run_work(run_id):
            try:
                await self._review_pull_request_inner(run_id, ctx)
            except asyncio.CancelledError:
                self._record_stream_interruption(run_id, "pull_request_review")
                raise
            except Exception as error:
                await self._record_failure(run_id, "pull_request_review", error)
                raise

    async def _review_pull_request_inner(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            run = await transition_run(
                session,
                run.id,
                RunState.GROUNDING_PR_REVIEW,
                expected_version=run.version,
                event_type="pull_request_review.grounding_started",
                payload={"pull_request_number": run.pull_request_number},
            )
            operation_key = (
                f"pr-{run.pull_request_number}:attempt-{run.version}"
            )
            async with tracked_operation(
                run_id=run.id,
                operation_type="source.grounding",
                operation_key=operation_key,
                detail={
                    "reason": "pull_request_review",
                    "pull_request_number": run.pull_request_number,
                },
            ) as operation:
                snapshot, context = await capture_pull_request_snapshot(
                    session,
                    run,
                    run.repository,
                )
                operation.set_result(
                    {
                        "source_sha": snapshot.git_sha,
                        "base_sha": context.get("base_sha"),
                        "files_discovered": len(
                            snapshot.manifest.get("files", [])
                        ),
                        "changed_files": context.get("changed_files"),
                    }
                )
        identity = RepositoryIdentity(run.repository.owner, run.repository.name)
        base_sha = context.get("base_sha")
        if not isinstance(base_sha, str):
            raise RuntimeError("Pull-request snapshot has no base SHA")
        base_configuration = await run_command(
            (
                "git",
                "-C",
                snapshot.worktree_path,
                "show",
                f"{base_sha}:.mafia.toml",
            ),
            check=False,
        )
        if (
            base_configuration.returncode != 0
            and "does not exist in" not in base_configuration.stderr
        ):
            raise ProjectConfigurationError(
                f"Could not read repository .mafia.toml at {base_sha}: "
                f"{base_configuration.stderr.strip()[-1_000:]}"
            )
        project_configuration = resolve_project_configuration_content(
            identity,
            base_configuration.stdout if base_configuration.returncode == 0 else None,
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
            current_snapshot = await session.get(SourceSnapshot, snapshot.id)
            if current_snapshot is None:
                raise LookupError(snapshot.id)
            current_snapshot.issue_data = context
            await session.commit()
        run = await self._transition_state(
            run_id,
            RunState.REVIEWING_PR,
            "pull_request_review.started",
            {
                "pull_request_number": run.pull_request_number,
                "head_sha": snapshot.git_sha,
                "models": [run.primary_model, run.reviewer_model],
            },
        )
        generated = await asyncio.gather(
            self.review_service.review(
                run,
                snapshot,
                context,
                model=run.primary_model,
            ),
            self.review_service.review(
                run,
                snapshot,
                context,
                model=run.reviewer_model,
            ),
        )
        review_artifacts = [
            await self.review_service.persist_review(
                run,
                snapshot,
                review,
                model=model,
            )
            for model, review in zip(
                (run.primary_model, run.reviewer_model),
                generated,
                strict=True,
            )
        ]
        run = await self._transition_state(
            run_id,
            RunState.CONSOLIDATING_PR_REVIEW,
            "pull_request_review.adjudication_started",
            {
                "review_artifacts": [
                    artifact.id for artifact in review_artifacts
                ],
                "adjudicator_model": run.primary_model,
            },
        )
        consolidated = await self.review_service.consolidate(
            run,
            snapshot,
            context,
            review_artifacts,
        )
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            run.active_review_revision = consolidated.revision
            await session.commit()
            run = await transition_run(
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
            )
        await ctx.add_event(
            WorkflowEvent(
                type="data",
                data={
                    "event_type": "artifact",
                    "kind": "pull_request_review_consolidated",
                    "artifact_id": consolidated.id,
                    "revision": consolidated.revision,
                    "markdown": consolidated.rendered_markdown,
                },
            )
        )
        if run.pull_request_number is None:
            raise RuntimeError("Pull-request review run lost its pull request number")
        await ctx.request_info(
            PullRequestReviewDecisionRequest(
                run_id=run.id,
                artifact_id=consolidated.id,
                revision=consolidated.revision,
                pull_request_number=run.pull_request_number,
                prompt=(
                    "Review the consolidated findings. Post them to the pull request "
                    "or finish without posting."
                ),
            ),
            dict,
            request_id=f"pr-review-{consolidated.id}",
        )

    @response_handler(request=PullRequestReviewDecisionRequest, response=dict)
    async def decide_pull_request_review(
        self,
        original_request: PullRequestReviewDecisionRequest,
        response: dict[str, Any],
        ctx: WorkflowContext[Any, str],
    ) -> None:
        async with active_run_work(original_request.run_id):
            try:
                await self._decide_pull_request_review(
                    original_request,
                    response,
                    ctx,
                )
            except asyncio.CancelledError:
                self._record_stream_interruption(
                    original_request.run_id,
                    "pull_request_review_post",
                )
                raise
            except Exception as error:
                await self._record_failure(
                    original_request.run_id,
                    "pull_request_review_post",
                    error,
                )
                raise

    async def _decide_pull_request_review(
        self,
        original_request: PullRequestReviewDecisionRequest,
        response: dict[str, Any],
        ctx: WorkflowContext[Any, str],
    ) -> None:
        decision = PullRequestReviewDecision.model_validate(response)
        async with SessionFactory() as session:
            run = await get_run(session, original_request.run_id)
            artifact = await session.get(Artifact, original_request.artifact_id)
            if (
                artifact is None
                or artifact.run_id != run.id
                or artifact.kind
                != ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED
                or artifact.revision != run.active_review_revision
                or artifact.revision != original_request.revision
                or run.state != RunState.AWAITING_PR_REVIEW_DECISION
            ):
                await ctx.yield_output(
                    "Ignored a stale pull-request review decision."
                )
                return
            decision_type = (
                DecisionType.POST_REVIEW
                if decision.action == "post"
                else DecisionType.FINISH_REVIEW
                if decision.action == "finish"
                else DecisionType.CANCEL
            )
            session.add(
                Decision(
                    run_id=run.id,
                    artifact_id=artifact.id,
                    decision_type=decision_type,
                )
            )
            await session.commit()
            if decision.action == "cancel":
                await transition_run(
                    session,
                    run.id,
                    RunState.CANCELLED,
                    expected_version=run.version,
                    event_type="run.cancelled",
                )
                await ctx.yield_output("Pull-request review cancelled.")
                return
            if decision.action == "finish":
                await transition_run(
                    session,
                    run.id,
                    RunState.COMPLETED,
                    expected_version=run.version,
                    event_type="pull_request_review.finished",
                    payload={"posted": False},
                )
                await ctx.yield_output(
                    "Pull-request review completed without posting."
                )
                return
            run = await transition_run(
                session,
                run.id,
                RunState.POSTING_PR_REVIEW,
                expected_version=run.version,
                event_type="pull_request_review.post_started",
                payload={"pull_request_number": original_request.pull_request_number},
            )
            identity = RepositoryIdentity(
                run.repository.owner,
                run.repository.name,
            )
            markdown = artifact.rendered_markdown
        async with tracked_operation(
            run_id=run.id,
            operation_type="github.pull_request_comment",
            operation_key=artifact.id,
            detail={
                "repository": identity.slug,
                "pull_request_number": original_request.pull_request_number,
                "artifact_id": artifact.id,
            },
        ) as operation:
            url = await post_pull_request_comment(
                identity,
                original_request.pull_request_number,
                run_id=run.id,
                artifact_id=artifact.id,
                markdown=markdown,
            )
            operation.set_result({"comment_url": url})
        async with SessionFactory() as session:
            run = await get_run(session, run.id)
            await transition_run(
                session,
                run.id,
                RunState.COMPLETED,
                expected_version=run.version,
                event_type="pull_request_review.posted",
                payload={"comment_url": url},
            )
        await ctx.yield_output(f"Pull-request review posted: {url}")

    async def _restore_artifact_decision(
        self,
        run: Run,
        kind: ArtifactKind,
        revision: int | None,
        prompt: str,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        if revision is None:
            raise RuntimeError(f"Run {run.id} has no active {kind.value} revision")
        async with SessionFactory() as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.kind == kind,
                    Artifact.revision == revision,
                )
            )
        if artifact is None:
            raise RuntimeError(
                f"Run {run.id} is waiting for {kind.value} revision {revision}, "
                "but the artifact is missing"
            )
        artifact_kind = "specification" if kind == ArtifactKind.SPECIFICATION else "plan"
        await ctx.request_info(
            ArtifactDecisionRequest(
                run_id=run.id,
                artifact_id=artifact.id,
                artifact_kind=artifact_kind,
                revision=artifact.revision,
                prompt=prompt,
            ),
            dict,
            request_id=f"{kind.value}-{artifact.id}-restore-{uuid4()}",
        )

    async def _generate_specification(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
        feedback: str | None = None,
    ) -> None:
        async with active_run_work(run_id):
            await self._generate_specification_guarded(run_id, ctx, feedback)

    async def _generate_specification_guarded(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
        feedback: str | None = None,
    ) -> None:
        try:
            await self._generate_specification_inner(run_id, ctx, feedback)
        except asyncio.CancelledError:
            self._record_stream_interruption(run_id, "specification")
            raise
        except Exception as error:
            await self._record_failure(run_id, "specification", error)
            raise

    async def _generate_specification_inner(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
        feedback: str | None = None,
    ) -> None:
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
                snapshot = await capture_source_snapshot(
                    session,
                    run,
                    run.repository,
                    reason=reason,
                )
                operation.set_result(
                    {
                        "source_sha": snapshot.git_sha,
                        "files_discovered": len(snapshot.manifest.get("files", [])),
                        "manifests_found": len(snapshot.manifest.get("manifests", [])),
                        "instructions_found": len(snapshot.instructions),
                    }
                )
            artifact = await self.generator.specification(
                session,
                run,
                snapshot,
                feedback=feedback,
            )
            run.active_spec_revision = artifact.revision
            await session.commit()
            run = await transition_run(
                session,
                run.id,
                RunState.AWAITING_SPEC_DECISION,
                expected_version=run.version,
                event_type="specification.generated",
                payload={"artifact_id": artifact.id, "revision": artifact.revision},
            )
            await ctx.add_event(
                WorkflowEvent(
                    type="data",
                    data={
                        "event_type": "artifact",
                        "kind": "specification",
                        "artifact_id": artifact.id,
                        "revision": artifact.revision,
                        "markdown": artifact.rendered_markdown,
                    },
                )
            )
            await ctx.request_info(
                ArtifactDecisionRequest(
                    run_id=run.id,
                    artifact_id=artifact.id,
                    artifact_kind="specification",
                    revision=artifact.revision,
                    prompt="Accept this specification or refine it with feedback.",
                ),
                dict,
                request_id=f"spec-{artifact.id}",
            )

    @response_handler(request=ArtifactDecisionRequest, response=dict)
    async def decide_artifact(
        self,
        original_request: ArtifactDecisionRequest,
        response: dict[str, Any],
        ctx: WorkflowContext[Any, str],
    ) -> None:
        async with active_run_work(original_request.run_id):
            await self._decide_artifact(original_request, response, ctx)

    async def _decide_artifact(
        self,
        original_request: ArtifactDecisionRequest,
        response: dict[str, Any],
        ctx: WorkflowContext[Any, str],
    ) -> None:
        decision = ArtifactDecision.model_validate(response)
        async with SessionFactory() as session:
            run = await get_run(session, original_request.run_id)
            artifact = await session.get(Artifact, original_request.artifact_id)
            expected_state = (
                RunState.AWAITING_SPEC_DECISION
                if original_request.artifact_kind == "specification"
                else RunState.AWAITING_PLAN_DECISION
            )
            active_revision = (
                run.active_spec_revision
                if original_request.artifact_kind == "specification"
                else run.active_plan_revision
            )
            if (
                artifact is None
                or artifact.run_id != run.id
                or artifact.revision != active_revision
                or artifact.revision != original_request.revision
                or run.state != expected_state
            ):
                await ctx.yield_output(
                    "Ignored a stale artifact decision because the workflow has moved on."
                )
                return
            session.add(
                Decision(
                    run_id=run.id,
                    artifact_id=artifact.id,
                    decision_type=DecisionType(decision.action),
                    feedback=decision.feedback,
                )
            )
            await session.commit()
        if decision.action == "cancel":
            async with SessionFactory() as session:
                run = await get_run(session, original_request.run_id)
                await transition_run(
                    session,
                    run.id,
                    RunState.CANCELLED,
                    expected_version=run.version,
                    event_type="run.cancelled",
                )
            await ctx.yield_output("Run cancelled.")
            return
        if original_request.artifact_kind == "specification":
            if decision.action == "refine":
                await self._generate_specification(original_request.run_id, ctx, decision.feedback)
            else:
                await self._generate_plan(original_request.run_id, ctx)
            return
        if decision.action == "refine":
            await self._generate_plan(original_request.run_id, ctx, decision.feedback)
        else:
            await self._accept_plan(original_request.run_id, artifact, ctx)

    async def _generate_plan(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
        feedback: str | None = None,
    ) -> None:
        async with active_run_work(run_id):
            await self._generate_plan_guarded(run_id, ctx, feedback)

    async def _generate_plan_guarded(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
        feedback: str | None = None,
    ) -> None:
        try:
            await self._generate_plan_inner(run_id, ctx, feedback)
        except asyncio.CancelledError:
            self._record_stream_interruption(run_id, "planning")
            raise
        except Exception as error:
            await self._record_failure(run_id, "planning", error)
            raise

    async def _transition_state(
        self,
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

    async def _generate_plan_inner(
        self,
        run_id: str,
        ctx: WorkflowContext[Any, str],
        feedback: str | None = None,
    ) -> None:
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
                snapshot = await capture_source_snapshot(
                    session,
                    run,
                    run.repository,
                    reason=reason,
                )
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
                raise RuntimeError("Accepted specification artifact is missing")
            preserved_phases = list(
                await session.scalars(
                    select(Phase)
                    .where(
                        Phase.run_id == run.id,
                        Phase.status.in_(
                            {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}
                        ),
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
                    f"{feedback or ''}\n\n"
                    "Merged phases and phases with an open pull request are immutable. Include "
                    "each one at its existing ordinal without assigning new work to it. Add new "
                    f"work only after those phases:\n{completed_context}"
                ).strip()

        run = await self._transition_state(
            run_id,
            RunState.GENERATING_PLAN,
            "plan.generation_started",
            {"source_sha": snapshot.git_sha},
        )
        draft_plan = await self.generator.draft_plan(
            run,
            snapshot,
            specification,
            feedback=feedback,
        )
        run = await self._transition_state(
            run_id,
            RunState.REVIEWING_PLAN,
            "plan.review_started",
            {
                "source_sha": snapshot.git_sha,
                "draft_plan_artifact_id": draft_plan.id,
                "reviewer_model": run.reviewer_model,
            },
        )
        review = await self.generator.adversarial_review(
            run,
            snapshot,
            specification,
            draft_plan,
        )
        run = await self._transition_state(
            run_id,
            RunState.ADJUDICATING_PLAN,
            "plan.adjudication_started",
            {
                "review_artifact_id": review.id,
                "primary_model": run.primary_model,
            },
        )
        resolution = await self.generator.adjudicate_plan(
            run,
            snapshot,
            specification,
            draft_plan,
            review,
        )
        run = await self._transition_state(
            run_id,
            RunState.PERSISTING_PLAN,
            "plan.persistence_started",
            {
                "review_artifact_id": review.id,
                "dispositions": len(resolution.dispositions),
            },
        )
        final_plan, ledger = await self.generator.persist_final_plan(
            run,
            snapshot,
            review,
            resolution,
        )
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            run.active_plan_revision = final_plan.revision
            await session.commit()
        run = await self._transition_state(
            run_id,
            RunState.AWAITING_PLAN_DECISION,
            "plan.review_completed",
            {
                "plan_artifact_id": final_plan.id,
                "review_artifact_id": review.id,
                "ledger_artifact_id": ledger.id,
            },
        )
        await ctx.add_event(
            WorkflowEvent(
                type="data",
                data={
                    "event_type": "artifact",
                    "kind": "plan",
                    "artifact_id": final_plan.id,
                    "revision": final_plan.revision,
                    "markdown": final_plan.rendered_markdown,
                    "review_artifact_id": review.id,
                    "ledger_artifact_id": ledger.id,
                },
            )
        )
        await ctx.request_info(
            ArtifactDecisionRequest(
                run_id=run.id,
                artifact_id=final_plan.id,
                artifact_kind="plan",
                revision=final_plan.revision,
                prompt="Accept the reviewed plan or refine it with feedback.",
            ),
            dict,
            request_id=f"plan-{final_plan.id}",
        )

    async def _accept_plan(
        self,
        run_id: str,
        artifact: Artifact,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        plan = ImplementationPlan.model_validate(artifact.structured_data)
        async with SessionFactory() as session:
            run = await get_run(session, run_id)
            if (
                run.state != RunState.AWAITING_PLAN_DECISION
                or run.active_plan_revision != artifact.revision
                or artifact.run_id != run.id
            ):
                raise RuntimeError("The plan decision is stale")
            snapshot = await session.get(SourceSnapshot, artifact.source_snapshot_id)
            if snapshot is None:
                raise RuntimeError("Plan source snapshot is missing")
            existing_phases = {
                phase.ordinal: phase
                for phase in await session.scalars(select(Phase).where(Phase.run_id == run.id))
            }
            protected_phases = {
                ordinal: phase
                for ordinal, phase in existing_phases.items()
                if phase.status in {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}
            }
            planned_phases = {item.ordinal: item for item in plan.phases}
            missing_protected = sorted(set(protected_phases) - set(planned_phases))
            if missing_protected:
                raise RuntimeError(
                    "The revised plan omitted immutable phases: "
                    + ", ".join(str(ordinal) for ordinal in missing_protected)
                )
            planned_ordinals = {item.ordinal for item in plan.phases}
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
                elif existing.status not in {
                    PhaseState.MERGED,
                    PhaseState.WAITING_FOR_MERGE,
                }:
                    existing.title = item.title
                    existing.objective = item.objective
                    existing.dependencies = item.dependencies
                    existing.details = item.model_dump(mode="json")
                    existing.status = PhaseState.PENDING
                    existing.plan_revision = artifact.revision
                    existing.source_sha = snapshot.git_sha
            for ordinal, existing in existing_phases.items():
                if (
                    ordinal not in planned_ordinals
                    and existing.status
                    not in {PhaseState.MERGED, PhaseState.WAITING_FOR_MERGE}
                ):
                    await session.delete(existing)
            await session.flush()
            merged_ordinals = {
                ordinal
                for ordinal, existing in existing_phases.items()
                if existing.status == PhaseState.MERGED
            }
            waiting_phase = next(
                (
                    existing
                    for existing in existing_phases.values()
                    if existing.status == PhaseState.WAITING_FOR_MERGE
                ),
                None,
            )
            candidates = list(
                await session.scalars(
                    select(Phase)
                    .where(Phase.run_id == run.id, Phase.status == PhaseState.PENDING)
                    .order_by(Phase.ordinal)
                )
            )
            ready = next(
                (
                    candidate
                    for candidate in candidates
                    if set(candidate.dependencies).issubset(merged_ordinals)
                ),
                None,
            ) if waiting_phase is None else None
            if ready is not None:
                ready.status = PhaseState.READY
            await session.commit()
            if waiting_phase is not None:
                target = RunState.WAITING_FOR_MERGE
                event_type = "plan.accepted_waiting_for_merge"
            elif ready is not None:
                target = RunState.READY_FOR_PHASE
                event_type = "plan.accepted"
            else:
                target = RunState.COMPLETED
                event_type = "plan.accepted_complete"
            run = await transition_run(
                session,
                run.id,
                target,
                expected_version=run.version,
                event_type=event_type,
                payload={"revision": artifact.revision},
            )
            if waiting_phase is not None:
                await ctx.yield_output(
                    f"Plan accepted; pull request {waiting_phase.pr_number} must merge before "
                    "the next phase can start."
                )
                return
            if target == RunState.COMPLETED:
                await ctx.yield_output("Plan accepted; all planned phases are already complete.")
                return
            phase = await session.scalar(
                select(Phase).where(Phase.run_id == run.id, Phase.status == PhaseState.READY)
            )
            if phase is None:
                await ctx.yield_output("Plan accepted; no phases are ready.")
                return
            await self._request_phase_start(phase, ctx)

    async def _request_phase_start(
        self,
        phase: Phase,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        async with SessionFactory() as session:
            run = await get_run(session, phase.run_id)
            identity = RepositoryIdentity(run.repository.owner, run.repository.name)
            try:
                validation_available, validation_source = (
                    await source_validation_status(
                        identity,
                        run.repository.cache_path,
                        phase.source_sha,
                    )
                )
            except ProjectConfigurationError as error:
                await ctx.yield_output(
                    f"Phase {phase.ordinal} cannot start: {error}"
                )
                return
        if not validation_available:
            await ctx.yield_output(
                f"Phase {phase.ordinal} cannot start until deterministic validation is "
                f"configured for {identity.slug}. Add .mafia.toml to the repository or "
                "configure the project in mafia."
            )
            return
        await ctx.request_info(
            PhaseDecisionRequest(
                run_id=phase.run_id,
                phase_id=phase.id,
                ordinal=phase.ordinal,
                title=phase.title,
                objective=phase.objective,
                prompt=(
                    f"Start phase {phase.ordinal}: {phase.title}? "
                    f"Deterministic validation source: {validation_source}."
                ),
            ),
            dict,
            request_id=f"phase-{phase.id}",
        )

    @response_handler(request=PhaseDecisionRequest, response=dict)
    async def decide_phase(
        self,
        original_request: PhaseDecisionRequest,
        response: dict[str, Any],
        ctx: WorkflowContext[Any, str],
    ) -> None:
        decision = PhaseDecision.model_validate(response)
        async with SessionFactory() as session:
            run = await get_run(session, original_request.run_id)
            phase = await session.get(Phase, original_request.phase_id)
            if (
                phase is None
                or phase.run_id != run.id
                or run.state != RunState.READY_FOR_PHASE
                or phase.status != PhaseState.READY
            ):
                await ctx.yield_output(
                    "Ignored a stale phase decision because the phase is no longer ready."
                )
                return
            if decision.action == "cancel":
                session.add(
                    Decision(
                        run_id=run.id,
                        phase_id=phase.id,
                        decision_type=DecisionType.CANCEL,
                    )
                )
                await session.commit()
                await transition_run(
                    session,
                    run.id,
                    RunState.CANCELLED,
                    expected_version=run.version,
                    event_type="run.cancelled",
                )
                await ctx.yield_output("Run cancelled.")
                return
        from mafia.services.execution import PhaseNotReadyError, execute_phase

        try:
            await execute_phase(original_request.run_id, original_request.phase_id, ctx)
        except PhaseNotReadyError:
            async with SessionFactory() as session:
                run = await get_run(session, original_request.run_id)
                phase = await session.get(Phase, original_request.phase_id)
                if (
                    phase is None
                    or phase.run_id != run.id
                    or run.state != RunState.READY_FOR_PHASE
                    or phase.status != PhaseState.READY
                ):
                    await ctx.yield_output(
                        "Ignored a stale phase decision because the phase is no longer ready."
                    )
                    return
            raise


def build_run_workflow(
    thread_id: str,
    checkpoint_storage: CheckpointStorage | None = None,
):
    executor = RunWorkflowExecutor(thread_id)
    return WorkflowBuilder(
        name=f"mafia-run-{thread_id}",
        start_executor=executor,
        output_from=[executor],
        checkpoint_storage=checkpoint_storage,
    ).build()

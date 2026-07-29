import asyncio
import os
import re
from pathlib import Path
from typing import Any

from agent_framework import WorkflowContext, WorkflowEvent
from mafia.agents.copilot import CopilotAgentService
from mafia.agents.prompts import IMPLEMENTATION_INSTRUCTIONS
from mafia.config import get_settings
from mafia.db.models import Decision, Phase, Run
from mafia.db.session import SessionFactory
from mafia.domain.artifacts import PhaseExecutionReport
from mafia.domain.enums import ArtifactKind, DecisionType, PhaseState, RunState
from mafia.services.artifacts import persist_artifact
from mafia.services.commands import run_command
from mafia.services.devcontainers import (
    close_environment_after_failure,
    create_execution_environment,
    find_devcontainer_config,
)
from mafia.services.github import gh_json
from mafia.services.lifecycle import pull_request_for_branch
from mafia.services.locks import acquire_repository_lock, release_repository_lock
from mafia.services.operations import active_run_work, tracked_operation
from mafia.services.repositories import RepositoryIdentity
from mafia.services.runs import get_run, transition_run
from mafia.services.sandbox import ExecutionEnvironment
from mafia.services.workspaces import WorkspaceService
from sqlalchemy import select
from sqlalchemy.orm import selectinload


class PhaseExecutionError(RuntimeError):
    pass


async def validate_worktree_diff(worktree: Path) -> list[str]:
    changed = await run_command(
        ("git", "-C", str(worktree), "diff", "--name-only", "-z", "HEAD")
    )
    untracked = await run_command(
        ("git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z")
    )
    changed_paths = sorted(
        {
            path
            for path in (changed.stdout + untracked.stdout).split("\x00")
            if path
        }
    )
    root = worktree.resolve(strict=True)  # noqa: ASYNC240
    for relative in changed_paths:
        candidate = root / relative
        if candidate.is_symlink():
            target = Path(os.readlink(candidate))
            if target.is_absolute():
                raise PhaseExecutionError(f"Changed symlink has an absolute target: {relative}")
            resolved_target = (candidate.parent / target).resolve(strict=False)
            if resolved_target != root and root not in resolved_target.parents:
                raise PhaseExecutionError(f"Changed symlink escapes the worktree: {relative}")
        stage = await run_command(
            ("git", "-C", str(worktree), "ls-files", "--stage", "--", relative),
            check=False,
        )
        if any(line.startswith("160000 ") for line in stage.stdout.splitlines()):
            raise PhaseExecutionError(f"Submodule changes are not allowed: {relative}")
    return changed_paths


def branch_name(run_id: str, phase: Phase) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", phase.title.casefold()).strip("-")[:40]
    return f"mafia/{run_id[:8]}/phase-{phase.ordinal}-{slug}"


async def _phase_with_run(run_id: str, phase_id: str) -> tuple[Run, Phase]:
    async with SessionFactory() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id).options(selectinload(Run.repository)))
        phase = await session.get(Phase, phase_id)
        if run is None or phase is None or phase.run_id != run_id:
            raise LookupError(phase_id)
        return run, phase


async def _record_phase_failure(run_id: str, phase_id: str, error: Exception) -> None:
    async with SessionFactory() as session:
        current_run = await get_run(session, run_id)
        current_phase = await session.get(Phase, phase_id)
        if current_phase is not None:
            current_phase.status = PhaseState.FAILED
        current_run.failure_code = "phase_execution_failed"
        current_run.failure_message = str(error)
        await session.commit()
        if current_run.state == RunState.EXECUTING_PHASE:
            await transition_run(
                session,
                current_run.id,
                RunState.FAILED,
                expected_version=current_run.version,
                event_type="phase.execution_failed",
                payload={"phase_id": phase_id, "error": str(error)},
            )


async def _cleanup_phase_resources(
    environment: ExecutionEnvironment | None,
    repository_id: str,
    lease_token: str,
) -> None:
    await close_environment_after_failure(environment)
    await release_repository_lock(repository_id, lease_token)


async def execute_phase(
    run_id: str,
    phase_id: str,
    ctx: WorkflowContext[Any, str],
) -> None:
    async with active_run_work(run_id):
        await _execute_phase(run_id, phase_id, ctx)


async def _execute_phase(
    run_id: str,
    phase_id: str,
    ctx: WorkflowContext[Any, str],
) -> None:
    run, phase = await _phase_with_run(run_id, phase_id)
    if run.state != RunState.READY_FOR_PHASE or phase.status != PhaseState.READY:
        raise PhaseExecutionError("Phase is not ready for execution")

    workspaces = WorkspaceService()
    identity = RepositoryIdentity(run.repository.owner, run.repository.name)
    async with tracked_operation(
        run_id=run.id,
        phase_id=phase.id,
        operation_type="source.refresh",
        operation_key=f"phase-{phase.ordinal}",
        detail={"repository": identity.slug, "phase": phase.ordinal},
    ) as operation:
        metadata, cache, current_sha = await workspaces.refresh(identity)
        operation.set_result(
            {"source_sha": current_sha, "default_branch": metadata.default_branch}
        )
    if current_sha != phase.source_sha:
        async with SessionFactory() as session:
            current_run = await get_run(session, run_id)
            await transition_run(
                session,
                current_run.id,
                RunState.REGROUNDING,
                expected_version=current_run.version,
                event_type="source.drift_detected",
                payload={"expected": phase.source_sha, "actual": current_sha},
            )
        await ctx.yield_output(
            "The default branch changed after plan acceptance. The remaining plan must be re-grounded."
        )
        return

    lease_token = await acquire_repository_lock(
        run.repository_id,
        run.id,
        phase.id,
    )
    branch = branch_name(run_id, phase)
    try:
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="worktree.create",
            operation_key=branch,
            detail={"branch": branch, "source_sha": current_sha},
        ) as operation:
            worktree = await workspaces.create_phase_worktree(
                identity, cache, current_sha, branch
            )
            operation.set_result({"worktree": str(worktree), "branch": branch})
    except BaseException:
        await asyncio.shield(release_repository_lock(run.repository_id, lease_token))
        raise
    environment: ExecutionEnvironment | None = None
    try:
        async with SessionFactory() as session:
            current_run = await get_run(session, run_id)
            await transition_run(
                session,
                current_run.id,
                RunState.EXECUTING_PHASE,
                expected_version=current_run.version,
                event_type="phase.execution_started",
                payload={"phase_id": phase.id, "branch": branch},
            )
            current_phase = await session.get(Phase, phase.id)
            if current_phase is None:
                raise LookupError(phase.id)
            current_phase.status = PhaseState.EXECUTING
            current_phase.branch_name = branch
            current_phase.worktree_path = str(worktree)
            session.add(
                Decision(
                    run_id=run_id,
                    phase_id=phase.id,
                    decision_type=DecisionType.START_PHASE,
                )
            )
            await session.commit()
        settings = get_settings()
        config_path = (
            None
            if settings.execution_mode == "host"
            else find_devcontainer_config(worktree)
        )
        candidate = (
            "host"
            if settings.execution_mode == "host"
            else "devcontainer"
            if config_path is not None
            else "bubblewrap"
        )
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="environment.prepare",
            operation_key=phase.id,
            timeout_seconds=settings.devcontainer_setup_timeout_seconds,
            detail={
                "candidate": candidate,
                "config": (
                    str(config_path.relative_to(worktree))
                    if config_path is not None
                    else None
                ),
            },
        ) as operation:
            environment = await create_execution_environment(
                worktree,
                progress=operation.update_detail,
            )
            operation.set_result(environment.description())
    except Exception as error:
        await _record_phase_failure(run_id, phase.id, error)
        await asyncio.shield(
            _cleanup_phase_resources(environment, run.repository_id, lease_token)
        )
        raise
    except BaseException:
        await asyncio.shield(
            _cleanup_phase_resources(environment, run.repository_id, lease_token)
        )
        raise
    def read_file(path: str, line_start: int = 1, line_end: int = 500) -> str:
        """Read a repository-relative file with line numbers."""
        return environment.read_file(path, line_start, line_end)

    def write_file(path: str, content: str) -> str:
        """Write UTF-8 content to a repository-relative file."""
        return environment.write_file(path, content)

    async def run_in_environment(
        command: str, timeout_seconds: int = 120
    ) -> dict[str, object]:
        """Run a shell command in the phase's selected execution environment."""
        return await environment.tool_run(command, timeout_seconds)

    try:
        report = await CopilotAgentService().run_structured(
            model=run.primary_model,
            instructions=IMPLEMENTATION_INSTRUCTIONS,
            prompt=(
                f"Repository: {identity.slug}\n"
                f"Source SHA: {current_sha}\n"
                f"Approved phase:\n{phase.details}\n"
                "Implement this phase completely, validate it, and return the execution report."
            ),
            response_type=PhaseExecutionReport,
            working_directory=worktree,
            tools=[read_file, write_file, run_in_environment],
            timeout_seconds=3600,
            run_id=run.id,
            phase_id=phase.id,
            operation_type="model.phase_implementation",
            operation_key=phase.id,
            operation_detail={
                "phase": phase.ordinal,
                "source_sha": current_sha,
                "branch": branch,
            },
            operation_detail_provider=environment.activity_snapshot,
        )
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="diff.validation",
            operation_key=phase.id,
            detail={"reported_changed_files": report.changed_files},
        ) as operation:
            status = await run_command(
                ("git", "-C", str(worktree), "status", "--porcelain")
            )
            if not status.stdout.strip():
                raise PhaseExecutionError("The implementation agent produced no changes")
            changed_files = await validate_worktree_diff(worktree)
            await run_command(("git", "-C", str(worktree), "diff", "--check"))
            operation.set_result({"changed_files": changed_files})
        for index, validation_command in enumerate(report.validation_commands, 1):
            async with tracked_operation(
                run_id=run.id,
                phase_id=phase.id,
                operation_type="environment.validation",
                operation_key=f"{phase.id}:{index}",
                timeout_seconds=900,
                detail={"command": validation_command, "ordinal": index},
            ) as operation:
                result = await environment.run(validation_command, timeout_seconds=900)
                operation.set_result({"returncode": result.returncode})
                if result.returncode != 0:
                    raise PhaseExecutionError(
                        f"Validation failed: {validation_command}\n{result.stderr[-2_000:]}"
                    )
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="environment.cleanup",
            operation_key=phase.id,
            timeout_seconds=60,
            detail=environment.description(),
        ) as operation:
            await environment.close()
            operation.set_result({"removed": True, **environment.description()})
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="git.commit",
            operation_key=phase.id,
            detail={"branch": branch, "changed_files": changed_files},
        ) as operation:
            await run_command(("git", "-C", str(worktree), "add", "--all"))
            await run_command(
                (
                    "git",
                    "-C",
                    str(worktree),
                    "commit",
                    "-m",
                    f"feat: {phase.title}",
                    "-m",
                    "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
                )
            )
            commit = (
                await run_command(("git", "-C", str(worktree), "rev-parse", "HEAD"))
            ).stdout.strip()
            operation.set_result({"commit_sha": commit, "branch": branch})
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="git.push",
            operation_key=phase.id,
            detail={"branch": branch, "commit_sha": commit},
        ) as operation:
            await run_command(
                ("git", "-C", str(worktree), "push", "--set-upstream", "origin", branch)
            )
            operation.set_result({"branch": branch, "commit_sha": commit})
        body = (
            f"## Summary\n\n{report.summary}\n\n"
            f"## Source\n\nPlanned from `{current_sha}`.\n\n"
            "## Validation\n\n" + "\n".join(f"- `{command}`" for command in report.validation_commands)
        )
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="github.pull_request",
            operation_key=phase.id,
            detail={"repository": identity.slug, "branch": branch},
        ) as operation:
            pr_data = await pull_request_for_branch(identity, branch)
            if pr_data is None:
                pr_url = (
                    await run_command(
                        (
                            "gh",
                            "pr",
                            "create",
                            "--repo",
                            identity.slug,
                            "--base",
                            metadata.default_branch,
                            "--head",
                            branch,
                            "--title",
                            f"Phase {phase.ordinal}: {phase.title}",
                            "--body",
                            body,
                        ),
                        cwd=worktree,
                    )
                ).stdout.strip()
                pr_data = await gh_json(
                    "pr",
                    "view",
                    pr_url,
                    "--repo",
                    identity.slug,
                    "--json",
                    "number,url",
                )
            if not isinstance(pr_data, dict):
                raise PhaseExecutionError("Could not resolve the created pull request")
            pr_number = pr_data.get("number")
            resolved_pr_url = pr_data.get("url")
            if not isinstance(pr_number, int) or not isinstance(resolved_pr_url, str):
                raise PhaseExecutionError("Created pull request response is incomplete")
            operation.set_result(
                {"pr_number": pr_number, "pr_url": resolved_pr_url}
            )
        async with SessionFactory() as session:
            current_run = await get_run(session, run_id)
            current_phase = await session.get(Phase, phase.id)
            if current_phase is None:
                raise LookupError(phase.id)
            current_phase.commit_sha = commit
            current_phase.pr_number = pr_number
            current_phase.pr_url = resolved_pr_url
            current_phase.status = PhaseState.WAITING_FOR_MERGE
            await persist_artifact(
                session,
                run=current_run,
                kind=ArtifactKind.PHASE_RESULT,
                data=report,
                markdown=f"# Phase {phase.ordinal} result\n\n{report.summary}",
                model=run.primary_model,
                snapshot=None,
            )
            await session.commit()
            current_run = await transition_run(
                session,
                current_run.id,
                RunState.PR_OPEN,
                expected_version=current_run.version,
                event_type="pull_request.created",
                payload={"phase_id": phase.id, "url": current_phase.pr_url},
            )
            await transition_run(
                session,
                current_run.id,
                RunState.WAITING_FOR_MERGE,
                expected_version=current_run.version,
                event_type="pull_request.waiting_for_merge",
                payload={"phase_id": phase.id, "number": current_phase.pr_number},
            )
        await ctx.add_event(
            WorkflowEvent(
                type="data",
                data={
                    "event_type": "phase_result",
                    "phase_id": phase.id,
                    "summary": report.summary,
                    "commit": commit,
                    "pr_url": resolved_pr_url,
                },
            )
        )
        await ctx.yield_output(
            f"Phase {phase.ordinal} is complete and pull request {resolved_pr_url} is waiting for merge."
        )
    except Exception as error:
        await _record_phase_failure(run_id, phase.id, error)
        raise
    finally:
        await asyncio.shield(
            _cleanup_phase_resources(environment, run.repository_id, lease_token)
        )

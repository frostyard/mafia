import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mafia.db.models import Operation, Phase, RepositoryLock, Run
from mafia.db.session import SessionFactory
from mafia.domain.enums import PhaseState, RunState
from mafia.services.commands import CommandError, run_command
from mafia.services.github import get_pr, gh_json
from mafia.services.repositories import (
    RepositoryIdentity,
    require_repository_owner,
)
from mafia.services.run_control import phase_pending_action
from mafia.services.runs import (
    PendingActionSpec,
    get_run,
    transition_run,
    transition_with_pending_action,
)
from mafia.services.workspaces import WorkspaceService, reset_and_verify_origin
from sqlalchemy import delete, select


class ReconciliationError(RuntimeError):
    pass


logger = logging.getLogger(__name__)
_reconciliation_locks: dict[str, asyncio.Lock] = {}


async def pull_request_for_branch(
    identity: RepositoryIdentity,
    branch: str,
) -> dict[str, Any] | None:
    require_repository_owner(identity)
    result = await gh_json(
        "pr",
        "list",
        "--repo",
        identity.slug,
        "--head",
        branch,
        "--state",
        "all",
        "--limit",
        "1",
        "--json",
        "number,url,state,mergedAt,headRefName",
    )
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return None
    pull_request = cast(dict[str, Any], result[0])
    if pull_request.get("state") == "CLOSED" and pull_request.get("mergedAt") is None:
        return None
    return pull_request


@asynccontextmanager
async def _reconciliation_lock(run_id: str) -> AsyncGenerator[None]:
    lock = _reconciliation_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        yield


async def _create_recovery_pr(
    identity: RepositoryIdentity,
    phase: Phase,
    default_branch: str,
    commit: str,
) -> str:
    require_repository_owner(identity)
    body = (
        "## Summary\n\n"
        f"Recovered phase {phase.ordinal} after the local process stopped.\n\n"
        f"## Source\n\nPlanned from `{phase.source_sha}` and committed as `{commit}`."
    )
    result = await run_command(
        (
            "gh",
            "pr",
            "create",
            "--repo",
            identity.slug,
            "--base",
            default_branch,
            "--head",
            phase.branch_name or "",
            "--title",
            f"Phase {phase.ordinal}: {phase.title}",
            "--body",
            body,
        ),
        cwd=Path(phase.worktree_path) if phase.worktree_path else None,
    )
    return result.stdout.strip()


async def _branch_commit(
    identity: RepositoryIdentity,
    phase: Phase,
    cache_path: str | None,
) -> str | None:
    require_repository_owner(identity)
    if phase.branch_name is None:
        return None
    remote = await run_command(
        ("git", "ls-remote", "--heads", identity.remote_url, phase.branch_name),
        github_credentials=True,
    )
    remote_line = remote.stdout.strip()
    if remote_line:
        return remote_line.split()[0]
    if cache_path is None:
        return None
    try:
        local = await run_command(
            (
                "git",
                "--git-dir",
                cache_path,
                "rev-parse",
                f"refs/heads/{phase.branch_name}",
            )
        )
    except CommandError:
        return None
    commit = local.stdout.strip()
    if not commit or commit == phase.source_sha or phase.worktree_path is None:
        return None
    await reset_and_verify_origin(
        Path(phase.worktree_path),
        identity,
        identity.remote_url,
    )
    await run_command(
        ("git", "push", "--set-upstream", "origin", phase.branch_name),
        cwd=Path(phase.worktree_path),
        github_credentials=True,
    )
    return commit


async def _record_recovered_pr(
    run_id: str,
    phase_id: str,
    commit: str,
    pr: dict[str, Any],
) -> None:
    number = pr.get("number")
    url = pr.get("url") or pr.get("html_url")
    if not isinstance(number, int) or not isinstance(url, str):
        raise ReconciliationError("Recovered pull request metadata is incomplete")
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        phase = await session.get(Phase, phase_id)
        if phase is None:
            raise ReconciliationError("Interrupted phase no longer exists")
        phase.commit_sha = commit
        phase.pr_number = number
        phase.pr_url = url
        phase.status = PhaseState.WAITING_FOR_MERGE
        await session.commit()
        if run.state == RunState.EXECUTING_PHASE:
            run = await transition_run(
                session,
                run.id,
                RunState.PR_OPEN,
                expected_version=run.version,
                event_type="pull_request.recovered",
                payload={"phase_id": phase.id, "number": number, "url": url},
            )
        elif run.state == RunState.FAILED:
            run.failure_code = None
            run.failure_message = None
            await session.commit()
            run = await transition_run(
                session,
                run.id,
                RunState.WAITING_FOR_MERGE,
                expected_version=run.version,
                event_type="pull_request.recovered",
                payload={"phase_id": phase.id, "number": number, "url": url},
            )
        phase = await session.get(Phase, phase_id)
        if phase is not None:
            phase.status = PhaseState.WAITING_FOR_MERGE
            await session.commit()
        if run.state == RunState.PR_OPEN:
            await transition_run(
                session,
                run.id,
                RunState.WAITING_FOR_MERGE,
                expected_version=run.version,
                event_type="pull_request.waiting_for_merge",
                payload={"phase_id": phase_id, "number": number},
            )
        await session.execute(delete(RepositoryLock).where(RepositoryLock.run_id == run_id))
        await session.commit()


async def recover_phase_pull_request(run_id: str, phase_id: str) -> bool:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        phase = await session.get(Phase, phase_id)
        if phase is None or phase.run_id != run.id or phase.branch_name is None:
            return False
        identity = RepositoryIdentity(run.repository.owner, run.repository.name)
        require_repository_owner(identity)
        cache_path = run.repository.cache_path
    pr = await pull_request_for_branch(identity, phase.branch_name)
    if pr is None:
        return False
    commit = await _branch_commit(identity, phase, cache_path)
    if commit is None:
        return False
    await _record_recovered_pr(run_id, phase_id, commit, pr)
    return True


async def _mark_interrupted_run_failed(run_id: str, message: str) -> None:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        phase = await session.scalar(
            select(Phase).where(
                Phase.run_id == run.id,
                Phase.status.in_({PhaseState.EXECUTING, PhaseState.WAITING_FOR_MERGE}),
            )
        )
        if phase is not None:
            phase.status = PhaseState.FAILED
        run.failure_code = (
            "pull_request_review_post_failed"
            if run.state == RunState.POSTING_PR_REVIEW
            else "interrupted"
        )
        run.failure_message = message
        await session.commit()
        if run.state in {
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
            RunState.GROUNDING_PR_REVIEW,
            RunState.REVIEWING_PR,
            RunState.CONSOLIDATING_PR_REVIEW,
            RunState.POSTING_PR_REVIEW,
        }:
            await transition_run(
                session,
                run.id,
                RunState.FAILED,
                expected_version=run.version,
                event_type="run.interrupted",
                payload={"message": message},
            )
        await session.execute(delete(RepositoryLock).where(RepositoryLock.run_id == run_id))
        await session.commit()


async def recover_interrupted_runs() -> None:
    recoverable_states = {
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
        RunState.GROUNDING_PR_REVIEW,
        RunState.REVIEWING_PR,
        RunState.CONSOLIDATING_PR_REVIEW,
        RunState.POSTING_PR_REVIEW,
    }
    async with SessionFactory() as session:
        interrupted_at = datetime.now(UTC)
        running_operations = list(
            await session.scalars(select(Operation).where(Operation.status == "running"))
        )
        for operation in running_operations:
            operation.status = "failed"
            operation.completed_at = interrupted_at
            operation.heartbeat_at = interrupted_at
            operation.progress_at = interrupted_at
            operation.error = {
                "type": "ProcessInterrupted",
                "message": "The backend process stopped before this operation completed.",
            }
        await session.commit()
        run_ids = list(
            await session.scalars(select(Run.id).where(Run.state.in_(recoverable_states)))
        )
    for run_id in run_ids:
        try:
            async with SessionFactory() as session:
                run = await get_run(session, run_id)
                phase = await session.scalar(
                    select(Phase).where(
                        Phase.run_id == run.id,
                        Phase.status.in_({PhaseState.EXECUTING, PhaseState.WAITING_FOR_MERGE}),
                    )
                )
                identity = RepositoryIdentity(run.repository.owner, run.repository.name)
                require_repository_owner(identity)
                cache_path = run.repository.cache_path
                default_branch = run.repository.default_branch
            if phase is None or phase.branch_name is None:
                await _mark_interrupted_run_failed(
                    run_id,
                    "The process stopped during model work. Start the workflow to retry.",
                )
                continue
            pr = await pull_request_for_branch(identity, phase.branch_name)
            commit = await _branch_commit(identity, phase, cache_path)
            if pr is None and commit is not None:
                if default_branch is None:
                    raise ReconciliationError("Repository default branch is unknown")
                url = await _create_recovery_pr(
                    identity,
                    phase,
                    default_branch,
                    commit,
                )
                pr = await pull_request_for_branch(identity, phase.branch_name)
                if pr is None:
                    raise ReconciliationError(f"Created {url} but could not retrieve it")
            if pr is not None and commit is not None:
                await _record_recovered_pr(run_id, phase.id, commit, pr)
            else:
                await _mark_interrupted_run_failed(
                    run_id,
                    "The process stopped before a validated phase branch was pushed. "
                    "Start the workflow to retry the phase.",
                )
        except Exception:
            logger.exception("Failed to recover interrupted run %s", run_id)
    async with SessionFactory() as session:
        await session.execute(delete(RepositoryLock))
        await session.commit()


def _touches_planned_files(changed_files: set[str], phases: list[Phase]) -> bool:
    likely_files = {
        path.rstrip("/*")
        for phase in phases
        for path in phase.details.get("likely_files", [])
        if isinstance(path, str) and path.strip()
    }
    return any(
        changed == planned or changed.startswith(f"{planned}/") or planned.startswith(f"{changed}/")
        for changed in changed_files
        for planned in likely_files
    )


async def reconcile_run(run_id: str) -> dict[str, Any]:
    async with _reconciliation_lock(run_id):
        return await _reconcile_run(run_id)


async def _reconcile_run(run_id: str) -> dict[str, Any]:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state != RunState.WAITING_FOR_MERGE:
            return {"changed": False, "state": run.state.value}
        phase = await session.scalar(
            select(Phase).where(
                Phase.run_id == run.id,
                Phase.status == PhaseState.WAITING_FOR_MERGE,
            )
        )
        if phase is None or phase.pr_number is None:
            raise ReconciliationError("Waiting run has no active pull request")
        identity = RepositoryIdentity(run.repository.owner, run.repository.name)
        require_repository_owner(identity)
        pr = await get_pr(identity, phase.pr_number)
        if not bool(pr.get("merged")):
            return {
                "changed": False,
                "state": run.state.value,
                "pr_number": phase.pr_number,
                "pr_state": pr.get("state"),
            }
        merge_sha = pr.get("merge_commit_sha")
        if not isinstance(merge_sha, str):
            raise ReconciliationError("Merged pull request has no merge commit SHA")
        phase_id = phase.id
        expected_source_sha = phase.source_sha

    workspaces = WorkspaceService()
    metadata, cache, default_sha = await workspaces.refresh(identity)
    parent_result = await run_command(
        ("git", "--git-dir", str(cache), "rev-list", "--parents", "-n", "1", merge_sha)
    )
    parents = parent_result.stdout.strip().split()
    first_parent = parents[1] if len(parents) > 1 else expected_source_sha
    external_changes: set[str] = set()
    if first_parent != expected_source_sha:
        diff = await run_command(
            (
                "git",
                "--git-dir",
                str(cache),
                "diff",
                "--name-only",
                expected_source_sha,
                first_parent,
            )
        )
        external_changes = {line for line in diff.stdout.splitlines() if line}

    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        phase = await session.get(Phase, phase_id)
        if phase is None:
            raise ReconciliationError("Active phase disappeared during reconciliation")
        phase.status = PhaseState.MERGED
        phase.merge_sha = merge_sha
        remaining = list(
            await session.scalars(
                select(Phase)
                .where(Phase.run_id == run.id, Phase.status == PhaseState.PENDING)
                .order_by(Phase.ordinal)
            )
        )
        material_drift = bool(external_changes) and _touches_planned_files(external_changes, remaining)
        pending_action = None
        if material_drift:
            next_state = RunState.REGROUNDING
            event_type = "source.material_drift_detected"
        elif remaining:
            next_phase = remaining[0]
            next_phase.status = PhaseState.READY
            next_phase.source_sha = default_sha
            next_state = RunState.READY_FOR_PHASE
            event_type = "phase.ready"
            pending_action = await phase_pending_action(
                run, next_phase, expected_run_version=run.version + 1
            )
        else:
            next_state = RunState.COMPLETED
            event_type = "run.completed"
        transition_payload: dict[str, object] = {
            "merged_phase_id": phase.id,
            "merge_sha": merge_sha,
            "default_branch": metadata.default_branch,
            "default_sha": default_sha,
            "external_changes": sorted(external_changes),
        }
        if pending_action is None:
            await session.commit()
            run = await transition_run(
                session,
                run.id,
                next_state,
                expected_version=run.version,
                event_type=event_type,
                payload=transition_payload,
            )
        else:
            run = await transition_with_pending_action(
                session,
                run.id,
                next_state,
                expected_version=run.version,
                event_type=event_type,
                pending=PendingActionSpec(
                    kind=pending_action.kind,
                    phase_id=pending_action.phase_id,
                    payload=pending_action.payload,
                ),
                payload=transition_payload,
            )
        worktree_path = phase.worktree_path
        cache_path = run.repository.cache_path

    if worktree_path and cache_path:
        await workspaces.remove_worktree(Path(cache_path), Path(worktree_path))
    return {
        "changed": True,
        "state": run.state.value,
        "merge_sha": merge_sha,
        "material_drift": material_drift,
    }


async def monitor_merges(stop: asyncio.Event, poll_seconds: float) -> None:
    while not stop.is_set():
        async with SessionFactory() as session:
            run_ids = list(
                await session.scalars(
                    select(Run.id).where(Run.state == RunState.WAITING_FOR_MERGE)
                )
            )
        for run_id in run_ids:
            try:
                await reconcile_run(run_id)
            except Exception:
                logger.exception("Failed to reconcile waiting run %s", run_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            continue

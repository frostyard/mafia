import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from mafia.db.models import Evidence, Repository, Run, SourceSnapshot
from mafia.services.github import get_issue, get_pull_request_context
from mafia.services.repositories import RepositoryIdentity
from mafia.services.workspaces import WorkspaceService
from sqlalchemy.ext.asyncio import AsyncSession

INSTRUCTION_NAMES = {"AGENTS.md", "copilot-instructions.md"}
MANIFEST_NAMES = {
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
}
SKIP_DIRECTORIES = {".git", "node_modules", ".venv", "dist", "build", "vendor"}


class SourcePathError(ValueError):
    pass


def resolve_in_root(root: Path, relative_path: str) -> Path:
    if not relative_path or "\x00" in relative_path:
        raise SourcePathError("A non-empty relative path is required")
    root = root.resolve()
    candidate = root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as error:
        raise SourcePathError(f"Source path does not exist: {relative_path}") from error
    if resolved != root and root not in resolved.parents:
        raise SourcePathError("Source path escapes the repository root")
    current = candidate
    while current != root:
        if current.is_symlink():
            target = current.resolve()
            if target != root and root not in target.parents:
                raise SourcePathError("Symlink escapes the repository root")
        current = current.parent
    return resolved


@dataclass
class SourceActivity:
    listed_paths: set[str] = field(default_factory=set[str])
    read_paths: set[str] = field(default_factory=set[str])
    search_queries: list[str] = field(default_factory=list[str])
    search_matches: int = 0

    def snapshot(self) -> dict[str, Any]:
        inspected = sorted(self.listed_paths | self.read_paths)
        return {
            "files_inspected": inspected[:200],
            "files_inspected_count": len(inspected),
            "searches": len(self.search_queries),
            "search_matches": self.search_matches,
            "recent_searches": self.search_queries[-10:],
        }


@dataclass(frozen=True)
class SourceReader:
    root: Path
    activity: SourceActivity = field(default_factory=SourceActivity)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    def list_source(self, relative_path: str = ".") -> list[str]:
        self.activity.listed_paths.add(relative_path)
        directory = resolve_in_root(self.root, relative_path)
        if not directory.is_dir():
            raise SourcePathError(f"Not a directory: {relative_path}")
        return sorted(
            str(path.relative_to(self.root))
            for path in directory.iterdir()
            if path.name not in SKIP_DIRECTORIES
        )

    def read_source(self, relative_path: str, line_start: int = 1, line_end: int = 400) -> str:
        self.activity.read_paths.add(relative_path)
        path = resolve_in_root(self.root, relative_path)
        if not path.is_file():
            raise SourcePathError(f"Not a file: {relative_path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 1_000:
            raise SourcePathError("Invalid line range")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(
            f"{index}: {line}" for index, line in enumerate(lines[line_start - 1 : line_end], line_start)
        )

    def search_source(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        if not query or len(query) > 500 or limit < 1 or limit > 500:
            raise SourcePathError("Invalid search")
        self.activity.search_queries.append(query[:120])
        matches: list[dict[str, Any]] = []
        needle = query.casefold()
        for directory, names, files in os.walk(self.root, followlinks=False):
            names[:] = [name for name in names if name not in SKIP_DIRECTORIES]
            for file_name in files:
                path = Path(directory) / file_name
                try:
                    resolved = resolve_in_root(self.root, str(path.relative_to(self.root)))
                    for number, line in enumerate(
                        resolved.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if needle in line.casefold():
                            matches.append(
                                {
                                    "path": str(path.relative_to(self.root)),
                                    "line": number,
                                    "text": line[:500],
                                }
                            )
                            if len(matches) >= limit:
                                self.activity.search_matches += len(matches)
                                return matches
                except (OSError, SourcePathError):
                    continue
        self.activity.search_matches += len(matches)
        return matches


def discover_source(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest: dict[str, Any] = {"files": [], "manifests": []}
    instructions: list[dict[str, Any]] = []
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name not in SKIP_DIRECTORIES]
        for file_name in files:
            path = Path(directory) / file_name
            if path.is_symlink():
                continue
            relative = str(path.relative_to(root))
            manifest["files"].append(relative)
            if file_name in MANIFEST_NAMES:
                manifest["manifests"].append(relative)
            if file_name == "AGENTS.md" or relative == ".github/copilot-instructions.md":
                content = path.read_text(encoding="utf-8", errors="replace")
                instructions.append({"path": relative, "content": content[:100_000]})
    manifest["files"].sort()
    manifest["manifests"].sort()
    instructions.sort(key=lambda item: str(item["path"]))
    return manifest, instructions


async def capture_source_snapshot(
    session: AsyncSession,
    run: Run,
    repository: Repository,
    *,
    reason: str,
    workspaces: WorkspaceService | None = None,
) -> SourceSnapshot:
    identity = RepositoryIdentity(repository.owner, repository.name)
    workspace_service = workspaces or WorkspaceService()
    metadata, cache, sha = await workspace_service.refresh(identity)
    path = await workspace_service.create_analysis_worktree(identity, cache, sha, f"{run.id}-{reason}-{sha}")
    issue_data = await get_issue(identity, run.issue_number) if run.issue_number is not None else None
    manifest, instructions = discover_source(path)
    repository.default_branch = metadata.default_branch
    repository.cache_path = str(cache)
    repository.last_fetched_sha = sha
    snapshot = SourceSnapshot(
        run_id=run.id,
        git_sha=sha,
        reason=reason,
        issue_data=issue_data,
        manifest=manifest,
        instructions=instructions,
        worktree_path=str(path),
    )
    session.add(snapshot)
    await session.flush()
    for instruction in instructions:
        content = str(instruction["content"])
        session.add(
            Evidence(
                snapshot_id=snapshot.id,
                kind="instruction",
                path_or_url=str(instruction["path"]),
                excerpt_hash=hashlib.sha256(content.encode()).hexdigest(),
                detail={"size": len(content)},
            )
        )
    await session.commit()
    return snapshot


async def capture_pull_request_snapshot(
    session: AsyncSession,
    run: Run,
    repository: Repository,
    *,
    workspaces: WorkspaceService | None = None,
) -> tuple[SourceSnapshot, dict[str, Any]]:
    if run.pull_request_number is None:
        raise ValueError("Pull-request review run has no pull request number")
    identity = RepositoryIdentity(repository.owner, repository.name)
    context = await get_pull_request_context(identity, run.pull_request_number)
    base_value = context.get("base")
    head_value = context.get("head")
    if not isinstance(base_value, dict) or not isinstance(head_value, dict):
        raise ValueError("Pull-request metadata has no base or head")
    base = cast(dict[str, Any], base_value)
    head = cast(dict[str, Any], head_value)
    base_ref_value = base.get("ref")
    base_sha_value = base.get("sha")
    head_sha_value = head.get("sha")
    if (
        not isinstance(base_ref_value, str)
        or not base_ref_value
        or not isinstance(base_sha_value, str)
        or not base_sha_value
        or not isinstance(head_sha_value, str)
        or not head_sha_value
    ):
        raise ValueError("Pull-request base or head metadata is incomplete")
    base_ref = base_ref_value
    base_sha = base_sha_value
    head_sha = head_sha_value
    workspace_service = workspaces or WorkspaceService()
    metadata, cache, resolved_base, resolved_head = (
        await workspace_service.refresh_pull_request(
            identity,
            run.pull_request_number,
            base_ref=base_ref,
            expected_base_sha=base_sha,
            expected_head_sha=head_sha,
        )
    )
    path = await workspace_service.create_analysis_worktree(
        identity,
        cache,
        resolved_head,
        f"{run.id}-pr-{run.pull_request_number}-{resolved_head}",
    )
    manifest, instructions = discover_source(path)
    repository.default_branch = metadata.default_branch
    repository.cache_path = str(cache)
    repository.last_fetched_sha = resolved_head
    context["base_sha"] = resolved_base
    context["head_sha"] = resolved_head
    snapshot = SourceSnapshot(
        run_id=run.id,
        git_sha=resolved_head,
        reason=f"pr-review-{run.pull_request_number}",
        issue_data=context,
        manifest=manifest,
        instructions=instructions,
        worktree_path=str(path),
    )
    session.add(snapshot)
    await session.flush()
    for instruction in instructions:
        content = str(instruction["content"])
        session.add(
            Evidence(
                snapshot_id=snapshot.id,
                kind="instruction",
                path_or_url=str(instruction["path"]),
                excerpt_hash=hashlib.sha256(content.encode()).hexdigest(),
                detail={"size": len(content)},
            )
        )
    session.add(
        Evidence(
            snapshot_id=snapshot.id,
            kind="pull_request",
            path_or_url=str(context.get("html_url") or f"{identity.slug}#{run.pull_request_number}"),
            excerpt_hash=hashlib.sha256(
                f"{run.pull_request_number}:{resolved_head}".encode()
            ).hexdigest(),
            detail={
                "number": run.pull_request_number,
                "base_sha": resolved_base,
                "head_sha": resolved_head,
                "changed_files": context.get("changed_files"),
            },
        )
    )
    await session.commit()
    return snapshot, context

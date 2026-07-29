import asyncio
import hashlib
import shutil
from pathlib import Path

from mafia.config import Settings, get_settings
from mafia.services.commands import run_command
from mafia.services.github import RepositoryMetadata, get_repository_metadata
from mafia.services.repositories import (
    RepositoryIdentity,
    parse_repository,
    require_repository_owner,
)

_locks: dict[str, asyncio.Lock] = {}


class WorkspaceError(RuntimeError):
    pass


async def reset_and_verify_origin(
    worktree: Path,
    identity: RepositoryIdentity,
    remote_url: str,
) -> None:
    require_repository_owner(identity)
    await run_command(
        (
            "git",
            "-C",
            str(worktree),
            "remote",
            "set-url",
            "origin",
            remote_url,
        )
    )
    configured_url = (
        await run_command(
            ("git", "-C", str(worktree), "remote", "get-url", "origin")
        )
    ).stdout.strip()
    if (
        parse_repository(configured_url).slug.casefold()
        != identity.slug.casefold()
    ):
        raise WorkspaceError(
            "The worktree origin does not match the authorized repository"
        )


def _lock(identity: RepositoryIdentity) -> asyncio.Lock:
    return _locks.setdefault(identity.slug.casefold(), asyncio.Lock())


class WorkspaceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def cache_path(self, identity: RepositoryIdentity) -> Path:
        require_repository_owner(identity, self.settings)
        return self.settings.repositories_dir / identity.owner / f"{identity.name}.git"

    def worktree_path(self, identity: RepositoryIdentity, label: str) -> Path:
        require_repository_owner(identity, self.settings)
        digest = hashlib.sha256(f"{identity.slug}:{label}".encode()).hexdigest()[:12]
        return self.settings.worktrees_dir / identity.owner / identity.name / digest

    async def refresh(self, identity: RepositoryIdentity) -> tuple[RepositoryMetadata, Path, str]:
        require_repository_owner(identity, self.settings)
        async with _lock(identity):
            metadata = await get_repository_metadata(identity)
            cache = self.cache_path(identity)
            cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not cache.exists():
                await run_command(
                    ("git", "clone", "--bare", metadata.clone_url, str(cache)),
                    github_credentials=True,
                )
            else:
                await run_command(
                    ("git", "--git-dir", str(cache), "remote", "set-url", "origin", metadata.clone_url)
                )
            await run_command(
                (
                    "git",
                    "--git-dir",
                    str(cache),
                    "fetch",
                    "--prune",
                    "origin",
                    f"+refs/heads/{metadata.default_branch}:refs/remotes/origin/{metadata.default_branch}",
                ),
                github_credentials=True,
            )
            result = await run_command(
                (
                    "git",
                    "--git-dir",
                    str(cache),
                    "rev-parse",
                    f"refs/remotes/origin/{metadata.default_branch}",
                )
            )
            return metadata, cache, result.stdout.strip()

    async def refresh_pull_request(
        self,
        identity: RepositoryIdentity,
        number: int,
        *,
        base_ref: str,
        expected_base_sha: str,
        expected_head_sha: str,
    ) -> tuple[RepositoryMetadata, Path, str, str]:
        require_repository_owner(identity, self.settings)
        async with _lock(identity):
            metadata = await get_repository_metadata(identity)
            cache = self.cache_path(identity)
            cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not cache.exists():
                await run_command(
                    ("git", "clone", "--bare", metadata.clone_url, str(cache)),
                    github_credentials=True,
                )
            else:
                await run_command(
                    (
                        "git",
                        "--git-dir",
                        str(cache),
                        "remote",
                        "set-url",
                        "origin",
                        metadata.clone_url,
                    )
                )
            await run_command(
                (
                    "git",
                    "--git-dir",
                    str(cache),
                    "fetch",
                    "--prune",
                    "origin",
                    f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}",
                    f"+refs/pull/{number}/head:refs/remotes/pull/{number}/head",
                ),
                github_credentials=True,
            )
            base = (
                await run_command(
                    (
                        "git",
                        "--git-dir",
                        str(cache),
                        "rev-parse",
                        f"refs/remotes/origin/{base_ref}",
                    )
                )
            ).stdout.strip()
            head = (
                await run_command(
                    (
                        "git",
                        "--git-dir",
                        str(cache),
                        "rev-parse",
                        f"refs/remotes/pull/{number}/head",
                    )
                )
            ).stdout.strip()
            if base != expected_base_sha or head != expected_head_sha:
                raise WorkspaceError(
                    "The pull request changed while its source was being prepared; retry the review"
                )
            return metadata, cache, base, head

    async def create_analysis_worktree(
        self,
        identity: RepositoryIdentity,
        cache: Path,
        sha: str,
        label: str,
    ) -> Path:
        require_repository_owner(identity, self.settings)
        path = self.worktree_path(identity, label)
        async with _lock(identity):
            if path.exists():
                result = await run_command(("git", "-C", str(path), "rev-parse", "HEAD"), check=False)
                if result.returncode == 0 and result.stdout.strip() == sha:
                    return path
                raise WorkspaceError(f"Existing worktree {path} is not at expected source revision")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            await run_command(("git", "--git-dir", str(cache), "worktree", "add", "--detach", str(path), sha))
            return path

    async def create_phase_worktree(
        self,
        identity: RepositoryIdentity,
        cache: Path,
        base_sha: str,
        branch_name: str,
    ) -> Path:
        require_repository_owner(identity, self.settings)
        path = self.worktree_path(identity, branch_name)
        async with _lock(identity):
            if path.exists():
                branch = await run_command(
                    ("git", "-C", str(path), "branch", "--show-current"),
                    check=False,
                )
                ancestor = await run_command(
                    ("git", "-C", str(path), "merge-base", "--is-ancestor", base_sha, "HEAD"),
                    check=False,
                )
                if branch.stdout.strip() == branch_name and ancestor.returncode == 0:
                    return path
                raise WorkspaceError(f"Existing phase worktree is not the expected branch: {path}")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            branch = await run_command(
                ("git", "--git-dir", str(cache), "rev-parse", "--verify", f"refs/heads/{branch_name}"),
                check=False,
            )
            if branch.returncode == 0:
                ancestor = await run_command(
                    (
                        "git",
                        "--git-dir",
                        str(cache),
                        "merge-base",
                        "--is-ancestor",
                        base_sha,
                        branch.stdout.strip(),
                    ),
                    check=False,
                )
                if ancestor.returncode != 0:
                    raise WorkspaceError(f"Existing phase branch is not based on {base_sha}")
                await run_command(
                    ("git", "--git-dir", str(cache), "worktree", "add", str(path), branch_name)
                )
                return path
            await run_command(
                (
                    "git",
                    "--git-dir",
                    str(cache),
                    "worktree",
                    "add",
                    "-b",
                    branch_name,
                    str(path),
                    base_sha,
                )
            )
            return path

    async def remove_worktree(self, cache: Path, path: Path) -> None:
        resolved_root = self.settings.worktrees_dir.resolve()
        resolved = path.resolve()  # noqa: ASYNC240
        if resolved == resolved_root or resolved_root not in resolved.parents:
            raise WorkspaceError("Refusing to remove a path outside the worktree root")
        await run_command(
            ("git", "--git-dir", str(cache), "worktree", "remove", "--force", str(resolved)),
            check=False,
        )
        if resolved.exists():
            shutil.rmtree(resolved)

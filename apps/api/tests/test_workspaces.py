from pathlib import Path

import pytest
from mafia.config import Settings
from mafia.services.commands import CommandResult
from mafia.services.github import RepositoryMetadata
from mafia.services.repositories import RepositoryIdentity
from mafia.services.workspaces import (
    WorkspaceError,
    WorkspaceService,
    reset_and_verify_origin,
)


@pytest.mark.asyncio
async def test_pull_request_refresh_fetches_exact_head_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []
    base_sha = "a" * 40
    head_sha = "b" * 40

    async def metadata(_: RepositoryIdentity) -> RepositoryMetadata:
        return RepositoryMetadata(
            default_branch="main",
            clone_url="https://github.com/octo/repo.git",
        )

    async def command(
        argv: tuple[str, ...],
        **_: object,
    ) -> CommandResult:
        commands.append(argv)
        stdout = ""
        if argv[-1] == "refs/remotes/origin/main":
            stdout = f"{base_sha}\n"
        elif argv[-1] == "refs/remotes/pull/42/head":
            stdout = f"{head_sha}\n"
        return CommandResult(argv=argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "mafia.services.workspaces.get_repository_metadata",
        metadata,
    )
    monkeypatch.setattr("mafia.services.workspaces.run_command", command)
    service = WorkspaceService(Settings(data_dir=tmp_path))

    _, _, resolved_base, resolved_head = await service.refresh_pull_request(
        RepositoryIdentity("octo", "repo"),
        42,
        base_ref="main",
        expected_base_sha=base_sha,
        expected_head_sha=head_sha,
    )

    fetch = next(argv for argv in commands if "fetch" in argv)
    assert "+refs/pull/42/head:refs/remotes/pull/42/head" in fetch
    assert resolved_base == base_sha
    assert resolved_head == head_sha


@pytest.mark.asyncio
async def test_reset_origin_rejects_remote_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        (
            CommandResult(("git",), 0, "", ""),
            CommandResult(
                ("git",),
                0,
                "https://github.com/attacker/other.git\n",
                "",
            ),
        )
    )

    async def command(*_args: object, **_kwargs: object) -> CommandResult:
        return next(responses)

    monkeypatch.setattr("mafia.services.workspaces.run_command", command)

    with pytest.raises(WorkspaceError, match="does not match"):
        await reset_and_verify_origin(
            tmp_path,
            RepositoryIdentity("octo", "repo"),
            "https://github.com/octo/repo.git",
        )

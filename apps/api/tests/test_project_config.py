import subprocess
from pathlib import Path

import pytest
from mafia.config import Settings
from mafia.services.commands import CommandResult
from mafia.services.project_config import (
    ProjectConfigurationError,
    parse_project_configuration,
    read_host_project_configuration,
    resolve_project_configuration,
    resolve_project_configuration_content,
    source_validation_status,
    write_host_project_configuration,
)
from mafia.services.repositories import RepositoryIdentity

REPOSITORY_CONFIG = """
version = 1

[[validation.commands]]
name = "Checks"
run = "npm run check"
timeout_seconds = 1200
""".strip()


@pytest.fixture
def cache_without_repository_config(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    cache = tmp_path / "cache.git"
    repository.mkdir()
    subprocess.run(("git", "init"), cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ("git", "config", "user.email", "test@example.com"),
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Test"),
        cwd=repository,
        check=True,
    )
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(("git", "add", "README.md"), cwd=repository, check=True)
    subprocess.run(("git", "commit", "-m", "initial"), cwd=repository, check=True)
    source_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ("git", "clone", "--bare", str(repository), str(cache)),
        check=True,
        capture_output=True,
    )
    return cache, source_sha


def test_repository_configuration_cannot_select_host_execution() -> None:
    with pytest.raises(ProjectConfigurationError, match="cannot configure execution"):
        parse_project_configuration(
            'version = 1\n[execution]\nmode = "host"\n',
            source="repository",
        )


def test_repository_validation_wins_over_host_fallback(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    identity = RepositoryIdentity("octo", "repo")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".mafia.toml").write_text(REPOSITORY_CONFIG, encoding="utf-8")
    write_host_project_configuration(
        identity,
        """
version = 1
[execution]
mode = "host"
[[validation.commands]]
name = "Host checks"
run = "make check"
""",
        settings,
    )

    resolved = resolve_project_configuration(identity, worktree, settings)

    assert resolved.execution_mode == "host"
    assert resolved.validation_source == "repository"
    assert [command.run for command in resolved.validation_commands] == ["npm run check"]


def test_host_configuration_is_used_when_repository_file_is_absent(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    identity = RepositoryIdentity("octo", "repo")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    write_host_project_configuration(
        identity,
        """
version = 1
[execution]
mode = "isolated"
[[validation.commands]]
name = "Checks"
run = "make check"
working_directory = "backend"
""",
        settings,
    )

    resolved = resolve_project_configuration(identity, worktree, settings)

    assert resolved.validation_source == "host"
    assert resolved.validation_commands[0].working_directory == "backend"
    _, persisted, configured = read_host_project_configuration(identity, settings)
    assert configured is True
    assert "make check" in persisted


def test_explicit_repository_content_is_resolved_without_reading_worktree(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    identity = RepositoryIdentity("octo", "repo")
    resolved = resolve_project_configuration_content(
        identity,
        REPOSITORY_CONFIG,
        settings,
    )

    assert resolved.validation_source == "repository"
    assert resolved.validation_commands[0].run == "npm run check"


def test_default_host_configuration_has_no_validation(tmp_path: Path) -> None:
    configuration, content, configured = read_host_project_configuration(
        RepositoryIdentity("octo", "repo"),
        Settings(data_dir=tmp_path),
    )

    assert configuration.execution.mode == "isolated"
    assert configuration.validation is None
    assert configured is False
    assert '[execution]\nmode = "isolated"' in content


@pytest.mark.asyncio
async def test_source_validation_uses_host_when_config_exists_only_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_without_repository_config: tuple[Path, str],
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    identity = RepositoryIdentity("octo", "repo")
    cache, source_sha = cache_without_repository_config
    write_host_project_configuration(identity, REPOSITORY_CONFIG, settings)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mafia.toml").write_text(REPOSITORY_CONFIG, encoding="utf-8")

    available, source = await source_validation_status(
        identity,
        str(cache),
        source_sha,
        settings,
    )

    assert available is True
    assert source == "host"


@pytest.mark.asyncio
async def test_source_validation_rejects_an_invalid_commit_without_parsing_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mafia.services import project_config

    async def invalid_commit(command: tuple[str, ...], *, check: bool = True) -> CommandResult:
        del check
        assert command[-1].endswith("^{commit}")
        return CommandResult(command, 1, "", "提交不存在")

    monkeypatch.setattr(project_config, "run_command", invalid_commit)
    with pytest.raises(ProjectConfigurationError, match="Could not read repository commit"):
        await source_validation_status(
            RepositoryIdentity("octo", "repo"), str(tmp_path / "cache.git"), "bad-sha"
        )

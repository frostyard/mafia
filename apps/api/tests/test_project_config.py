from pathlib import Path

import pytest
from mafia.config import Settings
from mafia.services.project_config import (
    ProjectConfigurationError,
    parse_project_configuration,
    read_host_project_configuration,
    resolve_project_configuration,
    resolve_project_configuration_content,
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
    assert [command.run for command in resolved.validation_commands] == [
        "npm run check"
    ]


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

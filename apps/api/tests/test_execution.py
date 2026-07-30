import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mafia.domain.enums import PhaseState, RunState
from mafia.services import execution
from mafia.services.execution import PhaseExecutionError, PhaseNotReadyError, validate_worktree_diff


def initialize_repository(path: Path) -> None:
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "Test User"), check=True)
    subprocess.run(
        ("git", "-C", str(path), "config", "user.email", "test@example.invalid"),
        check=True,
    )
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", "README.md"), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-q", "-m", "fixture"), check=True)


@pytest.mark.asyncio
async def test_rejects_changed_symlink_that_escapes_worktree(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "escape").symlink_to("/etc/passwd")

    with pytest.raises(PhaseExecutionError, match="absolute target"):
        await validate_worktree_diff(tmp_path)


@pytest.mark.asyncio
async def test_accepts_changed_internal_symlink(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "target.txt").write_text("safe\n", encoding="utf-8")
    (tmp_path / "link").symlink_to("target.txt")

    assert await validate_worktree_diff(tmp_path) == ["link", "target.txt"]


@pytest.mark.asyncio
async def test_execute_phase_raises_typed_error_when_phase_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        execution,
        "_phase_with_run",
        AsyncMock(
            return_value=(
                SimpleNamespace(state=RunState.INTAKE),
                SimpleNamespace(status=PhaseState.READY),
            )
        ),
    )

    with pytest.raises(PhaseNotReadyError):
        await execution._execute_phase(  # pyright: ignore[reportPrivateUsage]
            "run-1", "phase-1", AsyncMock()
        )

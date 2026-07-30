from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mafia.services.commands import CommandResult
from mafia.services.sandbox import SandboxResult
from mafia.workflows import run_workflow


class FailingEnvironment:
    kind = "test"

    def activity_snapshot(self) -> dict[str, object]:
        return {}

    async def run(
        self, command: str, *, timeout_seconds: float | None = None
    ) -> SandboxResult:
        raise NotImplementedError

    def read_file(self, path: str, line_start: int = 1, line_end: int = 500) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> str:
        raise NotImplementedError

    async def tool_run(
        self, command: str, timeout_seconds: int = 120
    ) -> dict[str, object]:
        raise NotImplementedError

    async def close(self) -> None:
        raise RuntimeError("container removal failed")

    def description(self) -> dict[str, object]:
        return {"environment": "test"}


@pytest.mark.asyncio
async def test_analysis_worktree_is_restored_when_environment_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = AsyncMock(
        side_effect=[
            CommandResult(("git",), 0, "", ""),
            CommandResult(("git",), 0, "", ""),
        ]
    )
    monkeypatch.setattr(run_workflow, "run_command", run)

    with pytest.raises(RuntimeError, match="container removal failed"):
        await run_workflow.restore_analysis_worktree(
            FailingEnvironment(),
            tmp_path,
            "a" * 40,
        )

    assert run.await_args_list[0].args[0][-2:] == ("--hard", "a" * 40)
    assert run.await_args_list[1].args[0][-2:] == ("clean", "-fdx")

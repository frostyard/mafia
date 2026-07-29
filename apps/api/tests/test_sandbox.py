import shlex
import shutil
from pathlib import Path

import pytest
from mafia.services.sandbox import BubblewrapSandbox, HostExecutionEnvironment


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is unavailable")
async def test_sandbox_confines_home_and_writes_workspace(tmp_path: Path) -> None:
    sandbox = BubblewrapSandbox(tmp_path)
    host_ssh_key = shlex.quote(str(Path.home() / ".ssh" / "id_rsa"))
    result = await sandbox.run(
        "printf changed > result.txt; "
        f"test ! -e {host_ssh_key}; "
        "test ! -e /etc/passwd; "
        "printf ':isolated'"
    )
    assert result.returncode == 0
    assert result.stdout == ":isolated"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "changed"


@pytest.mark.asyncio
async def test_host_environment_runs_directly_in_worktree(tmp_path: Path) -> None:
    environment = HostExecutionEnvironment(tmp_path)

    result = await environment.run(
        "test -r /etc/passwd; printf '%s' \"$PWD\"; printf changed > result.txt"
    )

    assert result.returncode == 0
    assert result.stdout == str(tmp_path)
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "changed"
    assert environment.description() == {
        "environment": "host",
        "network": "host",
        "worktree": str(tmp_path),
        "isolation": "disabled",
    }

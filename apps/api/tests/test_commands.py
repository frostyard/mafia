import asyncio
from pathlib import Path

import pytest
from mafia.config import Settings
from mafia.services import commands
from mafia.services.commands import run_command


def process_is_running(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat[stat.rfind(")") + 2 :].split()[0] != "Z"


@pytest.mark.asyncio
async def test_cancellation_kills_command_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    task = asyncio.create_task(
        run_command(
            (
                "/bin/sh",
                "-c",
                f"sleep 60 & echo $! > {pid_file}; wait",
            ),
            timeout_seconds=120,
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text(encoding="utf-8"))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process_is_running(child_pid) is False


@pytest.mark.asyncio
async def test_github_app_credentials_only_reach_git_and_gh_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "git"
    executable.write_text(
        "#!/bin/sh\n"
        'test "$GH_TOKEN" = "installation-token"\n'
        'test "$GIT_CONFIG_VALUE_0" = "!gh auth git-credential"\n'
        'test "$GIT_CONFIG_VALUE_1" = "/dev/null"\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    settings = Settings(
        repository_owner="frostyard",
        github_app_id=123,
        github_app_installation_id=456,
        github_app_private_key_path=tmp_path / "app.pem",
    )
    monkeypatch.setattr(commands, "get_settings", lambda: settings)

    async def token() -> str:
        return "installation-token"

    monkeypatch.setattr("mafia.services.github_app.github_app_token", token)

    result = await run_command((str(executable),), github_credentials=True)
    local_result = await run_command((str(executable),), check=False)

    assert result.returncode == 0
    assert local_result.returncode != 0

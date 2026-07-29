import asyncio
from pathlib import Path

import pytest
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

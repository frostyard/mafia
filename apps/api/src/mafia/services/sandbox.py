import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mafia.config import get_settings
from mafia.services.commands import (
    CommandTimeoutError,
    OutputLimitError,
    communicate_limited,
    kill_process_tree,
    run_command,
)
from mafia.services.source import SourcePathError, resolve_in_root


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


class ExecutionEnvironment(Protocol):
    kind: str

    def activity_snapshot(self) -> dict[str, object]: ...

    async def run(
        self, command: str, *, timeout_seconds: float | None = None
    ) -> SandboxResult: ...

    def read_file(self, path: str, line_start: int = 1, line_end: int = 500) -> str: ...

    def write_file(self, path: str, content: str) -> str: ...

    async def tool_run(
        self, command: str, timeout_seconds: int = 120
    ) -> dict[str, object]: ...

    async def close(self) -> None: ...

    def description(self) -> dict[str, object]: ...


def _process_limit(additional_processes: int) -> int:
    uid_prefix = f"Uid:\t{os.getuid()}\t"
    current = 0
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            if uid_prefix in status.read_text(encoding="utf-8", errors="replace"):
                current += sum(1 for _ in status.parent.joinpath("task").iterdir())
        except OSError:
            continue
    return current + additional_processes


class BubblewrapSandbox:
    kind = "bubblewrap"

    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree.resolve(strict=True)
        self.settings = get_settings()
        self.commands: list[dict[str, Any]] = []
        self.files_read: set[str] = set()
        self.files_written: set[str] = set()

    def activity_snapshot(self) -> dict[str, object]:
        return {
            "execution_environment": self.kind,
            "sandbox_commands": self.commands[-50:],
            "sandbox_command_count": len(self.commands),
            "files_read": sorted(self.files_read)[:200],
            "files_written": sorted(self.files_written)[:200],
        }

    def description(self) -> dict[str, object]:
        return {
            "environment": self.kind,
            "network": "disabled",
            "worktree": str(self.worktree),
        }

    def _argv(self, command: str) -> list[str]:
        argv = [
            "prlimit",
            f"--nproc={_process_limit(self.settings.sandbox_process_limit)}",
            "--nofile=1024",
            "--",
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-net",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home",
            "--dir",
            "/home/agent",
            "--bind",
            str(self.worktree),
            "/workspace",
            "--chdir",
            "/workspace",
            "--setenv",
            "HOME",
            "/home/agent",
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
        ]
        argv.extend(("--ro-bind", "/usr", "/usr"))
        for source in ("/bin", "/lib", "/lib64"):
            path = Path(source)
            if path.is_symlink():
                argv.extend(("--symlink", os.readlink(source), source))
            elif path.exists():
                argv.extend(("--ro-bind", source, source))
        argv.extend(("--dir", "/etc"))
        argv.extend(("/bin/bash", "--noprofile", "--norc", "-lc", command))
        return argv

    async def run(self, command: str, *, timeout_seconds: float | None = None) -> SandboxResult:
        if not command.strip() or "\x00" in command:
            raise SandboxError("Command must be non-empty")
        command_activity: dict[str, Any] = {
            "command": command[:1_000],
            "started": True,
        }
        self.commands.append(command_activity)
        process = await asyncio.create_subprocess_exec(
            *self._argv(command),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                communicate_limited(process, self.settings.command_output_limit),
                timeout=timeout_seconds or self.settings.command_timeout_seconds,
            )
        except TimeoutError as error:
            command_activity.update(status="timed_out")
            await kill_process_tree(process)
            raise SandboxError(f"Sandbox command timed out: {command[:200]}") from error
        except OutputLimitError as error:
            command_activity.update(status="output_limit")
            await kill_process_tree(process)
            raise SandboxError(str(error)) from error
        except BaseException:
            command_activity.update(status="cancelled")
            await kill_process_tree(process)
            raise
        result = SandboxResult(
            command=command,
            returncode=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
        command_activity.update(status="completed", returncode=result.returncode)
        return result

    def read_file(self, path: str, line_start: int = 1, line_end: int = 500) -> str:
        self.files_read.add(path)
        resolved = resolve_in_root(self.worktree, path)
        if not resolved.is_file():
            raise SourcePathError(f"Not a file: {path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 2_000:
            raise SourcePathError("Invalid line range")
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(
            f"{number}: {line}" for number, line in enumerate(lines[line_start - 1 : line_end], line_start)
        )

    def write_file(self, path: str, content: str) -> str:
        self.files_written.add(path)
        if not path or Path(path).is_absolute() or "\x00" in path:
            raise SourcePathError("A repository-relative path is required")
        candidate = self.worktree / path
        parent = candidate.parent.resolve(strict=True)
        if parent != self.worktree and self.worktree not in parent.parents:
            raise SourcePathError("Write path escapes the worktree")
        if candidate.exists() and candidate.is_symlink():
            raise SourcePathError("Refusing to write through a symlink")
        candidate.write_text(content, encoding="utf-8")
        return f"Wrote {len(content.encode())} bytes to {path}"

    async def tool_run(self, command: str, timeout_seconds: int = 120) -> dict[str, object]:
        """Run a shell command in the isolated worktree without network or host credentials."""
        timeout = min(max(timeout_seconds, 1), 900)
        result = await self.run(command, timeout_seconds=timeout)
        return {
            "command": result.command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def close(self) -> None:
        return


class HostExecutionEnvironment(BubblewrapSandbox):
    kind = "host"

    def description(self) -> dict[str, object]:
        return {
            "environment": self.kind,
            "network": "host",
            "worktree": str(self.worktree),
            "isolation": "disabled",
        }

    async def run(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> SandboxResult:
        if not command.strip() or "\x00" in command:
            raise SandboxError("Command must be non-empty")
        command_activity: dict[str, Any] = {
            "command": command[:1_000],
            "started": True,
        }
        self.commands.append(command_activity)
        try:
            result = await run_command(
                ("/bin/bash", "--noprofile", "--norc", "-lc", command),
                cwd=self.worktree,
                timeout_seconds=timeout_seconds,
                check=False,
            )
        except CommandTimeoutError as error:
            command_activity.update(status="timed_out")
            raise SandboxError(f"Host command timed out: {command[:200]}") from error
        except OutputLimitError as error:
            command_activity.update(status="output_limit")
            raise SandboxError(str(error)) from error
        except BaseException:
            command_activity.update(status="cancelled")
            raise
        command_activity.update(status="completed", returncode=result.returncode)
        return SandboxResult(
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def tool_run(
        self,
        command: str,
        timeout_seconds: int = 120,
    ) -> dict[str, object]:
        """Run a shell command directly on the host from the phase worktree."""
        timeout = min(max(timeout_seconds, 1), 900)
        result = await self.run(command, timeout_seconds=timeout)
        return {
            "command": result.command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

import asyncio
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from mafia.config import get_settings


class CommandError(RuntimeError):
    def __init__(self, result: "CommandResult") -> None:
        self.result = result
        super().__init__(
            f"Command {result.argv[0]!r} failed with exit code {result.returncode}: {result.stderr[:500]}"
        )


class CommandTimeoutError(TimeoutError):
    pass


class OutputLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


SAFE_ENV_KEYS = (
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SSH_AUTH_SOCK",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
)


async def communicate_limited(
    process: asyncio.subprocess.Process,
    limit: int,
) -> tuple[bytes, bytes]:
    async def read(stream: asyncio.StreamReader | None, name: str) -> bytes:
        if stream is None:
            return b""
        output = bytearray()
        while chunk := await stream.read(64 * 1024):
            output.extend(chunk)
            if len(output) > limit:
                raise OutputLimitError(f"Command {name} exceeded the {limit}-byte output limit")
        return bytes(output)

    _, stdout, stderr = await asyncio.gather(
        process.wait(),
        read(process.stdout, "stdout"),
        read(process.stderr, "stderr"),
    )
    return stdout, stderr


async def kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


def host_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})
    if extra:
        environment.update(extra)
    return environment


async def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    check: bool = True,
    github_credentials: bool = False,
) -> CommandResult:
    if not argv or any("\x00" in argument for argument in argv):
        raise ValueError("argv must contain valid arguments")
    settings = get_settings()
    environment = host_environment(env)
    command_name = Path(argv[0]).name
    if settings.github_app_enabled and (
        command_name == "gh" or (command_name == "git" and github_credentials)
    ):
        from mafia.services.github_app import github_app_token

        config_index = int(environment.get("GIT_CONFIG_COUNT", "0"))
        environment.update(
            {
                "GH_TOKEN": await github_app_token(),
                "GIT_CONFIG_COUNT": str(config_index + 2),
                f"GIT_CONFIG_KEY_{config_index}": "credential.https://github.com.helper",
                f"GIT_CONFIG_VALUE_{config_index}": "!gh auth git-credential",
                f"GIT_CONFIG_KEY_{config_index + 1}": "core.hooksPath",
                f"GIT_CONFIG_VALUE_{config_index + 1}": "/dev/null",
            }
        )
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            communicate_limited(process, settings.command_output_limit),
            timeout=timeout_seconds or settings.command_timeout_seconds,
        )
    except TimeoutError as error:
        await kill_process_tree(process)
        raise CommandTimeoutError(f"Command {argv[0]!r} timed out") from error
    except OutputLimitError:
        await kill_process_tree(process)
        raise
    except BaseException:
        await kill_process_tree(process)
        raise
    result = CommandResult(
        argv=tuple(argv),
        returncode=process.returncode or 0,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )
    if check and result.returncode != 0:
        raise CommandError(result)
    return result

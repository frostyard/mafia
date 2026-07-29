import asyncio
import shutil
from dataclasses import dataclass

import httpx
from mafia.config import get_settings
from mafia.domain.schemas import Capability, Readiness
from mafia.services.commands import (
    CommandError,
    CommandTimeoutError,
    OutputLimitError,
    run_command,
)
from mafia.services.devcontainers import (
    DevContainerError,
    resolve_devcontainer_cli,
    select_container_engine,
)
from mafia.services.github_app import (
    GitHubAppAuthenticationError,
    github_app_token,
)


@dataclass(frozen=True)
class Probe:
    name: str
    executable: str
    args: tuple[str, ...]


PROBES = (
    Probe("git", "git", ("--version",)),
    Probe("copilot", "copilot", ("--version",)),
    Probe("bubblewrap", "bwrap", ("--version",)),
    Probe("process-limits", "prlimit", ("--version",)),
)


async def _probe(probe: Probe) -> Capability:
    executable = shutil.which(probe.executable)
    if executable is None:
        return Capability(name=probe.name, available=False, detail=f"{probe.executable} not found")
    process = await asyncio.create_subprocess_exec(
        executable,
        *probe.args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()
        return Capability(name=probe.name, available=False, detail="probe timed out")
    output = (stdout or stderr).decode(errors="replace").strip().splitlines()
    detail = output[0][:300] if output else f"exit code {process.returncode}"
    return Capability(name=probe.name, available=process.returncode == 0, detail=detail)


async def readiness() -> Readiness:
    settings = get_settings()
    required_probes = PROBES if settings.execution_mode == "isolated" else PROBES[:2]
    capabilities = list(
        await asyncio.gather(*(_probe(probe) for probe in required_probes))
    )
    if shutil.which("gh") is None:
        capabilities.append(
            Capability(name="github", available=False, detail="gh not found")
        )
    elif settings.github_app_enabled:
        try:
            await github_app_token()
            capabilities.append(
                Capability(
                    name="github",
                    available=True,
                    detail="GitHub App installation authentication available",
                )
            )
        except (GitHubAppAuthenticationError, httpx.HTTPError) as error:
            capabilities.append(
                Capability(name="github", available=False, detail=str(error)[:300])
            )
    else:
        capabilities.append(
            await _probe(Probe("github", "gh", ("auth", "status")))
        )
    required_count = len(capabilities)
    if settings.execution_mode == "host":
        capabilities.append(
            Capability(
                name="execution-environment",
                available=True,
                detail="host execution; isolation disabled",
            )
        )
        return Readiness(
            ready=all(capability.available for capability in capabilities[:required_count]),
            capabilities=capabilities,
        )
    try:
        cli = resolve_devcontainer_cli()
        cli_result = await run_command((cli, "--version"), timeout_seconds=10)
        capabilities.append(
            Capability(
                name="devcontainer",
                available=True,
                detail=cli_result.stdout.strip() or "available",
            )
        )
    except (
        CommandError,
        CommandTimeoutError,
        DevContainerError,
        OutputLimitError,
        OSError,
    ) as error:
        capabilities.append(
            Capability(name="devcontainer", available=False, detail=str(error)[:300])
        )
    try:
        engine = await select_container_engine()
        capabilities.append(
            Capability(
                name="container-engine",
                available=True,
                detail=f"{engine.name} {engine.version}",
            )
        )
    except DevContainerError as error:
        capabilities.append(
            Capability(name="container-engine", available=False, detail=str(error)[:300])
        )
    return Readiness(
        ready=all(capability.available for capability in capabilities[:required_count]),
        capabilities=capabilities,
    )

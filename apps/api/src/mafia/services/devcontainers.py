import hashlib
import json
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from mafia.config import get_settings
from mafia.services.commands import (
    CommandError,
    CommandTimeoutError,
    OutputLimitError,
    run_command,
)
from mafia.services.sandbox import (
    BubblewrapSandbox,
    ExecutionEnvironment,
    HostExecutionEnvironment,
    SandboxResult,
)

logger = logging.getLogger(__name__)

ContainerEngineName = Literal["docker", "podman"]
DevContainerPolicy = Literal["strict", "allow-anything"]
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class DevContainerError(RuntimeError):
    pass


class DevContainerPolicyError(DevContainerError):
    pass


@dataclass(frozen=True)
class ContainerEngine:
    name: ContainerEngineName
    executable: str
    version: str


def find_devcontainer_config(worktree: Path) -> Path | None:
    for candidate in (
        worktree / ".devcontainer" / "devcontainer.json",
        worktree / ".devcontainer.json",
    ):
        if candidate.is_symlink():
            raise DevContainerPolicyError(
                f"Dev Container configuration cannot be a symlink: {candidate.relative_to(worktree)}"
            )
        if candidate.is_file():
            return candidate
    return None


def resolve_devcontainer_cli() -> str:
    configured = get_settings().devcontainer_cli_path
    if resolved := shutil.which(configured):
        return resolved
    configured_path = Path(configured)
    if configured_path.is_file():
        return str(configured_path.resolve())
    project_cli = Path(__file__).resolve().parents[5] / "node_modules" / ".bin" / "devcontainer"
    if project_cli.is_file():
        return str(project_cli)
    raise DevContainerError(
        "Dev Container CLI is unavailable. Run `npm install` at the MAFIA repository root "
        "or set MAFIA_DEVCONTAINER_CLI_PATH."
    )


async def _probe_engine(name: ContainerEngineName) -> ContainerEngine:
    executable = shutil.which(name)
    if executable is None:
        raise DevContainerError(f"{name} is not installed")
    try:
        result = await run_command(
            (
                executable,
                "info",
                "--format",
                "{{.ServerVersion}}" if name == "docker" else "{{.Version.Version}}",
            ),
            timeout_seconds=15,
            check=False,
        )
    except (CommandTimeoutError, OutputLimitError, OSError) as error:
        raise DevContainerError(f"{name} could not be probed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "engine is unavailable"
        raise DevContainerError(f"{name} is installed but not usable: {detail[:500]}")
    return ContainerEngine(
        name=name,
        executable=executable,
        version=result.stdout.strip() or "unknown",
    )


async def select_container_engine(
    preference: Literal["auto", "docker", "podman"] | None = None,
) -> ContainerEngine:
    selected = preference or get_settings().container_engine
    candidates: tuple[ContainerEngineName, ...] = (
        ("docker", "podman") if selected == "auto" else (selected,)
    )
    errors: list[str] = []
    for candidate in candidates:
        try:
            return await _probe_engine(candidate)
        except DevContainerError as error:
            errors.append(str(error))
    raise DevContainerError("No usable container engine was found: " + "; ".join(errors))


def _contains_local_environment(value: object) -> bool:
    if isinstance(value, str):
        return "${localEnv:" in value or "${env:" in value
    if isinstance(value, list):
        values = cast(list[object], value)
        return any(_contains_local_environment(item) for item in values)
    if isinstance(value, dict):
        values = cast(dict[object, object], value)
        return any(
            _contains_local_environment(key) or _contains_local_environment(item)
            for key, item in values.items()
        )
    return False


def _mount_type(mount: object) -> str | None:
    if isinstance(mount, dict):
        values = cast(dict[object, object], mount)
        value = values.get("type")
        return value if isinstance(value, str) else None
    if not isinstance(mount, str):
        return None
    values = {
        key.strip(): value.strip()
        for part in mount.split(",")
        if "=" in part
        for key, value in (part.split("=", 1),)
    }
    return values.get("type")


def _mount_source(mount: object) -> object:
    if isinstance(mount, dict):
        values = cast(dict[object, object], mount)
        return values.get("source") or values.get("src")
    if not isinstance(mount, str):
        return None
    values = {
        key.strip(): value.strip()
        for part in mount.split(",")
        if "=" in part
        for key, value in (part.split("=", 1),)
    }
    return values.get("source") or values.get("src")


_DISALLOWED_RUN_ARGS = (
    "--add-host",
    "--cap-add",
    "--cgroupns",
    "--device",
    "--env-file",
    "--gpus",
    "--group-add",
    "--ipc",
    "--link",
    "--mount",
    "--network",
    "--pid",
    "--publish",
    "--publish-all",
    "--privileged",
    "--runtime",
    "--security-opt",
    "--sysctl",
    "--userns",
    "--uts",
    "--volume",
    "--volumes-from",
    "-p",
    "-P",
    "-v",
)


def _path_is_confined(root: Path, candidate: Path) -> bool:
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=False)
    return resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents


def enforce_devcontainer_policy(
    configuration: dict[str, Any],
    *,
    worktree: Path | None = None,
    config_path: Path | None = None,
    policy: DevContainerPolicy = "strict",
) -> None:
    if policy == "allow-anything":
        return
    violations: list[str] = []
    if configuration.get("initializeCommand"):
        violations.append("initializeCommand runs on the host")
    if configuration.get("privileged") is True:
        violations.append("privileged containers are not allowed")
    if configuration.get("dockerComposeFile") or configuration.get("service"):
        violations.append("Docker Compose configurations are not supported by the safe runner")
    if configuration.get("workspaceMount"):
        violations.append("custom workspaceMount can expose arbitrary host paths")
    if configuration.get("capAdd"):
        violations.append("additional Linux capabilities are not allowed")
    if configuration.get("securityOpt"):
        violations.append("custom container security options are not allowed")
    if configuration.get("appPort") or configuration.get("forwardPorts"):
        violations.append("host port publication is not allowed")
    build = configuration.get("build")
    if isinstance(build, dict):
        build_values = cast(dict[object, object], build)
        if build_values.get("options"):
            violations.append("custom image build options are not allowed")
        if build_values.get("contexts"):
            violations.append("additional image build contexts are not allowed")
        if worktree is not None and config_path is not None:
            for key in ("context", "dockerfile", "dockerFile"):
                value = build_values.get(key)
                if isinstance(value, str) and not _path_is_confined(
                    worktree,
                    config_path.parent / value,
                ):
                    violations.append(f"build.{key} resolves outside the worktree")
    mounts = configuration.get("mounts", [])
    if isinstance(mounts, list):
        mount_values = cast(list[object], mounts)
        if any(
            _mount_type(mount) != "volume" or _mount_source(mount)
            for mount in mount_values
        ):
            violations.append("only anonymous volume mounts are allowed")
    elif mounts:
        violations.append("mounts must be an array of anonymous volume mounts")
    run_args = configuration.get("runArgs", [])
    if isinstance(run_args, list):
        for argument in cast(list[object], run_args):
            if isinstance(argument, str) and any(
                argument == prefix or argument.startswith(f"{prefix}=")
                for prefix in _DISALLOWED_RUN_ARGS
            ):
                violations.append(f"runArgs contains disallowed option {argument}")
    elif run_args:
        violations.append("runArgs must be an array")
    if _contains_local_environment(configuration):
        violations.append("local environment substitution could forward host credentials")
    if violations:
        raise DevContainerPolicyError(
            "Dev Container policy rejected this configuration: " + "; ".join(violations)
        )


def _validate_raw_configuration(
    worktree: Path,
    config_path: Path,
    policy: DevContainerPolicy,
) -> None:
    if config_path.is_symlink() or not _path_is_confined(worktree, config_path):
        raise DevContainerPolicyError(
            "Dev Container configuration must be a regular file inside the worktree"
        )
    raw_configuration = config_path.read_text(encoding="utf-8", errors="replace")
    if policy == "strict" and (
        "${localEnv:" in raw_configuration or "${env:" in raw_configuration
    ):
        raise DevContainerPolicyError(
            "Dev Container configuration cannot substitute host environment variables"
        )


async def read_devcontainer_configuration(
    worktree: Path,
    config_path: Path,
    engine: ContainerEngine,
    cli: str,
    policy: DevContainerPolicy | None = None,
) -> dict[str, Any]:
    selected_policy = policy or get_settings().devcontainer_policy
    _validate_raw_configuration(worktree, config_path, selected_policy)
    result = await run_command(
        (
            cli,
            "read-configuration",
            "--workspace-folder",
            str(worktree),
            "--config",
            str(config_path),
            "--include-merged-configuration",
            "--docker-path",
            engine.executable,
        ),
        timeout_seconds=60,
        env={"SSH_AUTH_SOCK": ""},
    )
    try:
        payload = json.loads(result.stdout)
        configuration = payload["mergedConfiguration"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise DevContainerError("Dev Container CLI returned invalid configuration data") from error
    if not isinstance(configuration, dict):
        raise DevContainerError("Dev Container configuration is not an object")
    typed_configuration = cast(dict[str, Any], configuration)
    enforce_devcontainer_policy(
        typed_configuration,
        worktree=worktree,
        config_path=config_path,
        policy=selected_policy,
    )
    return typed_configuration


class DevContainerEnvironment(BubblewrapSandbox):
    kind = "devcontainer"

    def __init__(
        self,
        worktree: Path,
        *,
        cli: str,
        config_path: Path,
        engine: ContainerEngine,
        container_id: str,
        remote_user: str,
        remote_workspace_folder: str,
        network: str,
        policy: DevContainerPolicy = "strict",
    ) -> None:
        super().__init__(worktree)
        self.cli = cli
        self.config_path = config_path
        self.engine = engine
        self.container_id = container_id
        self.remote_user = remote_user
        self.remote_workspace_folder = remote_workspace_folder
        self.network = network
        self.policy = policy
        self._closed = False

    @classmethod
    async def create(
        cls,
        worktree: Path,
        *,
        config_path: Path,
        engine: ContainerEngine,
        progress: ProgressCallback | None = None,
    ) -> "DevContainerEnvironment":
        settings = get_settings()
        cli = resolve_devcontainer_cli()
        if progress is not None:
            await progress(
                {
                    "step": "validating_configuration",
                    "engine": engine.name,
                    "policy": settings.devcontainer_policy,
                }
            )
        await read_devcontainer_configuration(
            worktree,
            config_path,
            engine,
            cli,
            policy=settings.devcontainer_policy,
        )
        label = f"mafia.execution={hashlib.sha256(str(worktree).encode()).hexdigest()[:24]}"
        if progress is not None:
            await progress({"step": "building_and_starting", "engine": engine.name})
        try:
            result = await run_command(
                (
                    cli,
                    "up",
                    "--workspace-folder",
                    str(worktree),
                    "--config",
                    str(config_path),
                    "--docker-path",
                    engine.executable,
                    "--id-label",
                    label,
                    "--remove-existing-container",
                    "--gpu-availability",
                    "none",
                ),
                timeout_seconds=settings.devcontainer_setup_timeout_seconds,
                env={"SSH_AUTH_SOCK": ""},
            )
        except BaseException:
            await cls._remove_labeled_containers(engine, label)
            raise
        try:
            payload = json.loads(result.stdout)
            container_id = payload["containerId"]
            remote_user = payload["remoteUser"]
            remote_workspace_folder = payload["remoteWorkspaceFolder"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            await cls._remove_labeled_containers(engine, label)
            raise DevContainerError("Dev Container CLI returned invalid startup data") from error
        if not all(
            isinstance(value, str) and value
            for value in (container_id, remote_user, remote_workspace_folder)
        ):
            await cls._remove_labeled_containers(engine, label)
            raise DevContainerError("Dev Container startup data is incomplete")
        environment = cls(
            worktree,
            cli=cli,
            config_path=config_path,
            engine=engine,
            container_id=container_id,
            remote_user=remote_user,
            remote_workspace_folder=remote_workspace_folder,
            network=settings.devcontainer_network,
            policy=settings.devcontainer_policy,
        )
        try:
            if progress is not None:
                await progress({"step": "applying_resource_limits", **environment.description()})
            await environment._apply_resource_limits()
            if settings.devcontainer_network == "setup-only":
                if progress is not None:
                    await progress({"step": "disconnecting_network", **environment.description()})
                await environment._disconnect_networks()
        except BaseException:
            await environment.close()
            raise
        if progress is not None:
            await progress({"step": "ready", **environment.description()})
        return environment

    @staticmethod
    async def _remove_labeled_containers(
        engine: ContainerEngine,
        label: str,
    ) -> None:
        listed = await run_command(
            (
                engine.executable,
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label={label}",
            ),
            timeout_seconds=30,
            check=False,
        )
        for container_id in listed.stdout.splitlines():
            if container_id.strip():
                await run_command(
                    (
                        engine.executable,
                        "rm",
                        "--force",
                        "--volumes",
                        container_id.strip(),
                    ),
                    timeout_seconds=60,
                    check=False,
                )

    def description(self) -> dict[str, object]:
        return {
            "environment": self.kind,
            "engine": self.engine.name,
            "engine_version": self.engine.version,
            "container_id": self.container_id[:12],
            "config": str(self.config_path.relative_to(self.worktree)),
            "policy": self.policy,
            "network": self.network,
            "remote_user": self.remote_user,
            "workspace": self.remote_workspace_folder,
        }

    def activity_snapshot(self) -> dict[str, object]:
        return {**super().activity_snapshot(), **self.description()}

    async def _apply_resource_limits(self) -> None:
        settings = get_settings()
        await run_command(
            (
                self.engine.executable,
                "update",
                "--cpus",
                str(settings.container_cpu_limit),
                "--memory",
                settings.container_memory_limit,
                "--memory-swap",
                settings.container_memory_limit,
                "--pids-limit",
                str(settings.sandbox_process_limit),
                self.container_id,
            ),
            timeout_seconds=30,
        )

    async def _disconnect_networks(self) -> None:
        result = await run_command(
            (
                self.engine.executable,
                "inspect",
                "--format",
                "{{range $key, $value := .NetworkSettings.Networks}}{{$key}}{{println}}{{end}}",
                self.container_id,
            ),
            timeout_seconds=30,
        )
        for network in (line.strip() for line in result.stdout.splitlines()):
            if network:
                await run_command(
                    (
                        self.engine.executable,
                        "network",
                        "disconnect",
                        network,
                        self.container_id,
                    ),
                    timeout_seconds=30,
                )

    async def run(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> SandboxResult:
        if not command.strip() or "\x00" in command:
            raise DevContainerError("Command must be non-empty")
        if self._closed:
            raise DevContainerError("The Dev Container has already been removed")
        command_activity: dict[str, Any] = {
            "command": command[:1_000],
            "started": True,
        }
        self.commands.append(command_activity)
        try:
            result = await run_command(
                (
                    self.cli,
                    "exec",
                    "--container-id",
                    self.container_id,
                    "--config",
                    str(self.config_path),
                    "--docker-path",
                    self.engine.executable,
                    "/bin/sh",
                    "-lc",
                    command,
                ),
                timeout_seconds=timeout_seconds,
                check=False,
                env={"SSH_AUTH_SOCK": ""},
            )
        except BaseException:
            command_activity.update(status="failed")
            raise
        command_activity.update(status="completed", returncode=result.returncode)
        return SandboxResult(
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await run_command(
                (
                    self.engine.executable,
                    "rm",
                    "--force",
                    "--volumes",
                    self.container_id,
                ),
                timeout_seconds=60,
            )
        except CommandError as error:
            if "no such container" not in error.result.stderr.casefold():
                raise
        self._closed = True


async def create_execution_environment(
    worktree: Path,
    *,
    progress: ProgressCallback | None = None,
) -> ExecutionEnvironment:
    if get_settings().execution_mode == "host":
        environment = HostExecutionEnvironment(worktree)
        if progress is not None:
            await progress({"step": "ready", **environment.description()})
        return environment
    config_path = find_devcontainer_config(worktree)
    if config_path is None:
        if progress is not None:
            await progress({"step": "ready", "environment": "bubblewrap"})
        return BubblewrapSandbox(worktree)
    engine = await select_container_engine()
    return await DevContainerEnvironment.create(
        worktree,
        config_path=config_path,
        engine=engine,
        progress=progress,
    )


async def close_environment_after_failure(environment: ExecutionEnvironment | None) -> None:
    if environment is None:
        return
    try:
        await environment.close()
    except Exception:
        logger.exception("Could not clean up execution environment after phase failure")

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from mafia.services import devcontainers
from mafia.services.commands import CommandResult
from mafia.services.devcontainers import (
    ContainerEngine,
    DevContainerEnvironment,
    DevContainerPolicyError,
    enforce_devcontainer_policy,
    find_devcontainer_config,
    select_container_engine,
)
from mafia.services.sandbox import BubblewrapSandbox, HostExecutionEnvironment


def result(
    executable: str,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> CommandResult:
    return CommandResult(
        argv=(executable,),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_finds_standard_devcontainer_configuration(tmp_path: Path) -> None:
    nested = tmp_path / ".devcontainer" / "devcontainer.json"
    nested.parent.mkdir()
    nested.write_text("{}", encoding="utf-8")

    assert find_devcontainer_config(tmp_path) == nested


@pytest.mark.asyncio
async def test_repository_without_devcontainer_uses_bubblewrap(tmp_path: Path) -> None:
    environment = await devcontainers.create_execution_environment(tmp_path)

    assert isinstance(environment, BubblewrapSandbox)


@pytest.mark.asyncio
async def test_host_mode_bypasses_devcontainer_and_bubblewrap(
    tmp_path: Path,
) -> None:
    (tmp_path / ".devcontainer.json").write_text(
        '{"privileged":true}',
        encoding="utf-8",
    )
    environment = await devcontainers.create_execution_environment(
        tmp_path, execution_mode="host"
    )

    assert isinstance(environment, HostExecutionEnvironment)


def test_rejects_symlinked_devcontainer_configuration(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    config = tmp_path / "workspace" / ".devcontainer.json"
    config.parent.mkdir()
    config.symlink_to(outside)

    with pytest.raises(DevContainerPolicyError, match="symlink"):
        find_devcontainer_config(config.parent)


def test_policy_accepts_container_lifecycle_and_volume_mounts() -> None:
    enforce_devcontainer_policy(
        {
            "image": "mcr.microsoft.com/devcontainers/python:1-3.12",
            "postCreateCommand": "uv sync",
            "mounts": ["target=/home/vscode/.cache/uv,type=volume"],
            "runArgs": ["--memory=4g"],
        }
    )


def test_allow_anything_policy_accepts_configuration_rejected_by_strict_policy() -> None:
    configuration = {
        "privileged": True,
        "capAdd": ["SYS_PTRACE"],
        "securityOpt": ["seccomp=unconfined"],
        "runArgs": ["--cap-add=SYS_PTRACE", "--security-opt=seccomp=unconfined"],
        "containerEnv": {"HOME_COPY": "${localEnv:HOME}"},
    }

    enforce_devcontainer_policy(configuration, policy="allow-anything")


@pytest.mark.parametrize(
    ("configuration", "message"),
    [
        ({"initializeCommand": "touch /tmp/host"}, "initializeCommand"),
        ({"privileged": True}, "privileged"),
        ({"dockerComposeFile": "compose.yaml"}, "Compose"),
        (
            {"mounts": ["source=/home/user,target=/host,type=bind"]},
            "anonymous volume mounts",
        ),
        (
            {"mounts": ["source=shared-cache,target=/cache,type=volume"]},
            "anonymous volume mounts",
        ),
        ({"runArgs": ["--network=host"]}, "--network=host"),
        ({"runArgs": ["--pid", "host"]}, "--pid"),
        ({"runArgs": ["--gpus", "all"]}, "--gpus"),
        ({"containerEnv": {"TOKEN": "${localEnv:TOKEN}"}}, "host credentials"),
    ],
)
def test_policy_rejects_host_exposure(
    configuration: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(DevContainerPolicyError, match=message):
        enforce_devcontainer_policy(configuration)


def test_policy_rejects_build_context_outside_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "workspace"
    config = worktree / ".devcontainer" / "devcontainer.json"
    config.parent.mkdir(parents=True)

    with pytest.raises(DevContainerPolicyError, match="outside the worktree"):
        enforce_devcontainer_policy(
            {"build": {"context": "../../host"}},
            worktree=worktree,
            config_path=config,
        )


@pytest.mark.asyncio
async def test_raw_configuration_rejects_host_environment_substitution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / ".devcontainer.json"
    config.write_text(
        '{"image":"debian:bookworm","containerEnv":{"HOME_COPY":"${localEnv:HOME}"}}',
        encoding="utf-8",
    )

    async def unexpected_command(*_: object, **__: object) -> CommandResult:
        raise AssertionError("The CLI must not run before raw configuration validation")

    monkeypatch.setattr(devcontainers, "run_command", unexpected_command)
    with pytest.raises(DevContainerPolicyError, match="host environment"):
        await devcontainers.read_devcontainer_configuration(
            tmp_path,
            config,
            ContainerEngine(name="docker", executable="/usr/bin/docker", version="29"),
            "/opt/devcontainer",
        )


@pytest.mark.asyncio
async def test_allow_anything_policy_permits_raw_host_environment_substitution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / ".devcontainer.json"
    config.write_text(
        '{"image":"debian:bookworm","containerEnv":{"HOME_COPY":"${localEnv:HOME}"}}',
        encoding="utf-8",
    )

    async def configuration_result(
        argv: tuple[str, ...],
        **_: object,
    ) -> CommandResult:
        return result(
            argv[0],
            stdout='{"mergedConfiguration":{"privileged":true}}',
        )

    monkeypatch.setattr(devcontainers, "run_command", configuration_result)

    configuration = await devcontainers.read_devcontainer_configuration(
        tmp_path,
        config,
        ContainerEngine(name="docker", executable="/usr/bin/docker", version="29"),
        "/opt/devcontainer",
        policy="allow-anything",
    )

    assert configuration == {"privileged": True}


@pytest.mark.asyncio
async def test_auto_engine_falls_back_to_podman(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    async def probe(name: devcontainers.ContainerEngineName) -> ContainerEngine:
        attempts.append(name)
        if name == "docker":
            raise devcontainers.DevContainerError("Docker daemon unavailable")
        return ContainerEngine(name="podman", executable="/usr/bin/podman", version="5.4.2")

    monkeypatch.setattr(devcontainers, "_probe_engine", probe)

    selected = await select_container_engine("auto")

    assert selected.name == "podman"
    assert attempts == ["docker", "podman"]


@pytest.mark.asyncio
async def test_devcontainer_command_uses_selected_engine_and_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / ".devcontainer.json"
    config.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    async def fake_run_command(
        argv: tuple[str, ...],
        **_: object,
    ) -> CommandResult:
        calls.append(argv)
        return result(argv[0], stdout="inside\n")

    monkeypatch.setattr(devcontainers, "run_command", fake_run_command)
    environment = DevContainerEnvironment(
        tmp_path,
        cli="/opt/devcontainer",
        config_path=config,
        engine=ContainerEngine(
            name="podman",
            executable="/usr/bin/podman",
            version="5.4.2",
        ),
        container_id="container-123",
        remote_user="vscode",
        remote_workspace_folder="/workspaces/project",
        network="setup-only",
    )

    command_result = await environment.run("go test ./...", timeout_seconds=90)

    assert command_result.stdout == "inside\n"
    assert environment.description()["policy"] == "strict"
    assert calls == [
        (
            "/opt/devcontainer",
            "exec",
            "--container-id",
            "container-123",
            "--config",
            str(config),
            "--docker-path",
            "/usr/bin/podman",
            "/bin/sh",
            "-lc",
            "go test ./...",
        )
    ]


@pytest.fixture(params=["docker", "podman"])
async def devcontainer_fixture(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncGenerator[tuple[Path, ContainerEngine]]:
    engine_name = request.param
    assert engine_name in {"docker", "podman"}
    try:
        devcontainers.resolve_devcontainer_cli()
    except devcontainers.DevContainerError as error:
        pytest.skip(str(error))
    try:
        engine = await select_container_engine(engine_name)
    except devcontainers.DevContainerError as error:
        pytest.skip(str(error))
    image = "docker.io/library/debian:bookworm-slim"
    available = await devcontainers.run_command(
        (engine.executable, "image", "inspect", image),
        check=False,
    )
    if available.returncode != 0:
        pytest.skip(f"{image} is not available in {engine.name}")
    worktree = tmp_path / "workspace"
    config = worktree / ".devcontainer" / "devcontainer.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        """
        {
          "image": "docker.io/library/debian:bookworm-slim",
          "postCreateCommand": "printf ready > /tmp/mafia-ready"
        }
        """,
        encoding="utf-8",
    )
    yield worktree, engine


@pytest.mark.asyncio
async def test_devcontainer_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    devcontainer_fixture: tuple[Path, ContainerEngine],
) -> None:
    worktree, engine = devcontainer_fixture
    monkeypatch.setenv("MAFIA_DEVCONTAINER_NETWORK", "setup-only")
    devcontainers.get_settings.cache_clear()
    environment: DevContainerEnvironment | None = None
    container_id: str | None = None
    try:
        environment = await DevContainerEnvironment.create(
            worktree,
            config_path=worktree / ".devcontainer" / "devcontainer.json",
            engine=engine,
        )
        container_id = environment.container_id
        command_result = await environment.run(
            "test -f /tmp/mafia-ready && printf devcontainer-ok",
            timeout_seconds=30,
        )
        networks = await devcontainers.run_command(
            (
                engine.executable,
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                environment.container_id,
            )
        )
        assert command_result.returncode == 0
        assert command_result.stdout == "devcontainer-ok"
        assert networks.stdout.strip() in {"{}", "null"}
    finally:
        if environment is not None:
            await environment.close()
        devcontainers.get_settings.cache_clear()
    assert container_id is not None
    removed = await devcontainers.run_command(
        (engine.executable, "inspect", container_id),
        check=False,
    )
    assert removed.returncode != 0

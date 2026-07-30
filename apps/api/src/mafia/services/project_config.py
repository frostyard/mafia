import hashlib
import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from mafia.config import Settings, get_settings
from mafia.services.commands import run_command
from mafia.services.operations import tracked_operation
from mafia.services.repositories import RepositoryIdentity
from mafia.services.sandbox import ExecutionEnvironment
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ProjectConfigurationError(ValueError):
    pass


class ProjectValidationError(RuntimeError):
    pass


class ValidationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    run: str = Field(min_length=1, max_length=2_000)
    working_directory: str = "."
    timeout_seconds: int = Field(default=900, ge=1, le=3_600)

    @field_validator("name", "run")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        normalized = value.strip() or "."
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("working_directory must stay inside the repository")
        return normalized


class ValidationConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[ValidationCommand] = Field(min_length=1, max_length=20)


class ExecutionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["isolated", "host"] = "isolated"


class ProjectConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    execution: ExecutionConfiguration = Field(default_factory=ExecutionConfiguration)
    validation: ValidationConfiguration | None = None


@dataclass(frozen=True)
class ResolvedProjectConfiguration:
    execution_mode: Literal["isolated", "host"]
    validation_commands: tuple[ValidationCommand, ...]
    validation_source: Literal["repository", "host", "missing"]
    validation_sha256: str | None

    def snapshot(self) -> dict[str, object]:
        return {
            "version": 1,
            "execution_mode": self.execution_mode,
            "validation_source": self.validation_source,
            "validation_sha256": self.validation_sha256,
            "validation_commands": [
                command.model_dump() for command in self.validation_commands
            ],
        }


def parse_project_configuration(
    content: str,
    *,
    source: Literal["host", "repository"],
) -> ProjectConfiguration:
    try:
        raw = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise ProjectConfigurationError(f"Invalid TOML: {error}") from error
    if source == "repository" and "execution" in raw:
        raise ProjectConfigurationError(
            "Repository .mafia.toml cannot configure execution; use mafia project settings"
        )
    try:
        return ProjectConfiguration.model_validate(raw)
    except ValidationError as error:
        raise ProjectConfigurationError(str(error)) from error


def render_project_configuration(configuration: ProjectConfiguration) -> str:
    lines = [
        "version = 1",
        "",
        "[execution]",
        f'mode = "{configuration.execution.mode}"',
    ]
    if configuration.validation is not None:
        for command in configuration.validation.commands:
            lines.extend(
                [
                    "",
                    "[[validation.commands]]",
                    f'name = "{_toml_string(command.name)}"',
                    f'run = "{_toml_string(command.run)}"',
                    f'working_directory = "{_toml_string(command.working_directory)}"',
                    f"timeout_seconds = {command.timeout_seconds}",
                ]
            )
    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def default_project_configuration() -> ProjectConfiguration:
    return ProjectConfiguration()


def project_configuration_path(
    identity: RepositoryIdentity,
    settings: Settings | None = None,
) -> Path:
    root = (settings or get_settings()).projects_dir
    return root / identity.owner / identity.name / ".mafia.toml"


def read_host_project_configuration(
    identity: RepositoryIdentity,
    settings: Settings | None = None,
) -> tuple[ProjectConfiguration, str, bool]:
    path = project_configuration_path(identity, settings)
    if not path.exists():
        configuration = default_project_configuration()
        return configuration, render_project_configuration(configuration), False
    if path.is_symlink() or not path.is_file():
        raise ProjectConfigurationError(f"Host project configuration is not a regular file: {path}")
    content = path.read_text(encoding="utf-8")
    return parse_project_configuration(content, source="host"), content, True


def write_host_project_configuration(
    identity: RepositoryIdentity,
    content: str,
    settings: Settings | None = None,
) -> tuple[ProjectConfiguration, str]:
    configuration = parse_project_configuration(content, source="host")
    canonical = render_project_configuration(configuration)
    path = project_configuration_path(identity, settings)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(".mafia.toml.tmp")
    temporary.write_text(canonical, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return configuration, canonical


def resolve_project_configuration(
    identity: RepositoryIdentity,
    worktree: Path,
    settings: Settings | None = None,
) -> ResolvedProjectConfiguration:
    host, host_content, _ = read_host_project_configuration(identity, settings)
    repository_path = worktree / ".mafia.toml"
    if repository_path.exists() or repository_path.is_symlink():
        if repository_path.is_symlink() or not repository_path.is_file():
            raise ProjectConfigurationError("Repository .mafia.toml must be a regular file")
        repository_content = repository_path.read_text(encoding="utf-8")
        repository = parse_project_configuration(repository_content, source="repository")
        validation = repository.validation
        source: Literal["repository", "host", "missing"] = (
            "repository" if validation is not None else "missing"
        )
        digest_content = repository_content
    else:
        validation = host.validation
        source = "host" if validation is not None else "missing"
        digest_content = host_content
    return ResolvedProjectConfiguration(
        execution_mode=host.execution.mode,
        validation_commands=tuple(validation.commands if validation is not None else ()),
        validation_source=source,
        validation_sha256=(
            hashlib.sha256(digest_content.encode()).hexdigest()
            if validation is not None
            else None
        ),
    )


def resolve_project_configuration_content(
    identity: RepositoryIdentity,
    repository_content: str | None,
    settings: Settings | None = None,
) -> ResolvedProjectConfiguration:
    host, host_content, _ = read_host_project_configuration(identity, settings)
    if repository_content is not None:
        repository = parse_project_configuration(
            repository_content, source="repository"
        )
        validation = repository.validation
        source: Literal["repository", "host", "missing"] = (
            "repository" if validation is not None else "missing"
        )
        digest_content = repository_content
    else:
        validation = host.validation
        source = "host" if validation is not None else "missing"
        digest_content = host_content
    return ResolvedProjectConfiguration(
        execution_mode=host.execution.mode,
        validation_commands=tuple(validation.commands if validation is not None else ()),
        validation_source=source,
        validation_sha256=(
            hashlib.sha256(digest_content.encode()).hexdigest()
            if validation is not None
            else None
        ),
    )


async def source_validation_status(
    identity: RepositoryIdentity,
    cache_path: str | None,
    source_sha: str,
    settings: Settings | None = None,
) -> tuple[bool, str]:
    host, _, _ = read_host_project_configuration(identity, settings)
    if cache_path is None:
        return host.validation is not None, "host" if host.validation is not None else "missing"
    result = await run_command(
        ("git", "--git-dir", cache_path, "show", f"{source_sha}:.mafia.toml"),
        check=False,
    )
    if result.returncode != 0 and "does not exist in" in result.stderr:
        return host.validation is not None, "host" if host.validation is not None else "missing"
    if result.returncode != 0:
        raise ProjectConfigurationError(
            f"Could not read repository .mafia.toml at {source_sha}: "
            f"{result.stderr.strip()[-1_000:]}"
        )
    repository = parse_project_configuration(result.stdout, source="repository")
    return (
        repository.validation is not None,
        "repository" if repository.validation is not None else "missing",
    )


async def run_deterministic_validation(
    environment: ExecutionEnvironment,
    configuration: ResolvedProjectConfiguration,
    *,
    run_id: str,
    stage: str,
    phase_id: str | None = None,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for index, validation_command in enumerate(configuration.validation_commands, 1):
        command = validation_command.run
        if validation_command.working_directory != ".":
            command = (
                f"cd {shlex.quote(validation_command.working_directory)} && "
                f"{validation_command.run}"
            )
        async with tracked_operation(
            run_id=run_id,
            phase_id=phase_id,
            operation_type="environment.project_validation",
            operation_key=f"{stage}:{index}",
            timeout_seconds=validation_command.timeout_seconds,
            detail={
                "name": validation_command.name,
                "command": validation_command.run,
                "working_directory": validation_command.working_directory,
                "ordinal": index,
                "stage": stage,
                "source": configuration.validation_source,
                "configuration_sha256": configuration.validation_sha256,
            },
        ) as operation:
            result = await environment.run(
                command,
                timeout_seconds=validation_command.timeout_seconds,
            )
            outcome: dict[str, object] = {
                "name": validation_command.name,
                "command": validation_command.run,
                "returncode": result.returncode,
            }
            operation.set_result(outcome)
            results.append(outcome)
            if result.returncode != 0:
                output = (result.stderr or result.stdout)[-2_000:]
                raise ProjectValidationError(
                    f"Project validation failed: {validation_command.name}\n{output}"
                )
    return results

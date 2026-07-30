from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MAFIA_", extra="ignore")

    data_dir: Path = Path("data")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_workers: Literal[1] = 1
    command_timeout_seconds: float = Field(default=120.0, gt=0)
    command_output_limit: int = Field(default=1_000_000, gt=0)
    sandbox_process_limit: int = Field(default=128, ge=16, le=1024)
    container_engine: Literal["auto", "docker", "podman"] = "auto"
    devcontainer_cli_path: str = "devcontainer"
    devcontainer_policy: Literal["strict", "allow-anything"] = "strict"
    devcontainer_setup_timeout_seconds: int = Field(default=1_800, ge=60, le=7_200)
    devcontainer_network: Literal["enabled", "setup-only"] = "setup-only"
    container_cpu_limit: float = Field(default=4.0, gt=0)
    container_memory_limit: str = "4g"
    merge_poll_seconds: float = Field(default=30.0, gt=0)
    operation_heartbeat_seconds: float = Field(default=15.0, ge=5.0)
    operation_stall_seconds: int = Field(default=300, ge=30)
    allowed_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]
    auth_mode: Literal["disabled", "github"] = "disabled"
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: SecretStr | None = None
    github_oauth_callback_url: str | None = None
    github_session_secret: SecretStr | None = None
    internal_secret: SecretStr | None = None
    github_allowed_user_ids: set[int] = Field(default_factory=lambda: set[int]())
    github_allowed_org: str | None = None
    github_session_hours: int = Field(default=12, ge=1, le=168)
    repository_owner: str | None = None
    github_app_id: int | None = Field(default=None, gt=0)
    github_app_installation_id: int | None = Field(default=None, gt=0)
    github_app_private_key_path: Path | None = None
    repository_action_name: str = "MAFIA repository agent"
    repository_action_email: str = "mafia@localhost"
    model_pairs: dict[str, str] = Field(
        default_factory=lambda: {
            "claude-opus-4.8": "gpt-5.6-sol",
            "gpt-5.6-sol": "claude-opus-4.8",
        }
    )

    @field_validator("model_pairs")
    @classmethod
    def validate_model_pairs(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("At least one model pair is required")
        normalized: dict[str, str] = {}
        for primary, reviewer in value.items():
            primary = primary.strip()
            reviewer = reviewer.strip()
            if not primary or not reviewer:
                raise ValueError("Model identifiers must not be empty")
            if len(primary) > 100 or len(reviewer) > 100:
                raise ValueError("Model identifiers must not exceed 100 characters")
            if primary == reviewer:
                raise ValueError("Primary and reviewer models must be different")
            if primary in normalized:
                raise ValueError(f"Duplicate primary model: {primary}")
            normalized[primary] = reviewer
        return normalized

    @field_validator("github_allowed_org")
    @classmethod
    def normalize_allowed_org(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 39 or not all(
            character.isalnum() or character == "-" for character in normalized
        ):
            raise ValueError("GitHub organization must be a valid organization login")
        return normalized

    @field_validator("repository_owner")
    @classmethod
    def normalize_repository_owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 39 or not all(
            character.isalnum() or character == "-" for character in normalized
        ):
            raise ValueError("Repository owner must be a valid GitHub login")
        return normalized

    @model_validator(mode="after")
    def validate_authentication(self) -> "Settings":
        if self.auth_mode == "github":
            required = {
                "github_oauth_client_id": self.github_oauth_client_id,
                "github_oauth_client_secret": self.github_oauth_client_secret,
                "github_oauth_callback_url": self.github_oauth_callback_url,
                "github_session_secret": self.github_session_secret,
                "internal_secret": self.internal_secret,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(
                    f"GitHub authentication requires settings: {', '.join(missing)}"
                )
            if not (self.github_oauth_client_id or "").strip():
                raise ValueError("github_oauth_client_id must not be empty")
            if (
                self.github_oauth_client_secret is None
                or not self.github_oauth_client_secret.get_secret_value()
            ):
                raise ValueError("github_oauth_client_secret must not be empty")
            for name, secret in (
                ("github_session_secret", self.github_session_secret),
                ("internal_secret", self.internal_secret),
            ):
                if secret is None or len(secret.get_secret_value()) < 32:
                    raise ValueError(f"{name} must contain at least 32 characters")
            if not self.github_allowed_user_ids and self.github_allowed_org is None:
                raise ValueError(
                    "GitHub authentication requires github_allowed_user_ids or "
                    "github_allowed_org"
                )
            if any(user_id <= 0 for user_id in self.github_allowed_user_ids):
                raise ValueError("GitHub allowed user IDs must be positive integers")
            callback = self.github_oauth_callback_url or ""
            if not callback.startswith("https://") and not callback.startswith(
                ("http://127.0.0.1:", "http://localhost:")
            ):
                raise ValueError(
                    "GitHub OAuth callback must use HTTPS or an HTTP loopback address"
                )
        github_app_values = (
            self.github_app_id,
            self.github_app_installation_id,
            self.github_app_private_key_path,
        )
        if any(value is not None for value in github_app_values):
            if not all(value is not None for value in github_app_values):
                raise ValueError(
                    "GitHub App authentication requires app ID, installation ID, "
                    "and private key path"
                )
            if self.repository_owner is None:
                raise ValueError(
                    "GitHub App authentication requires repository_owner"
                )
        if not self.repository_action_name.strip():
            raise ValueError("repository_action_name must not be empty")
        if "@" not in self.repository_action_email:
            raise ValueError("repository_action_email must be an email address")
        return self

    @property
    def required_models(self) -> set[str]:
        return set(self.model_pairs) | set(self.model_pairs.values())

    @property
    def github_app_enabled(self) -> bool:
        return (
            self.github_app_id is not None
            and self.github_app_installation_id is not None
            and self.github_app_private_key_path is not None
        )

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir.resolve() / 'mafia.db'}"

    @property
    def repositories_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def checkpoints_dir(self) -> Path:
        return self.data_dir / "checkpoints"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.repositories_dir,
            self.worktrees_dir,
            self.checkpoints_dir,
            self.projects_dir,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


@lru_cache
def get_settings() -> Settings:
    return Settings()

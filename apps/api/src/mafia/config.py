from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MAFIA_", extra="ignore")

    data_dir: Path = Path("data")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    command_timeout_seconds: float = Field(default=120.0, gt=0)
    command_output_limit: int = Field(default=1_000_000, gt=0)
    sandbox_process_limit: int = Field(default=128, ge=16, le=1024)
    execution_mode: Literal["isolated", "host"] = "isolated"
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

    @property
    def required_models(self) -> set[str]:
        return set(self.model_pairs) | set(self.model_pairs.values())

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

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.repositories_dir,
            self.worktrees_dir,
            self.checkpoints_dir,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


@lru_cache
def get_settings() -> Settings:
    return Settings()

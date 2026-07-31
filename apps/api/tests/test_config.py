import os
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from mafia.api import routes
from mafia.config import Settings
from pydantic import ValidationError
from pydantic_settings import (
    DotEnvSettingsSource,
    EnvSettingsSource,
    InitSettingsSource,
    SecretsSettingsSource,
)


@pytest.fixture(autouse=True)
def isolate_mafia_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("MAFIA_"):
            monkeypatch.delenv(name)


def settings_for_test(**values: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    return settings_factory(_env_file=None, _env_prefix="TEST_MAFIA_", **values)


def test_model_pairs_preserve_defaults_and_allow_overrides() -> None:
    defaults = settings_for_test()
    configured = settings_for_test(
        model_pairs={"claude-sonnet-5": "gpt-5.7"},
    )

    assert defaults.model_pairs["claude-opus-4.8"] == "gpt-5.6-sol"
    assert configured.model_pairs == {"claude-sonnet-5": "gpt-5.7"}
    assert configured.required_models == {"claude-sonnet-5", "gpt-5.7"}


def test_settings_have_no_checkpoint_directory() -> None:
    assert not hasattr(settings_for_test(), "checkpoints_dir")


def test_model_pairs_parse_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TEST_MAFIA_MODEL_PAIRS",
        '{"claude-sonnet-5":"gpt-5.7"}',
    )

    assert settings_for_test().model_pairs == {"claude-sonnet-5": "gpt-5.7"}


def test_explicit_model_pairs_replace_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MAFIA_MODEL_PAIRS", '{"old":"reviewer"}')

    settings = settings_for_test(model_pairs={"new": "peer"})

    assert settings.model_pairs == {"new": "peer"}


def test_filtered_settings_sources_have_distinct_names() -> None:
    sources = Settings.settings_customise_sources(
        Settings,
        init_settings=InitSettingsSource(Settings, {}),
        env_settings=EnvSettingsSource(Settings),
        dotenv_settings=DotEnvSettingsSource(Settings),
        file_secret_settings=SecretsSettingsSource(Settings),
    )

    assert sources[1].__name__ == "EnvSettingsSource"
    assert sources[2].__name__ == "DotEnvSettingsSource"


def test_production_environment_loads_unspecified_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAFIA_API_HOST", "0.0.0.0")

    assert Settings().api_host == "0.0.0.0"


def test_authentication_is_disabled_by_default() -> None:
    assert settings_for_test().auth_mode == "disabled"


def test_api_workers_rejects_multiple_processes() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"api_workers": 2})


def test_api_workers_rejects_multiple_processes_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MAFIA_API_WORKERS", "2")

    with pytest.raises(ValidationError):
        settings_for_test()


def test_github_app_requires_complete_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="requires app ID"):
        settings_for_test(github_app_id=123)

    settings = settings_for_test(
        repository_owner="Frostyard",
        github_app_id=123,
        github_app_installation_id=456,
        github_app_private_key_path=tmp_path / "app.pem",
    )

    assert settings.repository_owner == "Frostyard"
    assert settings.github_app_enabled is True


def test_repository_owner_rejects_invalid_github_owner() -> None:
    with pytest.raises(ValidationError):
        settings_for_test(repository_owner="not/an-owner")


def test_github_allowed_user_ids_parse_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MAFIA_GITHUB_ALLOWED_USER_IDS", "[37492,42]")

    assert settings_for_test().github_allowed_user_ids == {37492, 42}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_oauth_client_id", ""),
        ("github_oauth_client_secret", ""),
    ],
)
def test_github_auth_rejects_empty_oauth_credentials(
    field: str,
    value: str,
) -> None:
    settings: dict[str, object] = {
        "auth_mode": "github",
        "github_oauth_client_id": "client-id",
        "github_oauth_client_secret": "client-secret",
        "github_oauth_callback_url": "https://mafia.example/auth/callback",
        "github_session_secret": "s" * 32,
        "internal_secret": "i" * 32,
        "github_allowed_user_ids": {37492},
        field: value,
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(settings)


@pytest.mark.asyncio
async def test_models_index_exposes_configured_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for_test(model_pairs={"claude-sonnet-5": "gpt-5.7"})
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(
        routes.CopilotAgentService,
        "available_models",
        AsyncMock(return_value={"claude-sonnet-5"}),
    )

    response = await routes.models_index()

    assert response.pairs[0].model_dump() == {
        "primary_model": "claude-sonnet-5",
        "reviewer_model": "gpt-5.7",
    }
    assert response.available == ["claude-sonnet-5"]
    assert response.missing == ["gpt-5.7"]


@pytest.mark.parametrize(
    "pairs",
    [
        {},
        {"gpt-5.7": "gpt-5.7"},
        {" ": "claude-sonnet-5"},
        {"gpt-5.7": " "},
    ],
)
def test_model_pairs_reject_invalid_configuration(pairs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        settings_for_test(model_pairs=pairs)


def test_empty_model_pairs_reject_even_when_environment_supplies_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MAFIA_MODEL_PAIRS", '{"claude-sonnet-5":"gpt-5.7"}')

    with pytest.raises(ValidationError, match="At least one model pair is required"):
        settings_for_test(model_pairs={})

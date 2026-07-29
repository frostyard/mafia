from unittest.mock import AsyncMock

import pytest
from mafia.api import routes
from mafia.config import Settings
from pydantic import ValidationError


def test_model_pairs_preserve_defaults_and_allow_overrides() -> None:
    defaults = Settings()
    configured = Settings(
        model_pairs={"claude-sonnet-5": "gpt-5.7"},
    )

    assert defaults.model_pairs["claude-opus-4.8"] == "gpt-5.6-sol"
    assert configured.model_pairs == {"claude-sonnet-5": "gpt-5.7"}
    assert configured.required_models == {"claude-sonnet-5", "gpt-5.7"}


def test_model_pairs_parse_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MAFIA_MODEL_PAIRS",
        '{"claude-sonnet-5":"gpt-5.7"}',
    )

    assert Settings().model_pairs == {"claude-sonnet-5": "gpt-5.7"}


def test_authentication_is_disabled_by_default() -> None:
    assert Settings().auth_mode == "disabled"


def test_github_allowed_user_ids_parse_json_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAFIA_GITHUB_ALLOWED_USER_IDS", "[37492,42]")

    assert Settings().github_allowed_user_ids == {37492, 42}


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
    settings = Settings(model_pairs={"claude-sonnet-5": "gpt-5.7"})
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
        Settings(model_pairs=pairs)

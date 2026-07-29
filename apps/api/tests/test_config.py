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

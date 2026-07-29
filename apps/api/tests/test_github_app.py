from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mafia.config import Settings
from mafia.services.github_app import (
    GitHubAppAuthenticationError,
    GitHubAppTokenProvider,
)


def write_private_key(path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def app_settings(private_key_path: Path) -> Settings:
    return Settings(
        repository_owner="frostyard",
        github_app_id=123,
        github_app_installation_id=456,
        github_app_private_key_path=private_key_path,
    )


@pytest.mark.asyncio
async def test_installation_token_is_signed_and_cached(tmp_path: Path) -> None:
    key_path = tmp_path / "app.pem"
    write_private_key(key_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/app/installations/456/access_tokens"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(
            201,
            json={
                "token": "installation-token",
                "expires_at": (
                    datetime.now(UTC) + timedelta(hours=1)
                ).isoformat(),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GitHubAppTokenProvider(app_settings(key_path), client=client)
        assert await provider.token() == "installation-token"
        assert await provider.token() == "installation-token"

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_installation_token_refreshes_near_expiration(tmp_path: Path) -> None:
    key_path = tmp_path / "app.pem"
    write_private_key(key_path)
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            201,
            json={
                "token": f"token-{calls}",
                "expires_at": (
                    datetime.now(UTC) + timedelta(minutes=4)
                ).isoformat(),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GitHubAppTokenProvider(app_settings(key_path), client=client)
        assert await provider.token() == "token-1"
        assert await provider.token() == "token-2"


@pytest.mark.asyncio
async def test_installation_token_rejects_malformed_response(tmp_path: Path) -> None:
    key_path = tmp_path / "app.pem"
    write_private_key(key_path)

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"token": "missing-expiration"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GitHubAppTokenProvider(app_settings(key_path), client=client)
        with pytest.raises(GitHubAppAuthenticationError, match="incomplete"):
            await provider.token()

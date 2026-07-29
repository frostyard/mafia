import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import jwt
from anyio import Path
from mafia.config import Settings, get_settings


class GitHubAppAuthenticationError(RuntimeError):
    pass


class GitHubAppTokenProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)

    async def token(self) -> str:
        now = datetime.now(UTC)
        if self._token is not None and now < self._expires_at - timedelta(minutes=5):
            return self._token
        async with self._lock:
            now = datetime.now(UTC)
            if self._token is not None and now < self._expires_at - timedelta(minutes=5):
                return self._token
            token, expires_at = await self._request_token(now)
            self._token = token
            self._expires_at = expires_at
            return token

    async def _request_token(self, now: datetime) -> tuple[str, datetime]:
        settings = self.settings
        if not settings.github_app_enabled:
            raise GitHubAppAuthenticationError("GitHub App authentication is not configured")
        private_key_path = settings.github_app_private_key_path
        if private_key_path is None:
            raise GitHubAppAuthenticationError("GitHub App private key path is missing")
        try:
            private_key = await Path(private_key_path).read_text(encoding="utf-8")
        except OSError as error:
            raise GitHubAppAuthenticationError(
                f"Could not read GitHub App private key: {private_key_path}"
            ) from error
        app_id = settings.github_app_id
        installation_id = settings.github_app_installation_id
        if app_id is None or installation_id is None:
            raise GitHubAppAuthenticationError("GitHub App identifiers are incomplete")
        try:
            encoded_jwt = jwt.encode(
                {
                    "iat": int((now - timedelta(seconds=60)).timestamp()),
                    "exp": int((now + timedelta(minutes=9)).timestamp()),
                    "iss": str(app_id),
                },
                private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, ValueError) as error:
            raise GitHubAppAuthenticationError(
                "GitHub App private key is invalid"
            ) from error
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {encoded_jwt}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = (
            "https://api.github.com/app/installations/"
            f"{installation_id}/access_tokens"
        )
        if self.client is not None:
            response = await self.client.post(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=headers)
        if response.status_code != 201:
            raise GitHubAppAuthenticationError(
                "GitHub rejected the installation token request "
                f"with status {response.status_code}"
            )
        try:
            raw_payload: object = response.json()
        except ValueError as error:
            raise GitHubAppAuthenticationError(
                "GitHub returned an invalid installation token response"
            ) from error
        payload = (
            cast(dict[str, object], raw_payload)
            if isinstance(raw_payload, dict)
            else {}
        )
        token = payload.get("token")
        expires_at = payload.get("expires_at")
        if not isinstance(token, str) or not token or not isinstance(expires_at, str):
            raise GitHubAppAuthenticationError(
                "GitHub installation token response is incomplete"
            )
        try:
            expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise GitHubAppAuthenticationError(
                "GitHub installation token expiration is invalid"
            ) from error
        expiration = expiration.astimezone(UTC)
        if expiration <= now:
            raise GitHubAppAuthenticationError(
                "GitHub installation token is already expired"
            )
        return token, expiration


_provider: GitHubAppTokenProvider | None = None


async def github_app_token() -> str:
    global _provider
    if _provider is None:
        _provider = GitHubAppTokenProvider()
    return await _provider.token()

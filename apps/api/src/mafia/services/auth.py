import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from mafia.config import Settings
from pydantic import BaseModel, Field, ValidationError

SESSION_COOKIE = "mafia_session"
FLOW_COOKIE = "mafia_oauth_flow"
FLOW_TTL_SECONDS = 600


class AuthenticationError(ValueError):
    pass


class AuthorizationError(PermissionError):
    pass


class AuthenticatedUser(BaseModel):
    github_user_id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=100)
    avatar_url: str | None = None
    expires_at: int


class OAuthFlow(BaseModel):
    state: str
    code_verifier: str
    return_to: str
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def safe_return_to(value: str | None) -> str:
    if (
        value
        and value.startswith("/")
        and not value.startswith(("//", "/\\"))
        and "\\" not in value
    ):
        return value
    return "/"


@dataclass(frozen=True)
class SignedCookieCodec:
    secret: bytes

    @classmethod
    def from_settings(cls, settings: Settings) -> "SignedCookieCodec":
        if settings.github_session_secret is None:
            raise AuthenticationError("GitHub session secret is not configured")
        return cls(settings.github_session_secret.get_secret_value().encode())

    def encode(self, payload: BaseModel) -> str:
        body = _encode(
            json.dumps(
                payload.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        signature = _encode(
            hmac.digest(self.secret, body.encode(), hashlib.sha256)
        )
        return f"{body}.{signature}"

    def decode(self, value: str, model: type[BaseModel]) -> BaseModel:
        try:
            body, signature = value.split(".", 1)
            expected = _encode(
                hmac.digest(self.secret, body.encode(), hashlib.sha256)
            )
            if not hmac.compare_digest(signature, expected):
                raise AuthenticationError("Invalid signed cookie")
            payload = model.model_validate(json.loads(_decode(body)))
        except (
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise AuthenticationError("Invalid signed cookie") from error
        expires_at = getattr(payload, "expires_at", None)
        if not isinstance(expires_at, int) or expires_at <= int(time.time()):
            raise AuthenticationError("Signed cookie has expired")
        return payload

    def create_flow(self, return_to: str) -> tuple[OAuthFlow, str]:
        verifier = secrets.token_urlsafe(64)
        flow = OAuthFlow(
            state=secrets.token_urlsafe(32),
            code_verifier=verifier,
            return_to=safe_return_to(return_to),
            expires_at=int(time.time()) + FLOW_TTL_SECONDS,
        )
        return flow, self.encode(flow)

    def create_session(
        self,
        *,
        github_user_id: int,
        login: str,
        avatar_url: str | None,
        lifetime_hours: int,
    ) -> tuple[AuthenticatedUser, str]:
        user = AuthenticatedUser(
            github_user_id=github_user_id,
            login=login,
            avatar_url=avatar_url,
            expires_at=int(time.time()) + lifetime_hours * 3600,
        )
        return user, self.encode(user)

    def read_flow(self, value: str) -> OAuthFlow:
        return OAuthFlow.model_validate(self.decode(value, OAuthFlow))

    def read_session(self, value: str) -> AuthenticatedUser:
        return AuthenticatedUser.model_validate(
            self.decode(value, AuthenticatedUser)
        )


class GitHubOAuthService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client

    def authorization_url(self, flow: OAuthFlow) -> str:
        client_id = self.settings.github_oauth_client_id
        callback = self.settings.github_oauth_callback_url
        if client_id is None or callback is None:
            raise AuthenticationError("GitHub OAuth is not configured")
        challenge = _encode(hashlib.sha256(flow.code_verifier.encode()).digest())
        parameters = {
            "client_id": client_id,
            "redirect_uri": callback,
            "state": flow.state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self.settings.github_allowed_org is not None:
            parameters["scope"] = "read:org"
        return f"https://github.com/login/oauth/authorize?{urlencode(parameters)}"

    async def authenticate(self, code: str, flow: OAuthFlow) -> AuthenticatedUser:
        client_secret = self.settings.github_oauth_client_secret
        callback = self.settings.github_oauth_callback_url
        client_id = self.settings.github_oauth_client_id
        if client_secret is None or callback is None or client_id is None:
            raise AuthenticationError("GitHub OAuth is not configured")
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=15.0)
        try:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": callback,
                    "code_verifier": flow.code_verifier,
                },
            )
            token_response.raise_for_status()
            token = token_response.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise AuthenticationError("GitHub did not return an access token")
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            user_response = await client.get(
                "https://api.github.com/user",
                headers=headers,
            )
            user_response.raise_for_status()
            user_data = user_response.json()
            github_user_id = user_data.get("id")
            login = user_data.get("login")
            if (
                not isinstance(github_user_id, int)
                or github_user_id <= 0
                or not isinstance(login, str)
                or not login
            ):
                raise AuthenticationError("GitHub returned an invalid user profile")
            if not await self._is_authorized(
                client,
                headers,
                github_user_id=github_user_id,
            ):
                raise AuthorizationError(
                    "This GitHub account is not authorized for this MAFIA deployment"
                )
            avatar_url = user_data.get("avatar_url")
            expires_at = int(time.time()) + self.settings.github_session_hours * 3600
            return AuthenticatedUser(
                github_user_id=github_user_id,
                login=login,
                avatar_url=avatar_url if isinstance(avatar_url, str) else None,
                expires_at=expires_at,
            )
        except httpx.HTTPError as error:
            raise AuthenticationError("GitHub OAuth request failed") from error
        finally:
            if owns_client:
                await client.aclose()

    async def _is_authorized(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        *,
        github_user_id: int,
    ) -> bool:
        if github_user_id in self.settings.github_allowed_user_ids:
            return True
        organization = self.settings.github_allowed_org
        if organization is None:
            return False
        response = await client.get(
            f"https://api.github.com/user/memberships/orgs/{organization}",
            headers=headers,
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return response.json().get("state") == "active"


def cookie_options(*, max_age: int) -> dict[str, Any]:
    return {
        "httponly": True,
        "max_age": max_age,
        "path": "/",
        "samesite": "lax",
        "secure": True,
    }

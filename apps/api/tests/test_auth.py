import time

import httpx
import pytest
from fastapi import FastAPI
from mafia.api import auth as auth_routes
from mafia.config import Settings
from mafia.services.auth import (
    SESSION_COOKIE,
    AuthenticatedUser,
    AuthenticationError,
    AuthorizationError,
    GitHubOAuthService,
    SignedCookieCodec,
)
from mafia.services.auth_middleware import AuthenticationMiddleware
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_mode": "github",
        "github_oauth_client_id": "client-id",
        "github_oauth_client_secret": "client-secret",
        "github_oauth_callback_url": "https://mafia.example/auth/callback",
        "github_session_secret": "s" * 32,
        "internal_secret": "i" * 32,
        "github_allowed_user_ids": {37492},
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_github_auth_requires_an_authorization_policy() -> None:
    with pytest.raises(ValidationError, match="github_allowed_user_ids"):
        auth_settings(github_allowed_user_ids=set[int]())


def test_signed_session_rejects_tampering_and_expiration() -> None:
    codec = SignedCookieCodec.from_settings(auth_settings())
    user, cookie = codec.create_session(
        github_user_id=37492,
        login="bketelsen",
        avatar_url=None,
        lifetime_hours=1,
    )

    assert codec.read_session(cookie) == user
    with pytest.raises(AuthenticationError):
        codec.read_session(f"{cookie[:-1]}x")
    expired = AuthenticatedUser(
        github_user_id=37492,
        login="bketelsen",
        expires_at=int(time.time()) - 1,
    )
    with pytest.raises(AuthenticationError, match="expired"):
        codec.read_session(codec.encode(expired))


@pytest.mark.parametrize("return_to", ["//attacker.example", r"/\attacker.example"])
def test_user_allowlist_oauth_requests_no_scopes(return_to: str) -> None:
    settings = auth_settings()
    flow, _ = SignedCookieCodec.from_settings(settings).create_flow(return_to)
    url = GitHubOAuthService(settings).authorization_url(flow)

    assert "scope=" not in url
    assert flow.return_to == "/"


@pytest.mark.asyncio
async def test_oauth_accepts_active_optional_organization_membership() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "temporary"})
        if request.url.path == "/user":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "login": "octocat",
                    "avatar_url": "https://example/avatar",
                },
            )
        if request.url.path == "/user/memberships/orgs/frostyard":
            return httpx.Response(200, json={"state": "active"})
        raise AssertionError(request.url)

    settings = auth_settings(
        github_allowed_user_ids=set[int](),
        github_allowed_org="frostyard",
    )
    flow, _ = SignedCookieCodec.from_settings(settings).create_flow("/")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        user = await GitHubOAuthService(settings, client=client).authenticate(
            "oauth-code",
            flow,
        )

    assert user.github_user_id == 42
    assert any(
        request.url.path == "/user/memberships/orgs/frostyard"
        for request in requests
    )
    assert "scope=read%3Aorg" in GitHubOAuthService(settings).authorization_url(flow)


@pytest.mark.asyncio
async def test_oauth_rejects_inactive_organization_membership() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "temporary"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        if request.url.path == "/user/memberships/orgs/frostyard":
            return httpx.Response(200, json={"state": "pending"})
        raise AssertionError(request.url)

    settings = auth_settings(
        github_allowed_user_ids=set[int](),
        github_allowed_org="frostyard",
    )
    flow, _ = SignedCookieCodec.from_settings(settings).create_flow("/")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthorizationError):
            await GitHubOAuthService(settings, client=client).authenticate(
                "oauth-code",
                flow,
            )


@pytest.mark.asyncio
async def test_login_callback_session_and_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = auth_settings()
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)

    async def authenticate(
        _service: GitHubOAuthService,
        _code: str,
        _flow: object,
    ) -> AuthenticatedUser:
        return AuthenticatedUser(
            github_user_id=37492,
            login="bketelsen",
            expires_at=int(time.time()) + 3600,
        )

    monkeypatch.setattr(GitHubOAuthService, "authenticate", authenticate)
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, settings=settings)
    app.include_router(auth_routes.auth_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://mafia.example",
        follow_redirects=False,
    ) as client:
        login_response = await client.get(
            "/auth/login",
            params={"return_to": "/runs/new"},
        )
        assert login_response.status_code == 302
        location = httpx.URL(login_response.headers["location"])
        state = location.params["state"]
        assert location.host == "github.com"
        assert location.params["code_challenge_method"] == "S256"

        callback_response = await client.get(
            "/auth/callback",
            params={"code": "oauth-code", "state": state},
        )
        assert callback_response.status_code == 303
        assert callback_response.headers["location"] == "/runs/new"
        session_cookie = next(
            value
            for value in callback_response.headers.get_list("set-cookie")
            if value.startswith(f"{SESSION_COOKIE}=")
        )
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=lax" in session_cookie

        session_response = await client.get("/auth/session")
        assert session_response.json()["github_user_id"] == 37492
        assert session_response.headers["Cache-Control"] == "no-store"
        forward_response = await client.get("/auth/forward")
        assert forward_response.status_code == 204
        assert forward_response.headers["X-Mafia-GitHub-Login"] == "bketelsen"

        logout_response = await client.post("/auth/logout")
        assert logout_response.status_code == 303
        assert (await client.get("/auth/session")).status_code == 401
        denied_forward = await client.get(
            "/auth/forward",
            headers={"X-Forwarded-Uri": "/runs/new"},
        )
        assert denied_forward.status_code == 302
        assert "return_to=%2Fruns%2Fnew" in denied_forward.headers["location"]


@pytest.mark.asyncio
async def test_authentication_middleware_accepts_session_or_internal_secret() -> None:
    settings = auth_settings()
    codec = SignedCookieCodec.from_settings(settings)
    _, cookie = codec.create_session(
        github_user_id=37492,
        login="bketelsen",
        avatar_url=None,
        lifetime_hours=1,
    )

    async def private(request: Request) -> JSONResponse:
        user = getattr(request.state, "auth_user", None)
        return JSONResponse({"login": user.login if user else "internal"})

    async def public(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/private", private),
            Route("/healthz", public),
        ]
    )
    app.add_middleware(AuthenticationMiddleware, settings=settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://mafia.example",
    ) as client:
        assert (await client.get("/private")).status_code == 401
        assert (await client.get("/healthz")).status_code == 200
        client.cookies.set(SESSION_COOKIE, cookie)
        session_response = await client.get("/private")
        assert session_response.json() == {"login": "bketelsen"}
        client.cookies.clear()
        internal_response = await client.get(
            "/private",
            headers={"X-Mafia-Internal-Secret": "i" * 32},
        )
        assert internal_response.json() == {"login": "internal"}

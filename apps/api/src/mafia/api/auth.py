import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from mafia.config import get_settings
from mafia.services.auth import (
    FLOW_COOKIE,
    FLOW_TTL_SECONDS,
    SESSION_COOKIE,
    AuthenticatedUser,
    AuthenticationError,
    AuthorizationError,
    GitHubOAuthService,
    SignedCookieCodec,
    cookie_options,
    safe_return_to,
)

auth_router = APIRouter(prefix="/auth", tags=["authentication"])


def authenticated_user(request: Request) -> AuthenticatedUser:
    user = getattr(request.state, "auth_user", None)
    if not isinstance(user, AuthenticatedUser):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "Sign in required"},
        )
    return user


@auth_router.get("/login")
async def login(
    return_to: str = Query(default="/"),
) -> Response:
    settings = get_settings()
    if settings.auth_mode != "github":
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    codec = SignedCookieCodec.from_settings(settings)
    flow, cookie = codec.create_flow(return_to)
    response = RedirectResponse(
        url=GitHubOAuthService(settings).authorization_url(flow),
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        FLOW_COOKIE,
        cookie,
        **cookie_options(max_age=FLOW_TTL_SECONDS),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    oauth_error: str | None = Query(default=None, alias="error"),
) -> Response:
    settings = get_settings()
    if settings.auth_mode != "github":
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    if oauth_error is not None or code is None or state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "oauth_authorization_failed",
                "message": "GitHub authorization was not completed",
            },
        )
    codec = SignedCookieCodec.from_settings(settings)
    flow_cookie = request.cookies.get(FLOW_COOKIE)
    if flow_cookie is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "oauth_flow_missing", "message": "OAuth flow cookie is missing"},
        )
    try:
        flow = codec.read_flow(flow_cookie)
        if not secrets.compare_digest(flow.state, state):
            raise AuthenticationError("OAuth state does not match")
        user = await GitHubOAuthService(settings).authenticate(code, flow)
    except AuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "github_account_forbidden", "message": str(error)},
        ) from error
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "oauth_callback_invalid", "message": str(error)},
        ) from error
    response = RedirectResponse(
        url=flow.return_to,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(FLOW_COOKIE, path="/", secure=True, httponly=True)
    response.set_cookie(
        SESSION_COOKIE,
        codec.encode(user),
        **cookie_options(max_age=settings.github_session_hours * 3600),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_router.post("/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)
    response.delete_cookie(FLOW_COOKIE, path="/", secure=True, httponly=True)
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_router.get("/session", response_model=AuthenticatedUser)
async def session(request: Request, response: Response) -> AuthenticatedUser:
    response.headers["Cache-Control"] = "no-store"
    return authenticated_user(request)


@auth_router.get("/forward", status_code=status.HTTP_204_NO_CONTENT)
async def forward_auth(request: Request) -> Response:
    settings = get_settings()
    if settings.auth_mode != "github":
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    cookie = request.cookies.get(SESSION_COOKIE)
    try:
        user = (
            SignedCookieCodec.from_settings(settings).read_session(cookie)
            if cookie is not None
            else None
        )
    except AuthenticationError:
        user = None
    if user is None:
        return_to = safe_return_to(request.headers.get("X-Forwarded-Uri"))
        return RedirectResponse(
            url=f"/auth/login?{urlencode({'return_to': return_to})}",
            status_code=status.HTTP_302_FOUND,
        )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "Cache-Control": "no-store",
            "X-Mafia-GitHub-User-ID": str(user.github_user_id),
            "X-Mafia-GitHub-Login": user.login,
        },
    )

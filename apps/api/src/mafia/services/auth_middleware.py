import secrets
from http.cookies import SimpleCookie

from mafia.config import Settings
from mafia.services.auth import (
    SESSION_COOKIE,
    AuthenticatedUser,
    AuthenticationError,
    SignedCookieCodec,
)
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PUBLIC_PATHS = {
    "/auth/callback",
    "/auth/forward",
    "/auth/login",
    "/auth/logout",
    "/healthz",
}


class AuthenticationMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.codec = (
            SignedCookieCodec.from_settings(settings)
            if settings.auth_mode == "github"
            else None
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            self.settings.auth_mode == "disabled"
            or scope["type"] not in {"http", "websocket"}
            or scope.get("path") in PUBLIC_PATHS
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        user = self._session_user(headers.get("cookie"))
        if user is not None:
            scope.setdefault("state", {})["auth_user"] = user
            await self.app(scope, receive, send)
            return
        if self._valid_internal_secret(headers.get("x-mafia-internal-secret")):
            scope.setdefault("state", {})["auth_internal"] = True
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            message: Message = {"type": "websocket.close", "code": 4401}
            await send(message)
            return
        response = JSONResponse(
            {
                "detail": {
                    "code": "authentication_required",
                    "message": "A valid GitHub session is required",
                }
            },
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)

    def _session_user(self, cookie_header: str | None) -> AuthenticatedUser | None:
        if cookie_header is None or self.codec is None:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
            value = cookies.get(SESSION_COOKIE)
            return self.codec.read_session(value.value) if value is not None else None
        except (AuthenticationError, ValueError):
            return None

    def _valid_internal_secret(self, value: str | None) -> bool:
        configured = self.settings.internal_secret
        return (
            value is not None
            and configured is not None
            and secrets.compare_digest(value, configured.get_secret_value())
        )

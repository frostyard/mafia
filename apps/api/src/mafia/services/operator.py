from collections.abc import AsyncGenerator
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Request
from mafia.services.auth import AuthenticatedUser


@dataclass(frozen=True)
class OperatorIdentity:
    github_user_id: int
    login: str

    @property
    def actor(self) -> str:
        return f"github:{self.github_user_id}:{self.login}"


_operator: ContextVar[OperatorIdentity | None] = ContextVar(
    "mafia_operator",
    default=None,
)
_local_operator: ContextVar[bool] = ContextVar(
    "mafia_local_operator",
    default=False,
)


async def bind_request_operator(request: Request) -> AsyncGenerator[None]:
    user = getattr(request.state, "auth_user", None)
    forwarded = getattr(request.state, "auth_operator", None)
    if isinstance(user, AuthenticatedUser):
        operator_token = _operator.set(
            OperatorIdentity(
                github_user_id=user.github_user_id,
                login=user.login,
            )
        )
        local_token = _local_operator.set(False)
    elif isinstance(forwarded, OperatorIdentity):
        operator_token = _operator.set(forwarded)
        local_token = _local_operator.set(False)
    else:
        operator_token = _operator.set(None)
        local_token = _local_operator.set(True)
    try:
        yield
    finally:
        _operator.reset(operator_token)
        _local_operator.reset(local_token)


def current_actor() -> str:
    operator = _operator.get()
    if operator is not None:
        return operator.actor
    return "local-user" if _local_operator.get() else "system"

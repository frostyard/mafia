import re
from dataclasses import dataclass
from urllib.parse import urlparse

from mafia.config import Settings, get_settings
from mafia.db.models import Repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class InvalidRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryIdentity:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def remote_url(self) -> str:
        return f"https://github.com/{self.slug}.git"


def require_repository_owner(
    identity: RepositoryIdentity,
    settings: Settings | None = None,
) -> RepositoryIdentity:
    required_owner = (settings or get_settings()).repository_owner
    if (
        required_owner is not None
        and identity.owner.casefold() != required_owner.casefold()
    ):
        raise InvalidRepositoryError(
            f"Repository owner must be {required_owner!r}, got {identity.owner!r}"
        )
    return identity


def parse_repository(value: str) -> RepositoryIdentity:
    normalized = value.strip()
    if "://" in normalized:
        parsed = urlparse(normalized)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            raise InvalidRepositoryError("Only github.com repositories are supported")
        normalized = parsed.path.strip("/")
    elif normalized.startswith("git@github.com:"):
        normalized = normalized.removeprefix("git@github.com:")

    normalized = normalized.removesuffix(".git").strip("/")
    parts = normalized.split("/")
    if (
        len(parts) != 2
        or any(part in {".", ".."} for part in parts)
        or not all(SLUG_PATTERN.fullmatch(part) for part in parts)
    ):
        raise InvalidRepositoryError("Repository must be owner/name or a GitHub repository URL")
    return require_repository_owner(
        RepositoryIdentity(owner=parts[0], name=parts[1])
    )


async def get_or_create_repository(session: AsyncSession, identity: RepositoryIdentity) -> Repository:
    require_repository_owner(identity)
    repository = await session.scalar(
        select(Repository).where(Repository.owner == identity.owner, Repository.name == identity.name)
    )
    if repository is not None:
        return repository
    repository = Repository(
        owner=identity.owner,
        name=identity.name,
        remote_url=identity.remote_url,
    )
    session.add(repository)
    await session.flush()
    return repository


async def get_repository(session: AsyncSession, repository_id: str) -> Repository:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise LookupError(repository_id)
    require_repository_owner(RepositoryIdentity(repository.owner, repository.name))
    return repository


async def list_repositories(session: AsyncSession) -> list[Repository]:
    statement = select(Repository)
    repository_owner = get_settings().repository_owner
    if repository_owner is not None:
        statement = statement.where(Repository.owner.ilike(repository_owner))
    return list(await session.scalars(statement.order_by(Repository.owner, Repository.name)))

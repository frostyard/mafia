import re
from dataclasses import dataclass
from urllib.parse import urlparse

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
    if len(parts) != 2 or not all(SLUG_PATTERN.fullmatch(part) for part in parts):
        raise InvalidRepositoryError("Repository must be owner/name or a GitHub repository URL")
    return RepositoryIdentity(owner=parts[0], name=parts[1])


async def get_or_create_repository(session: AsyncSession, identity: RepositoryIdentity) -> Repository:
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

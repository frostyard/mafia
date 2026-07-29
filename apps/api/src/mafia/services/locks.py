from datetime import UTC, datetime, timedelta

from mafia.db.models import RepositoryLock, new_id
from mafia.db.session import SessionFactory
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError


class RepositoryBusyError(RuntimeError):
    pass


async def acquire_repository_lock(
    repository_id: str,
    run_id: str,
    phase_id: str,
    *,
    lease_hours: int = 4,
) -> str:
    token = new_id()
    async with SessionFactory() as session:
        existing = await session.get(RepositoryLock, repository_id)
        now = datetime.now(UTC)
        if existing is not None:
            expires_at = existing.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > now:
                raise RepositoryBusyError("Another phase is already executing for this repository")
            await session.delete(existing)
            await session.flush()
        session.add(
            RepositoryLock(
                repository_id=repository_id,
                run_id=run_id,
                phase_id=phase_id,
                lease_token=token,
                expires_at=now + timedelta(hours=lease_hours),
            )
        )
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise RepositoryBusyError("Another phase acquired the repository execution lease") from error
    return token


async def release_repository_lock(repository_id: str, token: str) -> None:
    async with SessionFactory() as session:
        await session.execute(
            delete(RepositoryLock).where(
                RepositoryLock.repository_id == repository_id,
                RepositoryLock.lease_token == token,
            )
        )
        await session.commit()

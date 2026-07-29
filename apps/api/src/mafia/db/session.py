from collections.abc import AsyncIterator
from typing import Protocol, cast

from mafia.config import get_settings
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

settings = get_settings()
engine = create_async_engine(settings.database_url)


class _SQLiteCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _SQLiteConnection(Protocol):
    def cursor(self) -> _SQLiteCursor: ...


def _configure_sqlite(dbapi_connection: object, _: object) -> None:
    cursor = cast(_SQLiteConnection, dbapi_connection).cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


if settings.database_url.startswith("sqlite"):
    event.listen(engine.sync_engine, "connect", _configure_sqlite)


SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session

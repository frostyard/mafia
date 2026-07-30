import pytest
from mafia.config import Settings
from mafia.db.base import Base
from mafia.db.models import Repository, Run
from mafia.services import repositories, runs
from mafia.services.repositories import InvalidRepositoryError, parse_repository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("octo/repo", "octo/repo"),
        ("https://github.com/octo/repo", "octo/repo"),
        ("git@github.com:octo/repo.git", "octo/repo"),
    ],
)
def test_parse_repository(value: str, expected: str) -> None:
    assert parse_repository(value).slug == expected


def test_rejects_non_github_repository() -> None:
    with pytest.raises(InvalidRepositoryError):
        parse_repository("https://example.com/octo/repo")


@pytest.mark.parametrize("value", ["../repo", "octo/..", "./repo", "octo/."])
def test_rejects_repository_path_components(value: str) -> None:
    with pytest.raises(InvalidRepositoryError):
        parse_repository(value)


def test_rejects_repository_outside_configured_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repositories,
        "get_settings",
        lambda: Settings(repository_owner="frostyard"),
    )

    assert parse_repository("frostyard/mafia").slug == "frostyard/mafia"
    with pytest.raises(InvalidRepositoryError, match="frostyard"):
        parse_repository("octo/repo")


@pytest.mark.asyncio
async def test_persisted_repository_is_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(repository_owner="frostyard")
    monkeypatch.setattr(repositories, "get_settings", lambda: settings)
    monkeypatch.setattr(runs, "get_settings", lambda: settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repository = Repository(
            owner="attacker",
            name="outside",
            remote_url="https://github.com/attacker/outside.git",
        )
        run = Run(
            repository=repository,
            requirement_text="Persisted before policy enforcement",
            primary_model="primary",
            reviewer_model="reviewer",
        )
        session.add(run)
        await session.commit()

        with pytest.raises(InvalidRepositoryError, match="frostyard"):
            await runs.get_run(session, run.id)
        assert await runs.list_runs(session) == []

    await engine.dispose()

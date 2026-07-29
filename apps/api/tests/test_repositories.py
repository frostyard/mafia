import pytest
from mafia.services.repositories import InvalidRepositoryError, parse_repository


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

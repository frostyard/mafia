from typing import Any

import pytest
from mafia.services.github import (
    GitHubDataError,
    get_issue,
    get_pull_request_context,
    get_repository_metadata,
    linked_issue_numbers,
    post_pull_request_comment,
)
from mafia.services.repositories import RepositoryIdentity


def test_linked_issues_stay_in_repository() -> None:
    identity = RepositoryIdentity("octo", "repo")
    numbers = linked_issue_numbers(
        identity,
        [
            "See #12, octo/repo#14, and https://github.com/octo/repo/issues/13.",
            "Ignore https://github.com/other/repo/issues/99.",
            "Version 2024 and commit 12345 are not issue references.",
        ],
    )
    assert numbers == {12, 13, 14}


@pytest.mark.asyncio
async def test_repository_metadata_rejects_mismatched_clone_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_gh_json(*_: str) -> dict[str, Any]:
        return {
        "default_branch": "main",
        "clone_url": "https://github.com/attacker/other.git",
        }

    monkeypatch.setattr("mafia.services.github.gh_json", fake_gh_json)

    with pytest.raises(GitHubDataError, match="does not match"):
        await get_repository_metadata(RepositoryIdentity("octo", "repo"))


@pytest.mark.asyncio
async def test_issue_comments_are_slurped_and_linked_resources_are_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    responses: list[dict[str, Any] | list[Any]] = [
        {"number": 1, "title": "Main", "body": "See #2", "state": "open"},
        [
            [
                {
                    "id": 10,
                    "body": "Related to octo/repo#3",
                    "html_url": "https://example.test/comment",
                    "user": {"login": "octocat"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        ],
        {"number": 2, "title": "Second", "body": "Details", "state": "open"},
        {"number": 3, "title": "Third", "body": "Details", "state": "closed"},
    ]

    async def fake_gh_json(*args: str) -> dict[str, Any] | list[Any]:
        calls.append(args)
        return responses.pop(0)

    monkeypatch.setattr("mafia.services.github.gh_json", fake_gh_json)
    issue = await get_issue(RepositoryIdentity("octo", "repo"), 1)

    assert issue["comments"][0]["user"] == "octocat"
    assert [item["number"] for item in issue["linked_resources"]] == [2, 3]
    assert "--paginate" in calls[1]
    assert "--slurp" in calls[1]


@pytest.mark.asyncio
async def test_pull_request_context_flattens_changed_file_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[dict[str, Any] | list[Any]] = [
        {
            "number": 42,
            "title": "Fix",
            "changed_files": 2,
            "head": {"sha": "b" * 40, "ref": "feature"},
            "base": {"sha": "a" * 40, "ref": "main"},
        },
        [
            [{"filename": "src/a.py", "status": "modified", "patch": "ignored"}],
            [{"filename": "src/b.py", "status": "added", "patch": "ignored"}],
        ],
    ]

    async def fake_gh_json(*_: str) -> dict[str, Any] | list[Any]:
        return responses.pop(0)

    monkeypatch.setattr("mafia.services.github.gh_json", fake_gh_json)

    context = await get_pull_request_context(
        RepositoryIdentity("octo", "repo"),
        42,
    )

    assert [file["filename"] for file in context["files"]] == [
        "src/a.py",
        "src/b.py",
    ]
    assert "patch" not in context["files"][0]


@pytest.mark.asyncio
async def test_pull_request_comment_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_gh_json(*args: str) -> dict[str, Any] | list[Any]:
        calls.append(args)
        return [
            [
                {
                    "body": "<!-- mafia-review:run-1:artifact-1 -->",
                    "html_url": "https://github.com/octo/repo/pull/42#issuecomment-1",
                }
            ]
        ]

    monkeypatch.setattr("mafia.services.github.gh_json", fake_gh_json)

    url = await post_pull_request_comment(
        RepositoryIdentity("octo", "repo"),
        42,
        run_id="run-1",
        artifact_id="artifact-1",
        markdown="# Review",
    )

    assert url.endswith("issuecomment-1")
    assert len(calls) == 1

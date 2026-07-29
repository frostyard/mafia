import json
import re
from dataclasses import dataclass
from typing import Any, cast

from mafia.services.commands import run_command
from mafia.services.repositories import (
    RepositoryIdentity,
    parse_repository,
    require_repository_owner,
)

LINK_PATTERN = re.compile(
    r"https://github\.com/(?P<url_owner>[A-Za-z0-9_.-]+)/(?P<url_repo>[A-Za-z0-9_.-]+)"
    r"/(?:issues|pull)/(?P<url_number>\d+)"
    r"|(?P<slug_owner>[A-Za-z0-9_.-]+)/(?P<slug_repo>[A-Za-z0-9_.-]+)#(?P<slug_number>\d+)"
    r"|(?<![\w/])#(?P<local_number>\d+)"
)


class GitHubDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepositoryMetadata:
    default_branch: str
    clone_url: str


def _slurped_items(value: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    raw: list[Any] = []
    if isinstance(value, list):
        for item in value:
            raw.extend(cast(list[Any], item) if isinstance(item, list) else [item])
    return [cast(dict[str, Any], item) for item in raw if isinstance(item, dict)]


async def gh_json(*args: str) -> dict[str, Any] | list[Any]:
    result = await run_command(("gh", *args))
    try:
        value: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GitHubDataError(f"GitHub CLI returned invalid JSON for {' '.join(args)}") from error
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if isinstance(value, list):
        return cast(list[Any], value)
    raise GitHubDataError(f"GitHub CLI returned unexpected JSON for {' '.join(args)}")


async def get_repository_metadata(identity: RepositoryIdentity) -> RepositoryMetadata:
    require_repository_owner(identity)
    data = await gh_json(
        "api",
        f"repos/{identity.slug}",
        "--jq",
        "{default_branch: .default_branch, clone_url: .clone_url}",
    )
    default_branch = data.get("default_branch") if isinstance(data, dict) else None
    clone_url = data.get("clone_url") if isinstance(data, dict) else None
    if not isinstance(default_branch, str):
        raise GitHubDataError(f"Repository metadata is incomplete for {identity.slug}")
    resolved_clone_url = (
        clone_url if isinstance(clone_url, str) else identity.remote_url
    )
    try:
        clone_identity = parse_repository(resolved_clone_url)
    except ValueError as error:
        raise GitHubDataError(
            f"Repository clone URL is invalid for {identity.slug}"
        ) from error
    if clone_identity.slug.casefold() != identity.slug.casefold():
        raise GitHubDataError(
            f"Repository clone URL does not match {identity.slug}"
        )
    return RepositoryMetadata(
        default_branch=default_branch,
        clone_url=resolved_clone_url,
    )


async def get_issue(identity: RepositoryIdentity, number: int) -> dict[str, Any]:
    require_repository_owner(identity)
    issue = await gh_json(
        "api",
        f"repos/{identity.slug}/issues/{number}",
        "--jq",
        "{number, title, body, state, html_url, labels: [.labels[].name], pull_request}",
    )
    comments = await gh_json(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{identity.slug}/issues/{number}/comments",
    )
    if not isinstance(issue, dict) or issue.get("number") != number:
        raise GitHubDataError(f"Issue {identity.slug}#{number} is unavailable")
    typed_issue = issue
    typed_comments: list[dict[str, Any]] = []
    for comment in _slurped_items(comments)[:100]:
        user_value = comment.get("user")
        user = cast(dict[str, Any], user_value) if isinstance(user_value, dict) else {}
        typed_comments.append(
            {
                "id": comment.get("id"),
                "body": comment.get("body"),
                "html_url": comment.get("html_url"),
                "user": user.get("login"),
                "created_at": comment.get("created_at"),
            }
        )
    typed_issue["comments"] = typed_comments
    linked_numbers = linked_issue_numbers(
        identity,
        [
            str(typed_issue.get("body") or ""),
            *(str(comment.get("body") or "") for comment in typed_comments),
        ],
    )
    linked_numbers.discard(number)
    linked_resources: list[dict[str, Any]] = []
    for linked_number in sorted(linked_numbers):
        linked = await gh_json(
            "api",
            f"repos/{identity.slug}/issues/{linked_number}",
            "--jq",
            "{number, title, body, state, html_url, labels: [.labels[].name], pull_request}",
        )
        if isinstance(linked, dict):
            linked_resources.append(linked)
    typed_issue["linked_resources"] = linked_resources
    return typed_issue


def linked_issue_numbers(
    identity: RepositoryIdentity,
    texts: list[str],
    *,
    limit: int = 20,
) -> set[int]:
    numbers: set[int] = set()
    for text in texts:
        for match in LINK_PATTERN.finditer(text):
            owner = match.group("url_owner") or match.group("slug_owner")
            repo = match.group("url_repo") or match.group("slug_repo")
            if owner and (
                owner.casefold() != identity.owner.casefold() or repo.casefold() != identity.name.casefold()
            ):
                continue
            number = (
                match.group("url_number")
                or match.group("slug_number")
                or match.group("local_number")
            )
            if number is None:
                continue
            numbers.add(int(number))
            if len(numbers) >= limit:
                return numbers
    return numbers


async def get_pr(identity: RepositoryIdentity, number: int) -> dict[str, Any]:
    require_repository_owner(identity)
    data = await gh_json(
        "api",
        f"repos/{identity.slug}/pulls/{number}",
        "--jq",
        "{number, state, merged, merged_at, merge_commit_sha, html_url, head: .head.ref, base: .base.ref}",
    )
    if not isinstance(data, dict):
        raise GitHubDataError(f"PR {identity.slug}#{number} is unavailable")
    return data


async def get_pull_request_context(
    identity: RepositoryIdentity,
    number: int,
) -> dict[str, Any]:
    require_repository_owner(identity)
    pull_request = await gh_json(
        "api",
        f"repos/{identity.slug}/pulls/{number}",
        "--jq",
        (
            "{number, title, body, state, draft, html_url, additions, deletions, "
            "changed_files, commits, labels: [.labels[].name], "
            "author: .user.login, "
            "head: {sha: .head.sha, ref: .head.ref, repository: .head.repo.full_name}, "
            "base: {sha: .base.sha, ref: .base.ref, repository: .base.repo.full_name}}"
        ),
    )
    files_response = await gh_json(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{identity.slug}/pulls/{number}/files?per_page=100",
        "--jq",
        (
            "[.[][] | {filename, previous_filename, status, sha, "
            "additions, deletions, changes}]"
        ),
    )
    if not isinstance(pull_request, dict) or pull_request.get("number") != number:
        raise GitHubDataError(f"PR {identity.slug}#{number} is unavailable")
    files = _slurped_items(files_response)
    changed_file_count = pull_request.get("changed_files")
    if isinstance(changed_file_count, int) and changed_file_count > len(files):
        raise GitHubDataError(
            f"PR {identity.slug}#{number} has {changed_file_count} changed files, "
            f"but only {len(files)} were returned"
        )
    pull_request["files"] = [
        {
            key: file.get(key)
            for key in (
                "filename",
                "previous_filename",
                "status",
                "sha",
                "additions",
                "deletions",
                "changes",
            )
        }
        for file in files
    ]
    return pull_request


async def post_pull_request_comment(
    identity: RepositoryIdentity,
    number: int,
    *,
    run_id: str,
    artifact_id: str,
    markdown: str,
) -> str:
    require_repository_owner(identity)
    marker = f"<!-- mafia-review:{run_id}:{artifact_id} -->"
    comments_response = await gh_json(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{identity.slug}/issues/{number}/comments?per_page=100",
        "--jq",
        "[.[][] | {body, html_url}]",
    )
    for comment in _slurped_items(comments_response):
        if marker not in str(comment.get("body") or ""):
            continue
        url = comment.get("html_url")
        if isinstance(url, str):
            return url
    body = f"{markdown[:60_000].rstrip()}\n\n{marker}"
    posted = await gh_json(
        "api",
        f"repos/{identity.slug}/issues/{number}/comments",
        "--method",
        "POST",
        "-f",
        f"body={body}",
    )
    url = posted.get("html_url") if isinstance(posted, dict) else None
    if not isinstance(url, str):
        raise GitHubDataError("GitHub did not return the posted review comment URL")
    return url

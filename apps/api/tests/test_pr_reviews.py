import subprocess
from pathlib import Path

import pytest
from mafia.domain.artifacts import PullRequestReview
from mafia.services.pr_reviews import (
    PullRequestReader,
    PullRequestReviewValidationError,
    validate_pull_request_review,
)


def commit(path: Path, message: str) -> str:
    subprocess.run(("git", "-C", str(path), "add", "--all"), check=True)
    subprocess.run(
        ("git", "-C", str(path), "commit", "-q", "-m", message),
        check=True,
    )
    return subprocess.run(
        ("git", "-C", str(path), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def review_at(line: int) -> PullRequestReview:
    return PullRequestReview.model_validate(
        {
            "summary": "Review",
            "verdict": "request_changes",
            "findings": [
                {
                    "id": "FIND-1",
                    "severity": "high",
                    "category": "correctness",
                    "title": "Wrong return value",
                    "description": "The changed function returns the wrong value.",
                    "file_path": "app.py",
                    "side": "new",
                    "line_start": line,
                    "line_end": line,
                    "evidence": "The return value changed without callers changing.",
                    "suggested_fix": "Restore the compatible return value.",
                }
            ],
            "strengths": [],
            "testing_assessment": "A regression test is needed.",
        }
    )


@pytest.fixture
def changed_repository(tmp_path: Path) -> tuple[Path, str, str]:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.name", "Test User"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(tmp_path),
            "config",
            "user.email",
            "test@example.invalid",
        ),
        check=True,
    )
    (tmp_path / "app.py").write_text(
        "def value():\n    enabled = True\n    return 'old'\n",
        encoding="utf-8",
    )
    base = commit(tmp_path, "base")
    (tmp_path / "app.py").write_text(
        "def value():\n    enabled = True\n    return 'new'\n",
        encoding="utf-8",
    )
    head = commit(tmp_path, "head")
    return tmp_path, base, head


@pytest.mark.asyncio
async def test_reader_exposes_diff_and_validates_changed_line(
    changed_repository: tuple[Path, str, str],
) -> None:
    root, base, head = changed_repository
    reader = PullRequestReader(
        root,
        base,
        head,
        [{"filename": "app.py", "status": "modified"}],
    )

    diff = await reader.read_pull_request_diff("app.py")
    await validate_pull_request_review(review_at(3), reader)

    assert "return 'new'" in diff
    assert reader.activity_snapshot()["diffs_inspected"] == ["app.py"]


@pytest.mark.asyncio
async def test_review_rejects_unchanged_line(
    changed_repository: tuple[Path, str, str],
) -> None:
    root, base, head = changed_repository
    reader = PullRequestReader(
        root,
        base,
        head,
        [{"filename": "app.py", "status": "modified"}],
    )

    with pytest.raises(PullRequestReviewValidationError, match="changed new line"):
        await validate_pull_request_review(review_at(2), reader)


@pytest.mark.asyncio
async def test_reader_uses_merge_base_when_base_branch_has_advanced(
    diverged_repository: tuple[Path, str, str],
) -> None:
    root, base_tip, head = diverged_repository
    reader = PullRequestReader(
        root,
        base_tip,
        head,
        [{"filename": "app.py", "status": "modified"}],
    )

    diff = await reader.read_pull_request_diff("app.py")
    base_file = await reader.read_base_file("app.py")

    assert "enabled = False" not in diff
    assert "2:     enabled = True" in base_file
    with pytest.raises(PullRequestReviewValidationError, match="changed new line"):
        await validate_pull_request_review(review_at(2), reader)


@pytest.fixture
def diverged_repository(
    tmp_path: Path,
) -> tuple[Path, str, str]:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.name", "Test User"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(tmp_path),
            "config",
            "user.email",
            "test@example.invalid",
        ),
        check=True,
    )
    (tmp_path / "app.py").write_text(
        "def value():\n    enabled = True\n    return 'old'\n",
        encoding="utf-8",
    )
    branch_point = commit(tmp_path, "branch point")
    subprocess.run(
        ("git", "-C", str(tmp_path), "branch", "feature", branch_point),
        check=True,
    )
    (tmp_path / "app.py").write_text(
        "def value():\n    enabled = False\n    return 'old'\n",
        encoding="utf-8",
    )
    base_tip = commit(tmp_path, "advance base")
    subprocess.run(
        ("git", "-C", str(tmp_path), "checkout", "-q", "feature"),
        check=True,
    )
    (tmp_path / "app.py").write_text(
        "def value():\n    enabled = True\n    return 'new'\n",
        encoding="utf-8",
    )
    head = commit(tmp_path, "feature change")
    return tmp_path, base_tip, head

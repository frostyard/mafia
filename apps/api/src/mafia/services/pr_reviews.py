import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from mafia.agents.copilot import CopilotAgentService
from mafia.agents.prompts import (
    PULL_REQUEST_ADJUDICATION_INSTRUCTIONS,
    PULL_REQUEST_REVIEW_INSTRUCTIONS,
)
from mafia.db.models import Artifact, Evidence, Run, SourceSnapshot
from mafia.db.session import SessionFactory
from mafia.domain.artifacts import (
    ConsolidatedPullRequestReview,
    PullRequestFinding,
    PullRequestReview,
    pull_request_review_markdown,
)
from mafia.domain.enums import ArtifactKind
from mafia.services.artifacts import persist_artifact
from mafia.services.commands import run_command
from mafia.services.operations import tracked_operation
from mafia.services.source import SourceActivity, SourcePathError, SourceReader, resolve_in_root
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

HUNK_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class PullRequestReviewValidationError(ValueError):
    pass


@dataclass
class PullRequestReader:
    root: Path
    base_sha: str
    head_sha: str
    files: list[dict[str, Any]]
    source: SourceReader = field(init=False)
    diff_paths: set[str] = field(default_factory=lambda: set[str]())
    _range_cache: dict[str, dict[str, list[tuple[int, int]]]] = field(
        default_factory=lambda: dict[str, dict[str, list[tuple[int, int]]]]()
    )
    _merge_base_sha: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.source = SourceReader(self.root, SourceActivity())

    @property
    def changed_paths(self) -> set[str]:
        return {
            path
            for file in self.files
            for key in ("filename", "previous_filename")
            if isinstance((path := file.get(key)), str)
        }

    def list_source(self, path: str = ".") -> list[str]:
        """List files in the pull-request head."""
        return self.source.list_source(path)

    def read_source(
        self,
        path: str,
        line_start: int = 1,
        line_end: int = 400,
    ) -> str:
        """Read a file from the pull-request head with numbered lines."""
        return self.source.read_source(path, line_start, line_end)

    def search_source(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Search files in the pull-request head."""
        return self.source.search_source(query, limit)

    def base_path(self, path: str) -> str:
        return next(
            (
                str(file.get("previous_filename"))
                for file in self.files
                if file.get("filename") == path
                and isinstance(file.get("previous_filename"), str)
            ),
            path,
        )

    async def merge_base(self) -> str:
        if self._merge_base_sha is None:
            result = await run_command(
                (
                    "git",
                    "-C",
                    str(self.root),
                    "merge-base",
                    self.base_sha,
                    self.head_sha,
                )
            )
            self._merge_base_sha = result.stdout.strip()
            if not self._merge_base_sha:
                raise PullRequestReviewValidationError(
                    "Pull-request base and head have no merge base"
                )
        return self._merge_base_sha

    async def read_pull_request_diff(
        self,
        path: str,
        line_start: int = 1,
        line_end: int = 500,
    ) -> str:
        """Read numbered lines from one changed file's unified diff."""
        if path not in self.changed_paths:
            raise SourcePathError(f"Not a changed pull-request path: {path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 1_000:
            raise SourcePathError("Invalid diff line range")
        self.diff_paths.add(path)
        merge_base = await self.merge_base()
        result = await run_command(
            (
                "git",
                "-C",
                str(self.root),
                "diff",
                "--no-ext-diff",
                "--find-renames",
                "--unified=40",
                merge_base,
                self.head_sha,
                "--",
                path,
            )
        )
        lines = result.stdout.splitlines()
        return "\n".join(
            f"{number}: {line}"
            for number, line in enumerate(
                lines[line_start - 1 : line_end],
                line_start,
            )
        )

    async def read_base_file(
        self,
        path: str,
        line_start: int = 1,
        line_end: int = 400,
    ) -> str:
        """Read a file from the pull-request base with numbered lines."""
        if path not in self.changed_paths:
            raise SourcePathError(f"Not a changed pull-request path: {path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 1_000:
            raise SourcePathError("Invalid line range")
        base_path = self.base_path(path)
        merge_base = await self.merge_base()
        result = await run_command(
            ("git", "-C", str(self.root), "show", f"{merge_base}:{base_path}"),
            check=False,
        )
        if result.returncode != 0:
            raise SourcePathError(f"File does not exist at pull-request base: {path}")
        lines = result.stdout.splitlines()
        return "\n".join(
            f"{number}: {line}"
            for number, line in enumerate(
                lines[line_start - 1 : line_end],
                line_start,
            )
        )

    async def changed_line_ranges(
        self,
        path: str,
    ) -> dict[str, list[tuple[int, int]]]:
        cached = self._range_cache.get(path)
        if cached is not None:
            return cached
        if path not in self.changed_paths:
            raise PullRequestReviewValidationError(f"Finding path is not changed: {path}")
        merge_base = await self.merge_base()
        result = await run_command(
            (
                "git",
                "-C",
                str(self.root),
                "diff",
                "--no-ext-diff",
                "--find-renames",
                "--unified=0",
                merge_base,
                self.head_sha,
                "--",
                path,
            )
        )
        ranges: dict[str, list[tuple[int, int]]] = {"old": [], "new": []}
        for line in result.stdout.splitlines():
            match = HUNK_PATTERN.match(line)
            if match is None:
                continue
            for side in ("old", "new"):
                start = int(match.group(f"{side}_start"))
                count_value = match.group(f"{side}_count")
                count = int(count_value) if count_value is not None else 1
                if count > 0:
                    ranges[side].append((start, start + count - 1))
        self._range_cache[path] = ranges
        return ranges

    def activity_snapshot(self) -> dict[str, Any]:
        return {
            **self.source.activity.snapshot(),
            "diffs_inspected": sorted(self.diff_paths)[:200],
            "diffs_inspected_count": len(self.diff_paths),
            "merge_base_sha": self._merge_base_sha,
        }


def pull_request_tools(reader: PullRequestReader) -> list[Any]:
    return [
        reader.list_source,
        reader.read_source,
        reader.search_source,
        reader.read_pull_request_diff,
        reader.read_base_file,
    ]


async def validate_pull_request_review(
    review: PullRequestReview,
    reader: PullRequestReader,
) -> None:
    for finding in review.findings:
        ranges = await reader.changed_line_ranges(finding.file_path)
        if not any(
            finding.line_start <= end and finding.line_end >= start
            for start, end in ranges[finding.side]
        ):
            raise PullRequestReviewValidationError(
                f"Finding {finding.id} does not cite a changed {finding.side} line in "
                f"{finding.file_path}"
            )
        if finding.side == "new":
            path = resolve_in_root(reader.root, finding.file_path)
            if not path.is_file():
                raise PullRequestReviewValidationError(
                    f"Finding {finding.id} does not cite a file at the pull-request head"
                )
            line_count = len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
            if finding.line_end > line_count:
                raise PullRequestReviewValidationError(
                    f"Finding {finding.id} exceeds {finding.file_path}'s line count"
                )
        else:
            merge_base = await reader.merge_base()
            base_file = await run_command(
                (
                    "git",
                    "-C",
                    str(reader.root),
                    "show",
                    f"{merge_base}:{reader.base_path(finding.file_path)}",
                ),
                check=False,
            )
            if (
                base_file.returncode != 0
                or finding.line_end > len(base_file.stdout.splitlines())
            ):
                raise PullRequestReviewValidationError(
                    f"Finding {finding.id} exceeds the base file's line count"
                )


async def persist_pull_request_findings(
    session: AsyncSession,
    snapshot: SourceSnapshot,
    reader: PullRequestReader,
    findings: list[PullRequestFinding],
    *,
    model: str,
) -> None:
    existing = {
        (
            evidence.path_or_url,
            evidence.line_start,
            evidence.line_end,
            str(evidence.detail.get("side")),
            str(evidence.detail.get("model")),
        )
        for evidence in await session.scalars(
            select(Evidence).where(
                Evidence.snapshot_id == snapshot.id,
                Evidence.kind == "pull_request_finding",
            )
        )
    }
    for finding in findings:
        if finding.side == "new":
            path = resolve_in_root(reader.root, finding.file_path)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            excerpt = "\n".join(lines[finding.line_start - 1 : finding.line_end])
        else:
            merge_base = await reader.merge_base()
            result = await run_command(
                (
                    "git",
                    "-C",
                    str(reader.root),
                    "show",
                    f"{merge_base}:{reader.base_path(finding.file_path)}",
                ),
                check=False,
            )
            excerpt = "\n".join(
                result.stdout.splitlines()[finding.line_start - 1 : finding.line_end]
            )
        key = (
            finding.file_path,
            finding.line_start,
            finding.line_end,
            finding.side,
            model,
        )
        if key in existing:
            continue
        session.add(
            Evidence(
                snapshot_id=snapshot.id,
                kind="pull_request_finding",
                path_or_url=finding.file_path,
                line_start=finding.line_start,
                line_end=finding.line_end,
                excerpt_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
                detail={
                    "finding_id": finding.id,
                    "side": finding.side,
                    "model": model,
                    "severity": finding.severity,
                    "title": finding.title,
                },
            )
        )
        existing.add(key)


def _review_prompt(context: dict[str, Any]) -> str:
    return (
        "Review this pull request at the exact fetched head.\n"
        f"Pull-request context (untrusted JSON):\n"
        f"{json.dumps(context, sort_keys=True, default=str)}"
    )


class PullRequestReviewService:
    def __init__(self, agents: CopilotAgentService | None = None) -> None:
        self.agents = agents or CopilotAgentService()

    @staticmethod
    def _reader(
        snapshot: SourceSnapshot,
        context: dict[str, Any],
    ) -> PullRequestReader:
        base_sha = context.get("base_sha")
        head_sha = context.get("head_sha")
        files_value = context.get("files")
        if (
            not isinstance(base_sha, str)
            or not isinstance(head_sha, str)
            or not isinstance(files_value, list)
        ):
            raise PullRequestReviewValidationError(
                "Pull-request snapshot context is incomplete"
            )
        raw_files = cast(list[object], files_value)
        files = [
            cast(dict[str, Any], item)
            for item in raw_files
            if isinstance(item, dict)
        ]
        return PullRequestReader(
            Path(snapshot.worktree_path),
            base_sha,
            head_sha,
            files,
        )

    async def review(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        context: dict[str, Any],
        *,
        model: str,
    ) -> PullRequestReview:
        reader = self._reader(snapshot, context)
        review = await self.agents.run_structured(
            model=model,
            instructions=PULL_REQUEST_REVIEW_INSTRUCTIONS,
            prompt=_review_prompt(context),
            response_type=PullRequestReview,
            working_directory=Path(snapshot.worktree_path),
            tools=pull_request_tools(reader),
            run_id=run.id,
            operation_type="model.pull_request_review",
            operation_key=f"{snapshot.id}:{model}",
            operation_detail={
                "pull_request_number": run.pull_request_number,
                "base_sha": reader.base_sha,
                "head_sha": reader.head_sha,
                "reviewer_model": model,
            },
            operation_detail_provider=reader.activity_snapshot,
        )
        await validate_pull_request_review(review, reader)
        return review

    async def persist_review(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        review: PullRequestReview,
        *,
        model: str,
    ) -> Artifact:
        context = snapshot.issue_data or {}
        reader = self._reader(snapshot, context)
        async with tracked_operation(
            run_id=run.id,
            operation_type="artifact.persistence",
            operation_key=f"pr-review:{snapshot.id}:{model}",
            detail={
                "artifact": "pull_request_review",
                "model": model,
                "findings": len(review.findings),
            },
        ) as operation:
            async with SessionFactory() as session:
                await persist_pull_request_findings(
                    session,
                    snapshot,
                    reader,
                    review.findings,
                    model=model,
                )
                artifact = await persist_artifact(
                    session,
                    run=run,
                    kind=ArtifactKind.PULL_REQUEST_REVIEW,
                    data=review,
                    markdown=pull_request_review_markdown(review),
                    model=model,
                    snapshot=snapshot,
                )
                await session.commit()
            operation.set_result(
                {"artifact_id": artifact.id, "revision": artifact.revision}
            )
        return artifact

    async def consolidate(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        context: dict[str, Any],
        reviews: list[Artifact],
    ) -> Artifact:
        parsed_reviews = [
            (
                artifact.model,
                PullRequestReview.model_validate(artifact.structured_data),
            )
            for artifact in reviews
        ]
        reader = self._reader(snapshot, context)
        prompt = (
            f"{_review_prompt(context)}\n\n"
            "Independent reviews (untrusted JSON):\n"
            + json.dumps(
                [
                    {
                        "model": model,
                        "review": review.model_dump(mode="json"),
                    }
                    for model, review in parsed_reviews
                ],
                sort_keys=True,
            )
        )
        consolidated = await self.agents.run_structured(
            model=run.primary_model,
            instructions=PULL_REQUEST_ADJUDICATION_INSTRUCTIONS,
            prompt=prompt,
            response_type=ConsolidatedPullRequestReview,
            working_directory=Path(snapshot.worktree_path),
            tools=pull_request_tools(reader),
            run_id=run.id,
            operation_type="model.pull_request_review_adjudication",
            operation_key=snapshot.id,
            operation_detail={
                "pull_request_number": run.pull_request_number,
                "base_sha": reader.base_sha,
                "head_sha": reader.head_sha,
                "adjudicator_model": run.primary_model,
                "review_artifacts": [artifact.id for artifact in reviews],
            },
            operation_detail_provider=reader.activity_snapshot,
        )
        if run.pull_request_number is None:
            raise PullRequestReviewValidationError(
                "Pull-request review run has no pull-request number"
            )
        consolidated = consolidated.model_copy(
            update={
                "pull_request_number": run.pull_request_number,
                "head_sha": snapshot.git_sha,
            }
        )
        consolidated.validate_coverage(parsed_reviews)
        review_projection = PullRequestReview(
            summary=consolidated.summary,
            verdict=consolidated.verdict,
            findings=consolidated.findings,
            strengths=consolidated.strengths,
            testing_assessment=consolidated.testing_assessment,
        )
        await validate_pull_request_review(review_projection, reader)
        async with tracked_operation(
            run_id=run.id,
            operation_type="artifact.persistence",
            operation_key=f"pr-review-final:{snapshot.id}",
            detail={
                "artifact": "pull_request_review_consolidated",
                "findings": len(consolidated.findings),
                "dispositions": len(consolidated.dispositions),
            },
        ) as operation:
            async with SessionFactory() as session:
                await persist_pull_request_findings(
                    session,
                    snapshot,
                    reader,
                    consolidated.findings,
                    model=f"{run.primary_model}:adjudicated",
                )
                artifact = await persist_artifact(
                    session,
                    run=run,
                    kind=ArtifactKind.PULL_REQUEST_REVIEW_CONSOLIDATED,
                    data=consolidated,
                    markdown=pull_request_review_markdown(consolidated),
                    model=run.primary_model,
                    snapshot=snapshot,
                )
                await session.commit()
            operation.set_result(
                {"artifact_id": artifact.id, "revision": artifact.revision}
            )
        return artifact

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mafia.agents.copilot import CopilotAgentService
from mafia.agents.prompts import (
    IMPLEMENTATION_ADJUDICATION_INSTRUCTIONS,
    IMPLEMENTATION_REMEDIATION_INSTRUCTIONS,
    IMPLEMENTATION_REVIEW_INSTRUCTIONS,
    REMEDIATION_VERIFICATION_INSTRUCTIONS,
)
from mafia.db.models import Artifact, Phase, Run
from mafia.db.session import SessionFactory
from mafia.domain.artifacts import (
    ImplementationFinding,
    ImplementationRemediationReport,
    ImplementationReview,
    ImplementationReviewLedger,
    RemediationRegression,
    RemediationVerification,
    implementation_review_ledger_markdown,
    implementation_review_markdown,
    remediation_report_markdown,
    remediation_verification_markdown,
)
from mafia.domain.enums import ArtifactKind
from mafia.services.artifacts import persist_artifact
from mafia.services.commands import run_command
from mafia.services.operations import OperationDetailProvider, tracked_operation
from mafia.services.source import SourceActivity, SourcePathError, SourceReader, resolve_in_root
from sqlalchemy import update

HUNK_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class ImplementationReviewGateError(RuntimeError):
    pass


class ImplementationReviewValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateDiff:
    base_sha: str
    diff_hash: str
    changed_files: list[str]
    canonical_diff: str
    previous_paths: dict[str, str]


async def capture_staged_candidate(worktree: Path) -> CandidateDiff:
    await run_command(("git", "-C", str(worktree), "add", "--all"))
    base_sha = (await run_command(("git", "-C", str(worktree), "rev-parse", "HEAD"))).stdout.strip()
    diff = await run_command(
        ("git", "-C", str(worktree), "diff", "--cached", "--find-renames", "HEAD")
    )
    if not diff.stdout:
        raise ImplementationReviewGateError("The staged implementation candidate is empty")
    names = await run_command(
        (
            "git",
            "-C",
            str(worktree),
            "diff",
            "--cached",
            "--find-renames",
            "--name-only",
            "-z",
            "HEAD",
        )
    )
    changed_files = sorted(path for path in names.stdout.split("\x00") if path)
    statuses = await run_command(
        (
            "git",
            "-C",
            str(worktree),
            "diff",
            "--cached",
            "--name-status",
            "-z",
            "--find-renames",
            "HEAD",
        )
    )
    previous_paths: dict[str, str] = {}
    values = [value for value in statuses.stdout.split("\x00") if value]
    index = 0
    while index < len(values):
        status = values[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(values):
                raise ImplementationReviewValidationError("Invalid staged rename metadata")
            old_path, new_path = values[index], values[index + 1]
            previous_paths[new_path] = old_path
            index += 2
        else:
            if index >= len(values):
                raise ImplementationReviewValidationError("Invalid staged change metadata")
            path = values[index]
            previous_paths[path] = path
            index += 1
    return CandidateDiff(
        base_sha=base_sha,
        diff_hash=hashlib.sha256(diff.stdout.encode()).hexdigest(),
        changed_files=changed_files,
        canonical_diff=diff.stdout,
        previous_paths=previous_paths,
    )


@dataclass
class CandidateDiffReader:
    root: Path
    candidate: CandidateDiff
    original_candidate: CandidateDiff | None = None
    source: SourceReader = field(init=False)
    diff_paths: set[str] = field(default_factory=lambda: set[str]())
    original_diff_paths: set[str] = field(default_factory=lambda: set[str]())
    _range_cache: dict[str, dict[str, list[tuple[int, int]]]] = field(
        default_factory=lambda: dict[str, dict[str, list[tuple[int, int]]]]()
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.source = SourceReader(self.root, SourceActivity())

    @property
    def changed_paths(self) -> set[str]:
        return set(self.candidate.changed_files) | set(self.candidate.previous_paths.values())

    def list_source(self, path: str = ".") -> list[str]:
        """List files in the current implementation candidate."""
        return self.source.list_source(path)

    def read_source(self, path: str, line_start: int = 1, line_end: int = 400) -> str:
        """Read a current candidate file with numbered lines."""
        return self.source.read_source(path, line_start, line_end)

    def search_source(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Search current candidate files."""
        return self.source.search_source(query, limit)

    async def read_candidate_diff(self, path: str, line_start: int = 1, line_end: int = 500) -> str:
        """Read numbered lines from one file in the canonical staged candidate diff."""
        if path not in self.changed_paths:
            raise SourcePathError(f"Not a changed candidate path: {path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 1_000:
            raise SourcePathError("Invalid diff line range")
        self.diff_paths.add(path)
        result = await run_command(
            (
                "git",
                "-C",
                str(self.root),
                "diff",
                "--cached",
                "--find-renames",
                "HEAD",
                "--",
                path,
            )
        )
        lines = result.stdout.splitlines()
        return "\n".join(
            f"{number}: {line}" for number, line in enumerate(lines[line_start - 1 : line_end], line_start)
        )

    async def read_original_candidate_diff(self, path: str, line_start: int = 1, line_end: int = 500) -> str:
        """Read numbered lines from the pre-remediation canonical candidate diff."""
        if self.original_candidate is None:
            raise SourcePathError("No original candidate diff is available")
        if path not in set(self.original_candidate.changed_files):
            raise SourcePathError(f"Not an originally changed candidate path: {path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 1_000:
            raise SourcePathError("Invalid diff line range")
        self.original_diff_paths.add(path)
        lines = self.original_candidate.canonical_diff.splitlines()
        selected: list[str] = []
        in_file = False
        for line in lines:
            if line.startswith("diff --git "):
                in_file = line.endswith(f" b/{path}") or f" a/{path} " in line
            if in_file:
                selected.append(line)
        return "\n".join(
            f"{number}: {line}" for number, line in enumerate(selected[line_start - 1 : line_end], line_start)
        )

    async def read_base_file(self, path: str, line_start: int = 1, line_end: int = 400) -> str:
        """Read a changed file from HEAD before the candidate, with numbered lines."""
        if path not in self.changed_paths:
            raise SourcePathError(f"Not a changed candidate path: {path}")
        if line_start < 1 or line_end < line_start or line_end - line_start > 1_000:
            raise SourcePathError("Invalid line range")
        base_path = self.candidate.previous_paths.get(path, path)
        result = await run_command(("git", "-C", str(self.root), "show", f"HEAD:{base_path}"), check=False)
        if result.returncode != 0:
            raise SourcePathError(f"File does not exist at candidate base: {path}")
        lines = result.stdout.splitlines()
        return "\n".join(
            f"{number}: {line}" for number, line in enumerate(lines[line_start - 1 : line_end], line_start)
        )

    async def changed_line_ranges(self, path: str) -> dict[str, list[tuple[int, int]]]:
        cached = self._range_cache.get(path)
        if cached is not None:
            return cached
        if path not in self.changed_paths:
            raise ImplementationReviewValidationError(f"Finding path is not changed: {path}")
        result = await run_command(
            (
                "git",
                "-C",
                str(self.root),
                "diff",
                "--cached",
                "--find-renames",
                "--unified=0",
                "HEAD",
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
            "original_diffs_inspected": sorted(self.original_diff_paths)[:200],
            "candidate_diff_hash": self.candidate.diff_hash,
            "candidate_base_sha": self.candidate.base_sha,
        }


def candidate_read_tools(reader: CandidateDiffReader, *, include_original: bool = False) -> list[Any]:
    tools = [
        reader.list_source,
        reader.read_source,
        reader.search_source,
        reader.read_candidate_diff,
        reader.read_base_file,
    ]
    if include_original:
        tools.append(reader.read_original_candidate_diff)
    return tools


async def _validate_location(
    finding: ImplementationFinding | RemediationRegression,
    reader: CandidateDiffReader,
) -> None:
    location = finding.location
    ranges = await reader.changed_line_ranges(location.file_path)
    if not any(
        location.line_start <= end and location.line_end >= start for start, end in ranges[location.side]
    ):
        raise ImplementationReviewValidationError(
            f"Finding {finding.id} does not cite a changed {location.side} line in {location.file_path}"
        )
    if location.side == "new":
        try:
            path = resolve_in_root(reader.root, location.file_path)
        except SourcePathError as error:
            raise ImplementationReviewValidationError(
                f"Finding {finding.id} does not cite a file in the candidate"
            ) from error
        if not path.is_file():
            raise ImplementationReviewValidationError(
                f"Finding {finding.id} does not cite a file in the candidate"
            )
        line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    else:
        base_path = reader.candidate.previous_paths.get(location.file_path, location.file_path)
        result = await run_command(("git", "-C", str(reader.root), "show", f"HEAD:{base_path}"), check=False)
        if result.returncode != 0:
            raise ImplementationReviewValidationError(
                f"Finding {finding.id} does not cite a file at the candidate base"
            )
        line_count = len(result.stdout.splitlines())
    if location.line_end > line_count:
        raise ImplementationReviewValidationError(
            f"Finding {finding.id} exceeds {location.file_path}'s {location.side} line count"
        )


async def validate_implementation_review(review: ImplementationReview, reader: CandidateDiffReader) -> None:
    missing_diff_inspection = set(reader.candidate.changed_files) - reader.diff_paths
    if missing_diff_inspection:
        raise ImplementationReviewValidationError(
            "Implementation review did not inspect every changed file diff: "
            + ", ".join(sorted(missing_diff_inspection))
        )
    for finding in review.findings:
        await _validate_location(finding, reader)


async def validate_remediation_verification(
    verification: RemediationVerification, reader: CandidateDiffReader
) -> None:
    for regression in verification.regressions:
        await _validate_location(regression, reader)


def required_remediation_ids(review: ImplementationReview, ledger: ImplementationReviewLedger) -> list[str]:
    unresolved = ledger.unresolved_blocker_major_ids(review)
    if unresolved:
        raise ImplementationReviewGateError(
            "Implementation review left blocker/major findings unresolved: " + ", ".join(unresolved)
        )
    return ledger.accepted_blocker_major_ids(review)


def require_remediation_verified(verification: RemediationVerification) -> None:
    unresolved = verification.unresolved_ids()
    if not unresolved and not verification.regressions:
        return
    details: list[str] = []
    if unresolved:
        details.append("unresolved findings: " + ", ".join(unresolved))
    if verification.regressions:
        details.append(
            "blocker/major regressions: " + ", ".join(item.id for item in verification.regressions)
        )
    raise ImplementationReviewGateError("Remediation verification failed; " + "; ".join(details))


async def record_candidate(phase_id: str, candidate: CandidateDiff) -> None:
    async with SessionFactory() as session:
        phase = await session.get(Phase, phase_id)
        if phase is None:
            raise LookupError(phase_id)
        phase.candidate_base_sha = candidate.base_sha
        phase.candidate_diff_hash = candidate.diff_hash
        await session.commit()


async def reserve_phase_budget(
    phase_id: str,
    budget: Literal["implementation_review_attempts", "remediation_attempts", "verification_attempts"],
) -> int:
    column = getattr(Phase, budget)
    async with SessionFactory() as session:
        cycle = await session.scalar(
            update(Phase)
            .where(Phase.id == phase_id, column < 1)
            .values({budget: column + 1})
            .returning(Phase.review_cycle)
        )
        if cycle is None:
            raise ImplementationReviewGateError(
                f"The {budget.replace('_attempts', '').replace('_', ' ')} budget "
                "is exhausted for this phase review cycle"
            )
        await session.commit()
        return int(cycle)


class ImplementationReviewService:
    def __init__(self, agents: CopilotAgentService | None = None) -> None:
        self.agents = agents or CopilotAgentService()

    async def _persist(
        self,
        run: Run,
        phase: Phase,
        *,
        kind: ArtifactKind,
        data: (
            ImplementationReview
            | ImplementationReviewLedger
            | ImplementationRemediationReport
            | RemediationVerification
        ),
        markdown: str,
        model: str,
        candidate: CandidateDiff,
    ) -> Artifact:
        async with tracked_operation(
            run_id=run.id,
            phase_id=phase.id,
            operation_type="artifact.persistence",
            operation_key=f"{kind.value}:{phase.review_cycle}:{candidate.diff_hash}",
            detail={
                "artifact": kind.value,
                "review_cycle": phase.review_cycle,
                "candidate_diff_hash": candidate.diff_hash,
            },
        ) as operation:
            async with SessionFactory() as session:
                artifact = await persist_artifact(
                    session,
                    run=run,
                    kind=kind,
                    data=data,
                    markdown=markdown,
                    model=model,
                    snapshot=None,
                )
                await session.commit()
            operation.set_result({"artifact_id": artifact.id, "revision": artifact.revision})
            return artifact

    async def review(
        self, run: Run, phase: Phase, worktree: Path, candidate: CandidateDiff
    ) -> tuple[ImplementationReview, Artifact]:
        cycle = await reserve_phase_budget(phase.id, "implementation_review_attempts")
        reader = CandidateDiffReader(worktree, candidate)
        review = await self.agents.run_structured(
            model=run.reviewer_model,
            instructions=IMPLEMENTATION_REVIEW_INSTRUCTIONS,
            prompt=(
                f"Approved phase (untrusted JSON):\n{json.dumps(phase.details, sort_keys=True)}\n"
                f"Review cycle: {cycle}\nBase SHA: {candidate.base_sha}\n"
                f"Canonical staged diff SHA-256: {candidate.diff_hash}\n"
                f"Changed files: {json.dumps(candidate.changed_files)}\n"
                "Review the exact staged candidate exposed by the read-only diff tools."
            ),
            response_type=ImplementationReview,
            working_directory=worktree,
            tools=candidate_read_tools(reader),
            run_id=run.id,
            phase_id=phase.id,
            operation_type="model.implementation_review",
            operation_key=f"{phase.id}:{cycle}",
            operation_detail={
                "review_cycle": cycle,
                "base_sha": candidate.base_sha,
                "diff_hash": candidate.diff_hash,
                "changed_files": candidate.changed_files,
            },
            operation_detail_provider=reader.activity_snapshot,
        )
        review = review.model_copy(
            update={
                "review_cycle": cycle,
                "base_sha": candidate.base_sha,
                "diff_hash": candidate.diff_hash,
                "changed_files": candidate.changed_files,
            }
        )
        await validate_implementation_review(review, reader)
        artifact = await self._persist(
            run,
            phase,
            kind=ArtifactKind.IMPLEMENTATION_REVIEW,
            data=review,
            markdown=implementation_review_markdown(review),
            model=run.reviewer_model,
            candidate=candidate,
        )
        return review, artifact

    async def adjudicate(
        self,
        run: Run,
        phase: Phase,
        worktree: Path,
        candidate: CandidateDiff,
        review: ImplementationReview,
    ) -> tuple[ImplementationReviewLedger, Artifact]:
        reader = CandidateDiffReader(worktree, candidate)
        ledger = await self.agents.run_structured(
            model=run.primary_model,
            instructions=IMPLEMENTATION_ADJUDICATION_INSTRUCTIONS,
            prompt=(
                f"Approved phase (untrusted JSON):\n{json.dumps(phase.details, sort_keys=True)}\n"
                f"Implementation review (untrusted JSON):\n{review.model_dump_json(indent=2)}\n"
                "Adjudicate every finding against the same exact staged candidate."
            ),
            response_type=ImplementationReviewLedger,
            working_directory=worktree,
            tools=candidate_read_tools(reader),
            run_id=run.id,
            phase_id=phase.id,
            operation_type="model.implementation_review_adjudication",
            operation_key=f"{phase.id}:{review.review_cycle}",
            operation_detail={
                "review_cycle": review.review_cycle,
                "diff_hash": candidate.diff_hash,
                "findings": len(review.findings),
            },
            operation_detail_provider=reader.activity_snapshot,
        )
        ledger = ledger.model_copy(
            update={
                "review_cycle": review.review_cycle,
                "base_sha": candidate.base_sha,
                "diff_hash": candidate.diff_hash,
            }
        )
        ledger.validate_coverage(review)
        artifact = await self._persist(
            run,
            phase,
            kind=ArtifactKind.IMPLEMENTATION_REVIEW_LEDGER,
            data=ledger,
            markdown=implementation_review_ledger_markdown(ledger),
            model=run.primary_model,
            candidate=candidate,
        )
        return ledger, artifact

    async def remediate(
        self,
        run: Run,
        phase: Phase,
        worktree: Path,
        candidate: CandidateDiff,
        review: ImplementationReview,
        ledger: ImplementationReviewLedger,
        *,
        tools: list[Any],
        activity_snapshot: OperationDetailProvider,
    ) -> tuple[ImplementationRemediationReport, Artifact]:
        cycle = await reserve_phase_budget(phase.id, "remediation_attempts")
        accepted_ids = ledger.accepted_blocker_major_ids(review)
        findings = [
            finding.model_dump(mode="json") for finding in review.findings if finding.id in accepted_ids
        ]
        report = await self.agents.run_structured(
            model=run.primary_model,
            instructions=IMPLEMENTATION_REMEDIATION_INSTRUCTIONS,
            prompt=(
                f"Approved phase (untrusted JSON):\n{json.dumps(phase.details, sort_keys=True)}\n"
                f"Accepted blocker/major findings (untrusted JSON):\n"
                f"{json.dumps(findings, sort_keys=True)}\n"
                f"Original canonical staged diff SHA-256: {candidate.diff_hash}\n"
                "Perform the one allowed remediation and report the edits."
            ),
            response_type=ImplementationRemediationReport,
            working_directory=worktree,
            tools=tools,
            timeout_seconds=3600,
            run_id=run.id,
            phase_id=phase.id,
            operation_type="model.implementation_remediation",
            operation_key=f"{phase.id}:{cycle}",
            operation_detail={
                "review_cycle": cycle,
                "original_diff_hash": candidate.diff_hash,
                "accepted_findings": accepted_ids,
            },
            operation_detail_provider=activity_snapshot,
        )
        report = report.model_copy(
            update={
                "review_cycle": cycle,
                "original_diff_hash": candidate.diff_hash,
            }
        )
        report.validate_coverage(accepted_ids)
        artifact = await self._persist(
            run,
            phase,
            kind=ArtifactKind.REMEDIATION_REPORT,
            data=report,
            markdown=remediation_report_markdown(report),
            model=run.primary_model,
            candidate=candidate,
        )
        return report, artifact

    async def verify(
        self,
        run: Run,
        phase: Phase,
        worktree: Path,
        original_candidate: CandidateDiff,
        remediated_candidate: CandidateDiff,
        review: ImplementationReview,
        ledger: ImplementationReviewLedger,
        remediation: ImplementationRemediationReport,
    ) -> tuple[RemediationVerification, Artifact]:
        cycle = await reserve_phase_budget(phase.id, "verification_attempts")
        accepted_ids = ledger.accepted_blocker_major_ids(review)
        findings = [
            finding.model_dump(mode="json") for finding in review.findings if finding.id in accepted_ids
        ]
        reader = CandidateDiffReader(worktree, remediated_candidate, original_candidate=original_candidate)
        verification = await self.agents.run_structured(
            model=run.reviewer_model,
            instructions=REMEDIATION_VERIFICATION_INSTRUCTIONS,
            prompt=(
                f"Accepted blocker/major findings (untrusted JSON):\n"
                f"{json.dumps(findings, sort_keys=True)}\n"
                f"Remediation report (untrusted JSON):\n{remediation.model_dump_json(indent=2)}\n"
                f"Original diff hash: {original_candidate.diff_hash}\n"
                f"Remediated diff hash: {remediated_candidate.diff_hash}\n"
                "Verify closure only and report any blocker/major regression introduced by remediation."
            ),
            response_type=RemediationVerification,
            working_directory=worktree,
            tools=candidate_read_tools(reader, include_original=True),
            run_id=run.id,
            phase_id=phase.id,
            operation_type="model.remediation_verification",
            operation_key=f"{phase.id}:{cycle}",
            operation_detail={
                "review_cycle": cycle,
                "original_diff_hash": original_candidate.diff_hash,
                "remediated_diff_hash": remediated_candidate.diff_hash,
                "accepted_findings": accepted_ids,
            },
            operation_detail_provider=reader.activity_snapshot,
        )
        verification = verification.model_copy(
            update={
                "review_cycle": cycle,
                "original_diff_hash": original_candidate.diff_hash,
                "remediated_diff_hash": remediated_candidate.diff_hash,
            }
        )
        verification.validate_coverage(accepted_ids)
        await validate_remediation_verification(verification, reader)
        artifact = await self._persist(
            run,
            phase,
            kind=ArtifactKind.REMEDIATION_VERIFICATION,
            data=verification,
            markdown=remediation_verification_markdown(verification),
            model=run.reviewer_model,
            candidate=remediated_candidate,
        )
        return verification, artifact

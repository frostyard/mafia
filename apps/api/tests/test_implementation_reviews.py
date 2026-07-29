import hashlib
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from mafia.db.base import Base
from mafia.db.models import Phase, Repository, Run
from mafia.domain.artifacts import (
    ImplementationRemediationReport,
    ImplementationReview,
    ImplementationReviewLedger,
    RemediationVerification,
)
from mafia.domain.enums import PhaseState, RequirementType, RunState
from mafia.services import implementation_reviews
from mafia.services.commands import run_command
from mafia.services.implementation_reviews import (
    CandidateDiffReader,
    ImplementationReviewGateError,
    ImplementationReviewValidationError,
    capture_staged_candidate,
    require_remediation_verified,
    required_remediation_ids,
    reserve_phase_budget,
    validate_implementation_review,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def initialize_repository(path: Path) -> None:
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "Test User"), check=True)
    subprocess.run(
        ("git", "-C", str(path), "config", "user.email", "test@example.invalid"),
        check=True,
    )
    (path / "app.py").write_text("def value():\n    stable = True\n    return 'old'\n", encoding="utf-8")
    (path / "removed.py").write_text("obsolete = True\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", "--all"), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-q", "-m", "base"), check=True)


def review(*findings: dict[str, object]) -> ImplementationReview:
    return ImplementationReview.model_validate(
        {
            "review_cycle": 1,
            "base_sha": "a" * 40,
            "diff_hash": "b" * 64,
            "changed_files": ["app.py"],
            "summary": "Comprehensive review",
            "areas": [
                {"category": category, "assessment": "Reviewed."}
                for category in (
                    "requirements",
                    "correctness",
                    "security",
                    "compatibility",
                    "testing",
                    "operability",
                    "scope",
                )
            ],
            "findings": list(findings),
        }
    )


def finding(
    finding_id: str,
    severity: str,
    *,
    path: str = "app.py",
    side: str = "new",
    line: int = 3,
) -> dict[str, object]:
    return {
        "id": finding_id,
        "severity": severity,
        "category": "correctness",
        "confidence": 0.95,
        "location": {
            "file_path": path,
            "side": side,
            "line_start": line,
            "line_end": line,
        },
        "source_evidence": "The changed line produces the failure.",
        "failure_scenario": "A caller receives an invalid result.",
        "suggested_remediation": "Return the compatible result.",
    }


def ledger(*dispositions: dict[str, object]) -> ImplementationReviewLedger:
    return ImplementationReviewLedger.model_validate(
        {
            "review_cycle": 1,
            "base_sha": "a" * 40,
            "diff_hash": "b" * 64,
            "summary": "Adjudicated.",
            "dispositions": list(dispositions),
        }
    )


def disposition(finding_id: str, value: str) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "disposition": value,
        "evidence": ["Verified against the staged source."],
        "rationale": "The source supports this disposition.",
    }


@pytest.mark.asyncio
async def test_staged_candidate_hashes_new_deleted_files_and_validates_changed_sides(
    tmp_path: Path,
) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "app.py").write_text("def value():\n    stable = True\n    return 'new'\n", encoding="utf-8")
    (tmp_path / "removed.py").unlink()

    candidate = await capture_staged_candidate(tmp_path)
    canonical = (await run_command(("git", "-C", str(tmp_path), "diff", "--cached", "HEAD"))).stdout
    candidate_review = review(
        finding("IMP-1", "major"),
        finding("IMP-2", "minor", path="removed.py", side="old", line=1),
    ).model_copy(
        update={
            "base_sha": candidate.base_sha,
            "diff_hash": candidate.diff_hash,
            "changed_files": candidate.changed_files,
        }
    )

    reader = CandidateDiffReader(tmp_path, candidate)
    for path in candidate.changed_files:
        await reader.read_candidate_diff(path)
    await validate_implementation_review(candidate_review, reader)

    assert candidate.changed_files == ["app.py", "removed.py"]
    assert candidate.diff_hash == hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.asyncio
async def test_staged_candidate_uses_deterministic_rename_detection(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    await run_command(("git", "-C", str(tmp_path), "config", "diff.renames", "false"))
    (tmp_path / "app.py").rename(tmp_path / "renamed.py")

    candidate = await capture_staged_candidate(tmp_path)

    assert candidate.changed_files == ["renamed.py"]
    assert candidate.previous_paths == {"renamed.py": "app.py"}


@pytest.mark.asyncio
async def test_implementation_review_rejects_unchanged_line(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    (tmp_path / "app.py").write_text("def value():\n    stable = True\n    return 'new'\n", encoding="utf-8")
    candidate = await capture_staged_candidate(tmp_path)
    invalid = review(finding("IMP-1", "major", line=2)).model_copy(
        update={
            "base_sha": candidate.base_sha,
            "diff_hash": candidate.diff_hash,
            "changed_files": candidate.changed_files,
        }
    )

    reader = CandidateDiffReader(tmp_path, candidate)
    await reader.read_candidate_diff("app.py")
    with pytest.raises(ImplementationReviewValidationError, match="changed new line"):
        await validate_implementation_review(invalid, reader)


def test_no_remediation_without_accepted_blocker_or_major() -> None:
    candidate_review = review(finding("IMP-1", "major"), finding("IMP-2", "minor"))
    adjudication = ledger(disposition("IMP-1", "rejected"), disposition("IMP-2", "accepted"))
    adjudication.validate_coverage(candidate_review)

    assert required_remediation_ids(candidate_review, adjudication) == []


def test_deferred_serious_finding_fails_the_gate() -> None:
    candidate_review = review(finding("IMP-1", "blocker"))
    adjudication = ledger(disposition("IMP-1", "deferred"))
    adjudication.validate_coverage(candidate_review)

    with pytest.raises(ImplementationReviewGateError, match="IMP-1"):
        required_remediation_ids(candidate_review, adjudication)


def test_remediation_and_verification_require_exact_accepted_finding_coverage() -> None:
    report = ImplementationRemediationReport.model_validate(
        {
            "review_cycle": 1,
            "original_diff_hash": "a" * 64,
            "summary": "Fixed the accepted finding.",
            "edits": [
                {
                    "finding_id": "IMP-1",
                    "changed_files": ["app.py"],
                    "summary": "Corrected the return value.",
                }
            ],
        }
    )
    verification = RemediationVerification.model_validate(
        {
            "review_cycle": 1,
            "original_diff_hash": "a" * 64,
            "remediated_diff_hash": "b" * 64,
            "summary": "The finding is closed.",
            "closures": [
                {
                    "finding_id": "IMP-1",
                    "status": "resolved",
                    "evidence": ["The corrected changed line returns the compatible value."],
                    "rationale": "The failure scenario no longer occurs.",
                }
            ],
            "regressions": [],
        }
    )

    report.validate_coverage(["IMP-1"])
    verification.validate_coverage(["IMP-1"])
    require_remediation_verified(verification)

    with pytest.raises(ValueError, match="mismatch"):
        report.validate_coverage(["IMP-1", "IMP-2"])


def test_unresolved_accepted_finding_fails_without_another_remediation_edge() -> None:
    verification = RemediationVerification.model_validate(
        {
            "review_cycle": 1,
            "original_diff_hash": "a" * 64,
            "remediated_diff_hash": "b" * 64,
            "summary": "The finding remains open.",
            "closures": [
                {
                    "finding_id": "IMP-1",
                    "status": "unresolved",
                    "evidence": ["The failure remains on the changed line."],
                    "rationale": "The remediation did not alter the faulty behavior.",
                }
            ],
            "regressions": [],
        }
    )

    with pytest.raises(ImplementationReviewGateError, match="unresolved findings: IMP-1"):
        require_remediation_verified(verification)


@pytest.fixture
async def review_budget_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[async_sessionmaker[AsyncSession], str]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(implementation_reviews, "SessionFactory", factory)
    async with factory() as session:
        repository = Repository(
            owner="octo",
            name="repo",
            remote_url="https://github.com/octo/repo.git",
        )
        session.add(repository)
        await session.flush()
        run = Run(
            repository_id=repository.id,
            requirement_type=RequirementType.TEXT,
            requirement_text="Bound implementation review calls",
            primary_model="primary",
            reviewer_model="reviewer",
            state=RunState.EXECUTING_PHASE,
        )
        session.add(run)
        await session.flush()
        phase = Phase(
            run_id=run.id,
            ordinal=1,
            title="Review gate",
            objective="Bound model calls",
            dependencies=[],
            details={},
            status=PhaseState.EXECUTING,
            plan_revision=1,
            source_sha="a" * 40,
        )
        session.add(phase)
        await session.commit()
        phase_id = phase.id
    try:
        yield factory, phase_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_each_model_call_budget_allows_only_one_attempt_per_cycle(
    review_budget_phase: tuple[async_sessionmaker[AsyncSession], str],
) -> None:
    factory, phase_id = review_budget_phase
    budgets = (
        "implementation_review_attempts",
        "remediation_attempts",
        "verification_attempts",
    )
    for budget in budgets:
        assert await reserve_phase_budget(phase_id, budget) == 1
        with pytest.raises(ImplementationReviewGateError, match="budget is exhausted"):
            await reserve_phase_budget(phase_id, budget)

    async with factory() as session:
        phase = await session.get(Phase, phase_id)
    assert phase is not None
    assert (
        phase.implementation_review_attempts,
        phase.remediation_attempts,
        phase.verification_attempts,
    ) == (1, 1, 1)

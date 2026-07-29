from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AcceptanceCriterion(BaseModel):
    id: str = Field(pattern=r"^AC-\d+$")
    requirement_ids: list[str] = Field(min_length=1)
    statement: str = Field(min_length=1)


class FunctionalRequirement(BaseModel):
    id: str = Field(pattern=r"^REQ-\d+$")
    statement: str = Field(min_length=1)
    priority: Literal["must", "should", "could"] = "must"


class Specification(BaseModel):
    title: str
    problem_statement: str
    context: str
    goals: list[str] = Field(min_length=1)
    non_goals: list[str]
    users: list[str] = Field(min_length=1)
    use_cases: list[str] = Field(min_length=1)
    requirements: list[FunctionalRequirement] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    constraints: list[str]
    assumptions: list[str]
    open_questions: list[str]
    risks: list[str]
    out_of_scope: list[str]

    @model_validator(mode="after")
    def references_known_requirements(self) -> "Specification":
        requirement_ids = {requirement.id for requirement in self.requirements}
        unknown = {
            requirement_id
            for criterion in self.acceptance_criteria
            for requirement_id in criterion.requirement_ids
            if requirement_id not in requirement_ids
        }
        if unknown:
            raise ValueError(f"Acceptance criteria reference unknown requirements: {sorted(unknown)}")
        return self


class EvidenceCitation(BaseModel):
    path: str = Field(min_length=1)
    line_start: int = Field(gt=0)
    line_end: int = Field(gt=0)
    source_sha: str = Field(min_length=7, max_length=64)
    claim: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_lines(self) -> "EvidenceCitation":
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


class SystemFinding(BaseModel):
    statement: str
    citations: list[EvidenceCitation] = Field(min_length=1)


class ArchitectureDecision(BaseModel):
    decision: str
    rationale: str
    alternatives_rejected: list[str]
    citations: list[EvidenceCitation]


class PlanPhase(BaseModel):
    ordinal: int = Field(gt=0)
    title: str
    objective: str
    dependencies: list[int]
    scope: list[str] = Field(min_length=1)
    likely_files: list[str]
    implementation_steps: list[str] = Field(min_length=1)
    tests: list[str] = Field(min_length=1)
    migration_and_rollout: list[str]
    risks: list[str]
    acceptance_criteria: list[str] = Field(min_length=1)


class ImplementationPlan(BaseModel):
    specification_revision: int = Field(gt=0)
    source_sha: str
    summary: str
    system_findings: list[SystemFinding]
    architecture_decisions: list[ArchitectureDecision]
    phases: list[PlanPhase] = Field(min_length=1)
    cross_phase_invariants: list[str] = Field(min_length=1)
    completion_definition: list[str] = Field(min_length=1)
    unresolved_assumptions: list[str]

    @model_validator(mode="after")
    def valid_phase_graph(self) -> "ImplementationPlan":
        ordinals = [phase.ordinal for phase in self.phases]
        expected = list(range(1, len(self.phases) + 1))
        if ordinals != expected:
            raise ValueError(f"Phase ordinals must be sequential: {expected}")
        for phase in self.phases:
            if any(dependency >= phase.ordinal or dependency < 1 for dependency in phase.dependencies):
                raise ValueError(f"Phase {phase.ordinal} has an invalid dependency")
        return self


class ReviewFinding(BaseModel):
    id: str = Field(pattern=r"^REV-\d+$")
    severity: Literal["critical", "high", "medium", "low"]
    category: Literal[
        "correctness",
        "requirement",
        "grounding",
        "sequencing",
        "pr_size",
        "testing",
        "migration",
        "operability",
        "security",
    ]
    challenged_claim: str
    phase_ordinal: int | None = Field(default=None, gt=0)
    evidence: list[EvidenceCitation]
    failure_scenario: str
    recommendation: str


class AdversarialReview(BaseModel):
    summary: str
    findings: list[ReviewFinding]


class ReviewDisposition(BaseModel):
    finding_id: str
    disposition: Literal["accepted", "partially_accepted", "rejected"]
    rationale: str
    evidence: list[EvidenceCitation]
    changes: list[str]


class ReviewResolution(BaseModel):
    revised_plan: ImplementationPlan
    dispositions: list[ReviewDisposition]

    def validate_coverage(self, review: AdversarialReview) -> None:
        finding_ids = {finding.id for finding in review.findings}
        disposition_ids = {disposition.finding_id for disposition in self.dispositions}
        if finding_ids != disposition_ids:
            missing = finding_ids - disposition_ids
            extra = disposition_ids - finding_ids
            raise ValueError(f"Review ledger mismatch; missing={sorted(missing)}, extra={sorted(extra)}")


class ArtifactDecision(BaseModel):
    action: Literal["accept", "refine", "cancel"]
    feedback: str | None = None

    @model_validator(mode="after")
    def refinement_has_feedback(self) -> "ArtifactDecision":
        if self.action == "refine" and not (self.feedback and self.feedback.strip()):
            raise ValueError("Refinement feedback is required")
        return self


class ArtifactDecisionRequest(BaseModel):
    run_id: str
    artifact_id: str
    artifact_kind: Literal["specification", "plan"]
    revision: int
    prompt: str


class PhaseDecision(BaseModel):
    action: Literal["start", "cancel"]


class PhaseDecisionRequest(BaseModel):
    run_id: str
    phase_id: str
    ordinal: int
    title: str
    objective: str
    prompt: str


class PhaseExecutionReport(BaseModel):
    summary: str
    changed_files: list[str] = Field(min_length=1)
    validation_commands: list[str] = Field(min_length=1)
    validation_notes: list[str]
    known_limitations: list[str]


class PullRequestFinding(BaseModel):
    id: str = Field(pattern=r"^FIND-\d+$")
    severity: Literal["critical", "high", "medium", "low"]
    category: Literal[
        "correctness",
        "security",
        "reliability",
        "performance",
        "compatibility",
        "testing",
        "operability",
    ]
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    side: Literal["new", "old"] = "new"
    line_start: int = Field(gt=0)
    line_end: int = Field(gt=0)
    evidence: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_lines(self) -> "PullRequestFinding":
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


class PullRequestReview(BaseModel):
    summary: str = Field(min_length=1)
    verdict: Literal["approve", "comment", "request_changes"]
    findings: list[PullRequestFinding]
    strengths: list[str]
    testing_assessment: str = Field(min_length=1)


class PullRequestFindingDisposition(BaseModel):
    source_model: str = Field(min_length=1)
    finding_id: str = Field(pattern=r"^FIND-\d+$")
    disposition: Literal["accepted", "rejected", "duplicate"]
    rationale: str = Field(min_length=1)


class ConsolidatedPullRequestReview(BaseModel):
    pull_request_number: int = Field(gt=0)
    head_sha: str = Field(min_length=7, max_length=64)
    summary: str = Field(min_length=1)
    verdict: Literal["approve", "comment", "request_changes"]
    findings: list[PullRequestFinding]
    strengths: list[str]
    testing_assessment: str = Field(min_length=1)
    dispositions: list[PullRequestFindingDisposition]

    def validate_coverage(
        self,
        reviews: list[tuple[str, PullRequestReview]],
    ) -> None:
        expected = {
            (model, finding.id)
            for model, review in reviews
            for finding in review.findings
        }
        actual = {
            (disposition.source_model, disposition.finding_id)
            for disposition in self.dispositions
        }
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"Pull-request review ledger mismatch; missing={missing}, extra={extra}"
            )


class PullRequestReviewDecision(BaseModel):
    action: Literal["post", "finish", "cancel"]


class PullRequestReviewDecisionRequest(BaseModel):
    run_id: str
    artifact_id: str
    revision: int
    pull_request_number: int
    prompt: str


def specification_markdown(specification: Specification) -> str:
    requirement_lines = "\n".join(
        f"- **{requirement.id} ({requirement.priority})** {requirement.statement}"
        for requirement in specification.requirements
    )
    criteria_lines = "\n".join(
        f"- **{criterion.id}** [{', '.join(criterion.requirement_ids)}] {criterion.statement}"
        for criterion in specification.acceptance_criteria
    )
    return (
        f"# {specification.title}\n\n"
        f"## Problem\n\n{specification.problem_statement}\n\n"
        f"## Context\n\n{specification.context}\n\n"
        f"## Requirements\n\n{requirement_lines}\n\n"
        f"## Acceptance criteria\n\n{criteria_lines}\n"
    )


def plan_markdown(plan: ImplementationPlan) -> str:
    phases: list[str] = []
    for phase in plan.phases:
        steps = "\n".join(f"{index}. {step}" for index, step in enumerate(phase.implementation_steps, 1))
        phases.append(f"## Phase {phase.ordinal}: {phase.title}\n\n{phase.objective}\n\n{steps}")
    return f"# Implementation plan\n\n{plan.summary}\n\n" + "\n\n".join(phases)


def pull_request_review_markdown(
    review: PullRequestReview | ConsolidatedPullRequestReview,
) -> str:
    findings = "\n\n".join(
        (
            f"### {finding.severity.upper()}: {finding.title}\n\n"
            f"`{finding.file_path}:{finding.line_start}-{finding.line_end}`\n\n"
            f"{finding.description}\n\n"
            f"**Evidence:** {finding.evidence}\n\n"
            f"**Suggested fix:** {finding.suggested_fix}"
        )
        for finding in review.findings
    )
    if not findings:
        findings = "No actionable defects found."
    strengths = "\n".join(f"- {strength}" for strength in review.strengths)
    return (
        "# Pull request review\n\n"
        f"**Verdict:** {review.verdict.replace('_', ' ')}\n\n"
        f"{review.summary}\n\n"
        f"## Findings\n\n{findings}\n\n"
        f"## Strengths\n\n{strengths or 'None recorded.'}\n\n"
        f"## Testing assessment\n\n{review.testing_assessment}\n"
    )

import hashlib
from pathlib import Path
from typing import Any

from mafia.agents.copilot import CopilotAgentService
from mafia.agents.prompts import (
    ADJUDICATION_INSTRUCTIONS,
    PLANNING_INSTRUCTIONS,
    REVIEW_INSTRUCTIONS,
    SPECIFICATION_INSTRUCTIONS,
)
from mafia.db.models import Artifact, Evidence, Run, SourceSnapshot
from mafia.db.session import SessionFactory
from mafia.domain.artifacts import (
    AdversarialReview,
    ConsolidatedPullRequestReview,
    EvidenceCitation,
    ImplementationPlan,
    PhaseExecutionReport,
    PullRequestReview,
    ReviewResolution,
    Specification,
    plan_markdown,
    specification_markdown,
)
from mafia.domain.enums import ArtifactKind
from mafia.services.operations import tracked_operation
from mafia.services.source import SourcePathError, SourceReader, resolve_in_root
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class CitationValidationError(ValueError):
    pass


async def next_revision(session: AsyncSession, run_id: str, kind: ArtifactKind) -> int:
    current = await session.scalar(
        select(func.max(Artifact.revision)).where(Artifact.run_id == run_id, Artifact.kind == kind)
    )
    return int(current or 0) + 1


async def persist_artifact(
    session: AsyncSession,
    *,
    run: Run,
    kind: ArtifactKind,
    data: (
        Specification
        | ImplementationPlan
        | AdversarialReview
        | ReviewResolution
        | PhaseExecutionReport
        | PullRequestReview
        | ConsolidatedPullRequestReview
    ),
    markdown: str,
    model: str,
    snapshot: SourceSnapshot | None,
) -> Artifact:
    artifact = Artifact(
        run_id=run.id,
        source_snapshot_id=snapshot.id if snapshot else None,
        kind=kind,
        revision=await next_revision(session, run.id, kind),
        structured_data=data.model_dump(mode="json"),
        rendered_markdown=markdown,
        model=model,
    )
    session.add(artifact)
    await session.flush()
    return artifact


def source_tools(reader: SourceReader) -> list[Any]:
    def list_source(path: str = ".") -> list[str]:
        """List files immediately below a repository-relative directory."""
        return reader.list_source(path)

    def read_source(path: str, line_start: int = 1, line_end: int = 400) -> str:
        """Read a repository-relative file with numbered lines."""
        return reader.read_source(path, line_start, line_end)

    def search_source(query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Search text in repository files, returning paths, lines, and excerpts."""
        return reader.search_source(query, limit)

    return [list_source, read_source, search_source]


def plan_citations(plan: ImplementationPlan) -> list[EvidenceCitation]:
    return [citation for finding in plan.system_findings for citation in finding.citations] + [
        citation for decision in plan.architecture_decisions for citation in decision.citations
    ]


def validate_citations(
    citations: list[EvidenceCitation],
    snapshot: SourceSnapshot,
) -> None:
    root = Path(snapshot.worktree_path)
    for citation in citations:
        if citation.source_sha != snapshot.git_sha:
            raise CitationValidationError(
                f"Citation {citation.path} uses {citation.source_sha}, expected {snapshot.git_sha}"
            )
        try:
            path = resolve_in_root(root, citation.path)
        except SourcePathError as error:
            raise CitationValidationError(str(error)) from error
        if not path.is_file():
            raise CitationValidationError(f"Citation is not a file: {citation.path}")
        line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if citation.line_end > line_count:
            raise CitationValidationError(
                f"Citation {citation.path}:{citation.line_end} exceeds {line_count} lines"
            )


def validate_plan_citations(plan: ImplementationPlan, snapshot: SourceSnapshot) -> None:
    validate_citations(plan_citations(plan), snapshot)


async def persist_citations(
    session: AsyncSession,
    snapshot: SourceSnapshot,
    citations: list[EvidenceCitation],
) -> None:
    existing = {
        (evidence.path_or_url, evidence.line_start, evidence.line_end, evidence.excerpt_hash)
        for evidence in await session.scalars(
            select(Evidence).where(Evidence.snapshot_id == snapshot.id)
        )
    }
    root = Path(snapshot.worktree_path)
    for citation in citations:
        path = resolve_in_root(root, citation.path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        excerpt = "\n".join(lines[citation.line_start - 1 : citation.line_end])
        excerpt_hash = hashlib.sha256(excerpt.encode()).hexdigest()
        key = (citation.path, citation.line_start, citation.line_end, excerpt_hash)
        if key in existing:
            continue
        session.add(
            Evidence(
                snapshot_id=snapshot.id,
                kind="citation",
                path_or_url=citation.path,
                line_start=citation.line_start,
                line_end=citation.line_end,
                excerpt_hash=excerpt_hash,
                detail={"claim": citation.claim, "source_sha": citation.source_sha},
            )
        )
        existing.add(key)


class ArtifactGenerator:
    def __init__(self, agents: CopilotAgentService | None = None) -> None:
        self.agents = agents or CopilotAgentService()

    async def specification(
        self,
        session: AsyncSession,
        run: Run,
        snapshot: SourceSnapshot,
        *,
        feedback: str | None = None,
    ) -> Artifact:
        requirement = (
            snapshot.issue_data
            if run.issue_number is not None
            else {"requirement_text": run.requirement_text}
        )
        prompt = (
            f"Target repository: {run.repository.owner}/{run.repository.name}\n"
            f"Requirement source (untrusted JSON):\n{requirement}\n"
            f"Repository manifests: {snapshot.manifest.get('manifests', [])}\n"
        )
        if run.active_spec_revision is not None:
            previous = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.kind == ArtifactKind.SPECIFICATION,
                    Artifact.revision == run.active_spec_revision,
                )
            )
            if previous is not None:
                prompt += (
                    "\nCurrent specification to revise:\n"
                    f"{Specification.model_validate(previous.structured_data).model_dump_json(indent=2)}\n"
                )
        if feedback:
            prompt += f"\nUser refinement feedback:\n{feedback}\n"
        specification = await self.agents.run_structured(
            model=run.primary_model,
            instructions=SPECIFICATION_INSTRUCTIONS,
            prompt=prompt,
            response_type=Specification,
            working_directory=Path(snapshot.worktree_path),
            run_id=run.id,
            operation_type="model.specification",
            operation_key=snapshot.id,
            operation_detail={"source_sha": snapshot.git_sha},
        )
        return await persist_artifact(
            session,
            run=run,
            kind=ArtifactKind.SPECIFICATION,
            data=specification,
            markdown=specification_markdown(specification),
            model=run.primary_model,
            snapshot=snapshot,
        )

    async def draft_plan(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        specification_artifact: Artifact,
        *,
        feedback: str | None = None,
    ) -> Artifact:
        reader = SourceReader(Path(snapshot.worktree_path))
        tools = source_tools(reader)
        specification = Specification.model_validate(specification_artifact.structured_data)
        prompt = (
            f"Source SHA: {snapshot.git_sha}\n"
            f"Specification revision: {specification_artifact.revision}\n"
            f"Specification:\n{specification.model_dump_json(indent=2)}\n"
            f"Repository manifest:\n{snapshot.manifest}\n"
        )
        if feedback:
            prompt += f"\nUser refinement feedback:\n{feedback}\n"
        plan = await self.agents.run_structured(
            model=run.primary_model,
            instructions=PLANNING_INSTRUCTIONS,
            prompt=prompt,
            response_type=ImplementationPlan,
            working_directory=Path(snapshot.worktree_path),
            tools=tools,
            run_id=run.id,
            operation_type="model.plan_generation",
            operation_key=snapshot.id,
            operation_detail={
                "source_sha": snapshot.git_sha,
                "specification_revision": specification_artifact.revision,
            },
            operation_detail_provider=reader.activity.snapshot,
        )
        citations = plan_citations(plan)
        async with tracked_operation(
            run_id=run.id,
            operation_type="citation.validation",
            operation_key=f"draft:{snapshot.id}",
            detail={
                "artifact": "draft_plan",
                "citation_count": len(citations),
                "source_sha": snapshot.git_sha,
            },
        ) as operation:
            validate_plan_citations(plan, snapshot)
            operation.set_result({"citations_validated": len(citations)})
        async with tracked_operation(
            run_id=run.id,
            operation_type="artifact.persistence",
            operation_key=f"draft:{snapshot.id}",
            detail={"artifact": "draft_plan"},
        ) as operation:
            async with SessionFactory() as session:
                artifact = await persist_artifact(
                    session,
                    run=run,
                    kind=ArtifactKind.PLAN,
                    data=plan,
                    markdown=plan_markdown(plan),
                    model=run.primary_model,
                    snapshot=snapshot,
                )
                await session.commit()
            operation.set_result({"artifact_id": artifact.id, "revision": artifact.revision})
        return artifact

    async def adversarial_review(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        specification_artifact: Artifact,
        plan_artifact: Artifact,
    ) -> Artifact:
        reader = SourceReader(Path(snapshot.worktree_path))
        tools = source_tools(reader)
        specification = Specification.model_validate(specification_artifact.structured_data)
        plan = ImplementationPlan.model_validate(plan_artifact.structured_data)
        review = await self.agents.run_structured(
            model=run.reviewer_model,
            instructions=REVIEW_INSTRUCTIONS,
            prompt=(
                f"Source SHA: {snapshot.git_sha}\n"
                f"Specification:\n{specification.model_dump_json(indent=2)}\n"
                f"Plan under review:\n{plan.model_dump_json(indent=2)}"
            ),
            response_type=AdversarialReview,
            working_directory=Path(snapshot.worktree_path),
            tools=tools,
            run_id=run.id,
            operation_type="model.adversarial_review",
            operation_key=snapshot.id,
            operation_detail={
                "source_sha": snapshot.git_sha,
                "draft_plan_revision": plan_artifact.revision,
            },
            operation_detail_provider=reader.activity.snapshot,
        )
        review_citations = [
            citation for finding in review.findings for citation in finding.evidence
        ]
        async with tracked_operation(
            run_id=run.id,
            operation_type="citation.validation",
            operation_key=f"review:{snapshot.id}",
            detail={
                "artifact": "adversarial_review",
                "citation_count": len(review_citations),
                "source_sha": snapshot.git_sha,
            },
        ) as operation:
            validate_citations(review_citations, snapshot)
            operation.set_result({"citations_validated": len(review_citations)})
        async with tracked_operation(
            run_id=run.id,
            operation_type="artifact.persistence",
            operation_key=f"review:{snapshot.id}",
            detail={"artifact": "adversarial_review"},
        ) as operation:
            async with SessionFactory() as session:
                review_artifact = await persist_artifact(
                    session,
                    run=run,
                    kind=ArtifactKind.REVIEW,
                    data=review,
                    markdown=f"# Adversarial review\n\n{review.summary}",
                    model=run.reviewer_model,
                    snapshot=snapshot,
                )
                await session.commit()
            operation.set_result(
                {"artifact_id": review_artifact.id, "revision": review_artifact.revision}
            )
        return review_artifact

    async def adjudicate_plan(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        specification_artifact: Artifact,
        plan_artifact: Artifact,
        review_artifact: Artifact,
    ) -> ReviewResolution:
        reader = SourceReader(Path(snapshot.worktree_path))
        tools = source_tools(reader)
        specification = Specification.model_validate(specification_artifact.structured_data)
        plan = ImplementationPlan.model_validate(plan_artifact.structured_data)
        review = AdversarialReview.model_validate(review_artifact.structured_data)
        resolution = await self.agents.run_structured(
            model=run.primary_model,
            instructions=ADJUDICATION_INSTRUCTIONS,
            prompt=(
                f"Source SHA: {snapshot.git_sha}\n"
                f"Specification:\n{specification.model_dump_json(indent=2)}\n"
                f"Draft plan:\n{plan.model_dump_json(indent=2)}\n"
                f"Adversarial review:\n{review.model_dump_json(indent=2)}"
            ),
            response_type=ReviewResolution,
            working_directory=Path(snapshot.worktree_path),
            tools=tools,
            run_id=run.id,
            operation_type="model.plan_adjudication",
            operation_key=snapshot.id,
            operation_detail={
                "source_sha": snapshot.git_sha,
                "draft_plan_revision": plan_artifact.revision,
                "review_revision": review_artifact.revision,
                "review_findings": len(review.findings),
            },
            operation_detail_provider=reader.activity.snapshot,
        )
        plan_citation_items = plan_citations(resolution.revised_plan)
        resolution_citations = [
            citation for disposition in resolution.dispositions for citation in disposition.evidence
        ]
        async with tracked_operation(
            run_id=run.id,
            operation_type="citation.validation",
            operation_key=f"adjudication:{snapshot.id}",
            detail={
                "artifact": "adjudicated_plan",
                "citation_count": len(plan_citation_items) + len(resolution_citations),
                "source_sha": snapshot.git_sha,
            },
        ) as operation:
            resolution.validate_coverage(review)
            validate_plan_citations(resolution.revised_plan, snapshot)
            validate_citations(resolution_citations, snapshot)
            operation.set_result(
                {
                    "citations_validated": len(plan_citation_items)
                    + len(resolution_citations),
                    "findings_resolved": len(resolution.dispositions),
                }
            )
        return resolution

    async def persist_final_plan(
        self,
        run: Run,
        snapshot: SourceSnapshot,
        review_artifact: Artifact,
        resolution: ReviewResolution,
    ) -> tuple[Artifact, Artifact]:
        review = AdversarialReview.model_validate(review_artifact.structured_data)
        review_citations = [
            citation for finding in review.findings for citation in finding.evidence
        ]
        resolution_citations = [
            citation for disposition in resolution.dispositions for citation in disposition.evidence
        ]
        citations = (
            plan_citations(resolution.revised_plan)
            + review_citations
            + resolution_citations
        )
        async with tracked_operation(
            run_id=run.id,
            operation_type="artifact.persistence",
            operation_key=f"final:{snapshot.id}",
            detail={
                "artifacts": ["plan", "review_ledger"],
                "citation_count": len(citations),
                "source_sha": snapshot.git_sha,
            },
        ) as operation:
            async with SessionFactory() as session:
                await persist_citations(session, snapshot, citations)
                final_plan = await persist_artifact(
                    session,
                    run=run,
                    kind=ArtifactKind.PLAN,
                    data=resolution.revised_plan,
                    markdown=plan_markdown(resolution.revised_plan),
                    model=run.primary_model,
                    snapshot=snapshot,
                )
                ledger = await persist_artifact(
                    session,
                    run=run,
                    kind=ArtifactKind.REVIEW_LEDGER,
                    data=resolution,
                    markdown="# Review ledger\n\n"
                    + "\n".join(
                        f"- **{item.finding_id}: {item.disposition}** {item.rationale}"
                        for item in resolution.dispositions
                    ),
                    model=run.primary_model,
                    snapshot=snapshot,
                )
                await session.commit()
            operation.set_result(
                {
                    "plan_artifact_id": final_plan.id,
                    "plan_revision": final_plan.revision,
                    "ledger_artifact_id": ledger.id,
                    "citations_persisted": len(citations),
                }
            )
        return final_plan, ledger

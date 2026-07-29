from pathlib import Path
from typing import Any, cast

import pytest
from mafia.agents.copilot import CopilotAgentService
from mafia.db.base import Base
from mafia.db.models import Artifact, Repository, Run, SourceSnapshot
from mafia.domain.artifacts import Specification
from mafia.domain.enums import ArtifactKind, RequirementType
from mafia.services.artifacts import ArtifactGenerator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def specification(title: str) -> Specification:
    return Specification.model_validate(
        {
            "title": title,
            "problem_statement": "The workflow needs revision.",
            "context": "An existing specification is available.",
            "goals": ["Revise the specification"],
            "non_goals": [],
            "users": ["Operator"],
            "use_cases": ["Provide refinement feedback"],
            "requirements": [
                {
                    "id": "REQ-1",
                    "statement": "Apply refinement feedback.",
                    "priority": "must",
                }
            ],
            "acceptance_criteria": [
                {
                    "id": "AC-1",
                    "requirement_ids": ["REQ-1"],
                    "statement": "The revised specification reflects the feedback.",
                }
            ],
            "constraints": [],
            "assumptions": [],
            "open_questions": [],
            "risks": [],
            "out_of_scope": [],
        }
    )


class RecordingAgents:
    prompt = ""

    async def run_structured(self, **kwargs: Any) -> Specification:
        self.prompt = str(kwargs["prompt"])
        return specification("Revised specification")


@pytest.mark.asyncio
async def test_specification_refinement_includes_current_revision(
    tmp_path: Path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agents = RecordingAgents()
    try:
        async with factory() as session:
            repository = Repository(
                owner="octo",
                name="repo",
                remote_url="https://github.com/octo/repo.git",
            )
            session.add(repository)
            await session.flush()
            run = Run(
                repository=repository,
                requirement_type=RequirementType.TEXT,
                requirement_text="Original requirement",
                primary_model="gpt-5.6-sol",
                reviewer_model="claude-opus-4.8",
                active_spec_revision=1,
            )
            session.add(run)
            await session.flush()
            session.add(
                Artifact(
                    run_id=run.id,
                    kind=ArtifactKind.SPECIFICATION,
                    revision=1,
                    structured_data=specification(
                        "Current specification"
                    ).model_dump(mode="json"),
                    rendered_markdown="Current specification",
                    model=run.primary_model,
                )
            )
            snapshot = SourceSnapshot(
                run_id=run.id,
                git_sha="a" * 40,
                reason="spec-r2",
                manifest={},
                instructions=[],
                worktree_path=str(tmp_path),
            )
            session.add(snapshot)
            await session.flush()

            await ArtifactGenerator(
                cast(CopilotAgentService, agents)
            ).specification(
                session,
                run,
                snapshot,
                feedback="Add an explicit rollback requirement.",
            )

        assert "Current specification to revise" in agents.prompt
        assert '"title": "Current specification"' in agents.prompt
        assert "Add an explicit rollback requirement." in agents.prompt
    finally:
        await engine.dispose()

import pytest
from mafia.domain.artifacts import (
    ConsolidatedPullRequestReview,
    PullRequestReview,
)
from mafia.domain.enums import WorkflowType
from mafia.domain.schemas import DecisionSubmission, RunCreate
from pydantic import ValidationError


def test_requires_exactly_one_requirement_source() -> None:
    with pytest.raises(ValidationError):
        RunCreate(
            repository="octo/repo",
            primary_model="gpt-5.6-sol",
            issue_number=1,
            requirement_text="also text",
        )


def test_refine_requires_non_blank_feedback() -> None:
    with pytest.raises(ValidationError):
        DecisionSubmission(action="refine", feedback="  ")


def test_non_refine_rejects_feedback() -> None:
    with pytest.raises(ValidationError):
        DecisionSubmission(action="accept", feedback="No changes")


def test_pull_request_review_requires_only_pull_request_number() -> None:
    request = RunCreate(
        workflow_type=WorkflowType.PULL_REQUEST_REVIEW,
        repository="octo/repo",
        primary_model="gpt-5.6-sol",
        pull_request_number=42,
    )

    assert request.pull_request_number == 42

    with pytest.raises(ValidationError):
        RunCreate(
            workflow_type=WorkflowType.PULL_REQUEST_REVIEW,
            repository="octo/repo",
            primary_model="gpt-5.6-sol",
            pull_request_number=42,
            requirement_text="Not valid for a review",
        )


def test_consolidated_review_requires_disposition_for_every_finding() -> None:
    review = PullRequestReview.model_validate(
        {
            "summary": "One defect",
            "verdict": "request_changes",
            "findings": [
                {
                    "id": "FIND-1",
                    "severity": "high",
                    "category": "correctness",
                    "title": "Wrong result",
                    "description": "The changed branch returns the wrong value.",
                    "file_path": "src/app.py",
                    "line_start": 10,
                    "line_end": 10,
                    "evidence": "The new condition is inverted.",
                    "suggested_fix": "Invert the condition.",
                }
            ],
            "strengths": [],
            "testing_assessment": "A regression test is required.",
        }
    )
    consolidated = ConsolidatedPullRequestReview.model_validate(
        {
            "pull_request_number": 42,
            "head_sha": "a" * 40,
            "summary": "No accepted findings",
            "verdict": "approve",
            "findings": [],
            "strengths": [],
            "testing_assessment": "Tests pass.",
            "dispositions": [],
        }
    )

    with pytest.raises(ValueError, match="ledger mismatch"):
        consolidated.validate_coverage([("gpt-5.6-sol", review)])

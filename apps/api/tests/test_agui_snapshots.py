# pyright: reportPrivateUsage=false

from mafia.agui.snapshots import (
    _active_interrupts,
    _deduplicate_messages,
)
from mafia.domain.enums import RunState


def artifact_interrupt(kind: str) -> dict[str, object]:
    return {
        "id": f"{kind}-decision",
        "metadata": {
            "agent_framework": {
                "request_type": "ArtifactDecisionRequest",
                "data": {"artifact_kind": kind},
            }
        },
    }


def test_snapshot_messages_keep_latest_value_for_each_id() -> None:
    messages = [
        {"id": "m1", "role": "assistant", "content": "partial"},
        {"id": "m2", "role": "user", "content": "answer"},
        {"id": "m1", "role": "assistant", "content": "complete"},
    ]

    assert _deduplicate_messages(messages) == [
        {"id": "m1", "role": "assistant", "content": "complete"},
        {"id": "m2", "role": "user", "content": "answer"},
    ]


def test_snapshot_messages_keep_only_one_copy_of_each_tool_call() -> None:
    messages = [
        {
            "id": "assistant-1",
            "role": "assistant",
            "tool_calls": [{"id": "decision-1"}],
        },
        {
            "id": "decision-1",
            "role": "assistant",
            "tool_calls": [{"id": "decision-1"}],
        },
    ]

    assert _deduplicate_messages(messages) == [messages[0]]


def test_stale_artifact_interrupt_is_hidden_after_state_advances() -> None:
    interrupt = artifact_interrupt("plan")

    assert _active_interrupts(
        [interrupt],
        RunState.AWAITING_PLAN_DECISION,
    ) == [interrupt]
    assert _active_interrupts([interrupt], RunState.REVIEWING_PLAN) is None


def test_pull_request_review_interrupt_matches_only_its_decision_state() -> None:
    interrupt = {
        "id": "pr-review-decision",
        "metadata": {
            "agent_framework": {
                "request_type": "PullRequestReviewDecisionRequest",
            }
        },
    }

    assert _active_interrupts(
        [interrupt],
        RunState.AWAITING_PR_REVIEW_DECISION,
    ) == [interrupt]
    assert _active_interrupts([interrupt], RunState.REVIEWING_PR) is None

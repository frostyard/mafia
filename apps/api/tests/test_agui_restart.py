# pyright: reportPrivateUsage=false

from pathlib import Path
from typing import Any

import pytest
from ag_ui.core import RunErrorEvent, RunFinishedEvent
from agent_framework import (
    Executor,
    FileCheckpointStorage,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from agent_framework.ag_ui import InMemoryAGUIThreadSnapshotStore
from mafia.agui.workflow import (
    DurableAgentFrameworkWorkflow,
    _is_snapshot_replay,
    _strip_replayed_messages,
)
from mafia.domain.artifacts import PhaseDecision, PhaseDecisionRequest


class DecisionExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="decision")

    @handler
    async def start(
        self,
        message: list[Message] | Message | str,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        del message
        await ctx.request_info(
            PhaseDecisionRequest(
                run_id="run-1",
                phase_id="phase-1",
                ordinal=1,
                title="First phase",
                objective="Verify restart behavior",
                prompt="Start?",
            ),
            dict,
            request_id="decision-1",
        )

    @response_handler(request=PhaseDecisionRequest, response=dict)
    async def decide(
        self,
        original_request: PhaseDecisionRequest,
        response: dict[str, Any],
        ctx: WorkflowContext[Any, str],
    ) -> None:
        del original_request
        decision = PhaseDecision.model_validate(response)
        await ctx.yield_output(f"Decision: {decision.action}")


def build_test_workflow(thread_id: str, storage: FileCheckpointStorage):
    executor = DecisionExecutor()
    return WorkflowBuilder(
        name=f"restart-test-{thread_id}",
        start_executor=executor,
        output_from=[executor],
        checkpoint_storage=storage,
    ).build()


def test_decision_response_handler_matches_request() -> None:
    executor = DecisionExecutor()
    assert executor.is_request_supported(PhaseDecisionRequest, dict)


def test_replayed_snapshot_messages_are_not_appended_again() -> None:
    stored = [
        {"id": "user-1", "role": "user", "content": "Start"},
        {"id": "assistant-1", "role": "assistant", "content": "Working"},
    ]
    incoming = [
        *stored,
        {"id": "tool-1", "role": "tool", "content": "Approved"},
    ]

    assert _is_snapshot_replay(stored, stored) is True
    assert _strip_replayed_messages(incoming, stored) == [incoming[-1]]


@pytest.mark.asyncio
async def test_pending_decision_resumes_after_wrapper_restart(tmp_path: Path) -> None:
    storage = FileCheckpointStorage(
        tmp_path / "checkpoints",
        allowed_checkpoint_types=[
            "mafia.domain.artifacts:PhaseDecision",
            "mafia.domain.artifacts:PhaseDecisionRequest",
        ],
    )

    def factory(thread_id: str):
        return build_test_workflow(thread_id, storage)

    first = DurableAgentFrameworkWorkflow(
        workflow_factory=factory,
        checkpoint_storage=storage,
    )
    initial_events = [
        event
        async for event in first.run(
            {
                "threadId": "thread-1",
                "runId": "run-1",
                "messages": [{"id": "m1", "role": "user", "content": "Start"}],
            }
        )
    ]
    assert any(isinstance(event, RunFinishedEvent) for event in initial_events)
    checkpoint = await storage.get_latest(workflow_name="restart-test-thread-1")
    assert checkpoint is not None
    assert set(checkpoint.pending_request_info_events) == {"decision-1"}

    restarted = DurableAgentFrameworkWorkflow(
        workflow_factory=factory,
        checkpoint_storage=storage,
    )
    resumed_events = [
        event
        async for event in restarted.run(
            {
                "threadId": "thread-1",
                "runId": "run-2",
                "resume": [
                    {
                        "interruptId": "decision-1",
                        "status": "resolved",
                        "value": {"action": "start"},
                    }
                ],
            }
        )
    ]

    errors = [event for event in resumed_events if isinstance(event, RunErrorEvent)]
    assert not errors, [event.model_dump() for event in errors]
    assert any(
        "Decision: start" in event.model_dump_json()
        for event in resumed_events
    )


@pytest.mark.asyncio
async def test_snapshot_reconnect_hydrates_and_resume_deduplicates(
    tmp_path: Path,
) -> None:
    storage = FileCheckpointStorage(
        tmp_path / "checkpoints",
        allowed_checkpoint_types=[
            "mafia.domain.artifacts:PhaseDecision",
            "mafia.domain.artifacts:PhaseDecisionRequest",
        ],
    )
    snapshots = InMemoryAGUIThreadSnapshotStore()

    def factory(thread_id: str):
        return build_test_workflow(thread_id, storage)

    first = DurableAgentFrameworkWorkflow(
        workflow_factory=factory,
        checkpoint_storage=storage,
        snapshot_store=snapshots,
    )
    scope = "test-user"
    initial_events = [
        event
        async for event in first.run(
            {
                "threadId": "thread-2",
                "runId": "run-1",
                "messages": [{"id": "m1", "role": "user", "content": "Start"}],
                "__ag_ui_snapshot_scope": scope,
            }
        )
    ]
    assert not [
        event for event in initial_events if isinstance(event, RunErrorEvent)
    ]
    snapshot = await snapshots.get(scope=scope, thread_id="thread-2")
    assert snapshot is not None
    assert snapshot.interrupt is not None

    reconnect = DurableAgentFrameworkWorkflow(
        workflow_factory=factory,
        checkpoint_storage=storage,
        snapshot_store=snapshots,
    )
    reconnect_events = [
        event
        async for event in reconnect.run(
            {
                "threadId": "thread-2",
                "runId": "run-2",
                "messages": snapshot.messages,
                "__ag_ui_snapshot_scope": scope,
            }
        )
    ]
    assert not [
        event for event in reconnect_events if isinstance(event, RunErrorEvent)
    ]

    resumed_events = [
        event
        async for event in reconnect.run(
            {
                "threadId": "thread-2",
                "runId": "run-3",
                "messages": snapshot.messages,
                "resume": [
                    {
                        "interruptId": "decision-1",
                        "status": "resolved",
                        "value": {"action": "start"},
                    }
                ],
                "__ag_ui_snapshot_scope": scope,
            }
        )
    ]
    assert not [
        event for event in resumed_events if isinstance(event, RunErrorEvent)
    ]
    resumed_snapshot = await snapshots.get(scope=scope, thread_id="thread-2")
    assert resumed_snapshot is not None
    message_ids = [
        message.get("id")
        for message in resumed_snapshot.messages
        if message.get("id")
    ]
    assert len(message_ids) == len(set(message_ids))
    assert resumed_snapshot.interrupt is None

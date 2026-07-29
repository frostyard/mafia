from collections.abc import AsyncGenerator
from typing import Any, cast

from agent_framework import CheckpointStorage
from agent_framework.ag_ui import AgentFrameworkWorkflow, AGUIThreadSnapshot

_SNAPSHOT_SCOPE_INPUT_KEY = "__ag_ui_snapshot_scope"


def _resume_payload(input_data: dict[str, Any]) -> Any:
    resume = input_data.get("resume")
    if resume is not None:
        return resume
    command_value = input_data.get("command")
    command = cast(dict[str, Any], command_value) if isinstance(command_value, dict) else None
    if command is not None and command.get("resume") is not None:
        return command["resume"]
    forwarded_value = input_data.get("forwarded_props") or input_data.get("forwardedProps")
    forwarded = (
        cast(dict[str, Any], forwarded_value) if isinstance(forwarded_value, dict) else None
    )
    if forwarded is not None:
        return forwarded.get("resume")
    return None


def _message_id(message: dict[str, Any]) -> str | None:
    message_id = message.get("id")
    return message_id if isinstance(message_id, str) and message_id else None


def _strip_replayed_messages(
    incoming_messages: list[dict[str, Any]],
    stored_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stored_ids = {
        message_id
        for message in stored_messages
        if (message_id := _message_id(message)) is not None
    }
    return [
        message
        for message in incoming_messages
        if (message_id := _message_id(message)) is None or message_id not in stored_ids
    ]


def _is_snapshot_replay(
    incoming_messages: list[dict[str, Any]],
    stored_messages: list[dict[str, Any]],
) -> bool:
    if not incoming_messages or not stored_messages:
        return False
    stored_ids = {
        message_id
        for message in stored_messages
        if (message_id := _message_id(message)) is not None
    }
    incoming_ids = [_message_id(message) for message in incoming_messages]
    return bool(incoming_ids) and all(
        message_id is not None and message_id in stored_ids
        for message_id in incoming_ids
    )


def _resume_interrupt_ids(payload: Any) -> set[str]:
    entries: list[object] = cast(list[object], payload) if isinstance(payload, list) else [payload]
    interrupt_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        values = cast(dict[str, Any], entry)
        interrupt_id = values.get("interruptId") or values.get("interrupt_id") or values.get("id")
        if isinstance(interrupt_id, str) and interrupt_id:
            interrupt_ids.add(interrupt_id)
    return interrupt_ids


def _clear_resumed_interrupts(
    snapshot: AGUIThreadSnapshot,
    interrupt_ids: set[str],
) -> bool:
    if not interrupt_ids or snapshot.interrupt is None:
        return False
    remaining = [
        interrupt
        for interrupt in snapshot.interrupt
        if str(interrupt.get("id", "")) not in interrupt_ids
    ]
    if len(remaining) == len(snapshot.interrupt):
        return False
    snapshot.interrupt = remaining or None
    return True


class DurableAgentFrameworkWorkflow(AgentFrameworkWorkflow):
    def __init__(self, *, checkpoint_storage: CheckpointStorage, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._checkpoint_storage = checkpoint_storage

    async def run(self, input_data: dict[str, Any]) -> AsyncGenerator[Any]:
        thread_id = self._thread_id_from_input(input_data)
        snapshot_scope = input_data.get(_SNAPSHOT_SCOPE_INPUT_KEY)
        cache_key = (snapshot_scope, thread_id)
        resume_payload = _resume_payload(input_data)
        snapshot_store = self.snapshot_store
        snapshot: AGUIThreadSnapshot | None = None
        if (
            snapshot_store is not None
            and isinstance(snapshot_scope, str)
            and snapshot_scope
        ):
            snapshot = await snapshot_store.get(
                scope=snapshot_scope,
                thread_id=thread_id,
            )
        incoming_value = input_data.get("messages")
        incoming_messages = (
            [
                cast(dict[str, Any], message)
                for message in cast(list[object], incoming_value)
                if isinstance(message, dict)
            ]
            if isinstance(incoming_value, list)
            else []
        )
        if snapshot is not None:
            if resume_payload is not None:
                input_data["messages"] = _strip_replayed_messages(
                    incoming_messages,
                    snapshot.messages,
                )
                if _clear_resumed_interrupts(
                    snapshot,
                    _resume_interrupt_ids(resume_payload),
                ) and snapshot_store is not None and isinstance(snapshot_scope, str):
                    await snapshot_store.save(
                        scope=snapshot_scope,
                        thread_id=thread_id,
                        snapshot=snapshot,
                    )
            elif _is_snapshot_replay(incoming_messages, snapshot.messages):
                input_data["messages"] = []

        if resume_payload is not None and cache_key not in self._workflow_by_thread:
            workflow = self._resolve_workflow(thread_id, snapshot_scope)
            checkpoint = await self._checkpoint_storage.get_latest(workflow_name=workflow.name)
            if checkpoint is not None:
                await workflow._runner.restore_from_checkpoint(  # pyright: ignore[reportPrivateUsage]
                    checkpoint.checkpoint_id,
                    self._checkpoint_storage,
                )

        async for event in super().run(input_data):
            yield event

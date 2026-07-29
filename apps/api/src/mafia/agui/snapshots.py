from dataclasses import asdict
from typing import Any, cast

from agent_framework.ag_ui import AGUIThreadSnapshot
from mafia.db.models import AGUISnapshot, Run
from mafia.db.session import SessionFactory
from mafia.domain.enums import RunState
from sqlalchemy import delete, select


def _deduplicate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_message_id: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for message in messages:
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            by_message_id.append(message)
            continue
        position = positions.get(message_id)
        if position is None:
            positions[message_id] = len(by_message_id)
            by_message_id.append(message)
        else:
            by_message_id[position] = message
    deduplicated: list[dict[str, Any]] = []
    tool_call_ids: set[str] = set()
    for message in by_message_id:
        tool_calls_key = "tool_calls" if isinstance(message.get("tool_calls"), list) else "toolCalls"
        tool_calls_value = message.get(tool_calls_key)
        if not isinstance(tool_calls_value, list):
            deduplicated.append(message)
            continue
        unique_calls: list[object] = []
        for tool_call in cast(list[object], tool_calls_value):
            if not isinstance(tool_call, dict):
                unique_calls.append(tool_call)
                continue
            typed_tool_call = cast(dict[str, Any], tool_call)
            tool_call_id = typed_tool_call.get("id")
            if isinstance(tool_call_id, str) and tool_call_id:
                if tool_call_id in tool_call_ids:
                    continue
                tool_call_ids.add(tool_call_id)
            unique_calls.append(typed_tool_call)
        if unique_calls:
            deduplicated.append({**message, tool_calls_key: unique_calls})
        elif message.get("content"):
            deduplicated.append({**message, tool_calls_key: []})
    return deduplicated


def _request_type(interrupt: dict[str, Any]) -> str | None:
    metadata = interrupt.get("metadata")
    if not isinstance(metadata, dict):
        return None
    framework = cast(dict[str, Any], metadata).get("agent_framework")
    if not isinstance(framework, dict):
        return None
    request_type = cast(dict[str, Any], framework).get("request_type")
    return request_type if isinstance(request_type, str) else None


def _artifact_kind(interrupt: dict[str, Any]) -> str | None:
    metadata = interrupt.get("metadata")
    if not isinstance(metadata, dict):
        return None
    framework = cast(dict[str, Any], metadata).get("agent_framework")
    if not isinstance(framework, dict):
        return None
    data = cast(dict[str, Any], framework).get("data")
    if not isinstance(data, dict):
        return None
    artifact_kind = cast(dict[str, Any], data).get("artifact_kind")
    return artifact_kind if isinstance(artifact_kind, str) else None


def _active_interrupts(
    interrupts: list[dict[str, Any]] | None,
    state: RunState | None,
) -> list[dict[str, Any]] | None:
    if interrupts is None or state is None:
        return interrupts
    active: list[dict[str, Any]] = []
    for interrupt in interrupts:
        request_type = _request_type(interrupt)
        if request_type == "ArtifactDecisionRequest":
            expected = (
                RunState.AWAITING_SPEC_DECISION
                if _artifact_kind(interrupt) == "specification"
                else RunState.AWAITING_PLAN_DECISION
            )
            if state == expected:
                active.append(interrupt)
        elif request_type == "PhaseDecisionRequest":
            if state == RunState.READY_FOR_PHASE:
                active.append(interrupt)
        elif request_type == "PullRequestReviewDecisionRequest":
            if state == RunState.AWAITING_PR_REVIEW_DECISION:
                active.append(interrupt)
        else:
            active.append(interrupt)
    return active or None


class SQLiteAGUIThreadSnapshotStore:
    async def save(self, *, scope: str, thread_id: str, snapshot: AGUIThreadSnapshot) -> None:
        self._validate_key(scope, thread_id)
        async with SessionFactory() as session:
            row = await session.scalar(
                select(AGUISnapshot).where(
                    AGUISnapshot.scope == scope,
                    AGUISnapshot.thread_id == thread_id,
                )
            )
            payload = asdict(snapshot)
            messages = payload.get("messages")
            if isinstance(messages, list):
                raw_messages = cast(list[object], messages)
                payload["messages"] = _deduplicate_messages(
                    [
                        cast(dict[str, Any], message)
                        for message in raw_messages
                        if isinstance(message, dict)
                    ]
                )
            if row is None:
                session.add(AGUISnapshot(scope=scope, thread_id=thread_id, snapshot=payload))
            else:
                row.snapshot = payload
            await session.commit()

    async def get(self, *, scope: str, thread_id: str) -> AGUIThreadSnapshot | None:
        self._validate_key(scope, thread_id)
        async with SessionFactory() as session:
            row = await session.scalar(
                select(AGUISnapshot).where(
                    AGUISnapshot.scope == scope,
                    AGUISnapshot.thread_id == thread_id,
                )
            )
            if row is None:
                return None
            run_state = await session.scalar(
                select(Run.state).where(Run.thread_id == thread_id)
            )
            payload = dict(row.snapshot)
            messages = payload.get("messages")
            if isinstance(messages, list):
                raw_messages = cast(list[object], messages)
                payload["messages"] = _deduplicate_messages(
                    [
                        cast(dict[str, Any], message)
                        for message in raw_messages
                        if isinstance(message, dict)
                    ]
                )
            interrupts = payload.get("interrupt")
            raw_interrupts = cast(list[object], interrupts) if isinstance(interrupts, list) else []
            payload["interrupt"] = _active_interrupts(
                (
                    [
                        cast(dict[str, Any], interrupt)
                        for interrupt in raw_interrupts
                        if isinstance(interrupt, dict)
                    ]
                    if isinstance(interrupts, list)
                    else None
                ),
                run_state,
            )
            return AGUIThreadSnapshot(**payload)

    async def delete(self, *, scope: str, thread_id: str) -> bool:
        self._validate_key(scope, thread_id)
        async with SessionFactory() as session:
            row = await session.scalar(
                select(AGUISnapshot).where(
                    AGUISnapshot.scope == scope,
                    AGUISnapshot.thread_id == thread_id,
                )
            )
            if row is None:
                return False
            await session.execute(
                delete(AGUISnapshot).where(
                    AGUISnapshot.scope == scope,
                    AGUISnapshot.thread_id == thread_id,
                )
            )
            await session.commit()
            return True

    async def clear(self, *, scope: str | None = None) -> None:
        async with SessionFactory() as session:
            statement = delete(AGUISnapshot)
            if scope is not None:
                statement = statement.where(AGUISnapshot.scope == scope)
            await session.execute(statement)
            await session.commit()

    @staticmethod
    def _validate_key(scope: str, thread_id: str) -> None:
        if not scope or not thread_id:
            raise ValueError("scope and thread_id must be non-empty")

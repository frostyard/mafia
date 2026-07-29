import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from agent_framework.github import GitHubCopilotAgent, GitHubCopilotOptions
from copilot import CopilotClient
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionUserNotAvailable,
)
from copilot.session import PermissionRequestResult
from copilot.session_events import PermissionRequest, PermissionRequestCustomTool
from mafia.config import get_settings
from mafia.services.operations import OperationDetailProvider, tracked_operation
from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


class StructuredCopilotOptions(GitHubCopilotOptions, total=False):
    available_tools: list[str]


class ModelUnavailableError(RuntimeError):
    pass


class StructuredOutputError(RuntimeError):
    pass


def tool_name(tool: Callable[..., Any]) -> str:
    return getattr(tool, "name", None) or tool.__name__


def permission_handler(
    allowed_tools: frozenset[str],
) -> Callable[[PermissionRequest, dict[str, str]], PermissionRequestResult]:
    def handle(
        request: PermissionRequest,
        _invocation: dict[str, str],
    ) -> PermissionRequestResult:
        if isinstance(request, PermissionRequestCustomTool) and request.tool_name in allowed_tools:
            return PermissionDecisionApproveOnce()
        return PermissionDecisionUserNotAvailable()

    return handle


class CopilotAgentService:
    async def available_models(self) -> set[str]:
        client = CopilotClient()
        try:
            await client.start()
            models = await client.list_models()
            return {model.id for model in models}
        finally:
            await client.stop()

    async def require_models(self) -> None:
        available = await self.available_models()
        missing = get_settings().required_models - available
        if missing:
            raise ModelUnavailableError(f"Required Copilot models are unavailable: {sorted(missing)}")

    async def run_structured(
        self,
        *,
        model: str,
        instructions: str,
        prompt: str,
        response_type: type[OutputT],
        working_directory: Path,
        tools: Sequence[Callable[..., Any]] = (),
        timeout_seconds: float = 900,
        run_id: str | None = None,
        phase_id: str | None = None,
        operation_type: str = "model.structured_output",
        operation_key: str | None = None,
        operation_detail: dict[str, Any] | None = None,
        operation_detail_provider: OperationDetailProvider | None = None,
    ) -> OutputT:
        if run_id is None:
            return await self._run_structured(
                model=model,
                instructions=instructions,
                prompt=prompt,
                response_type=response_type,
                working_directory=working_directory,
                tools=tools,
                timeout_seconds=timeout_seconds,
            )
        async with tracked_operation(
            run_id=run_id,
            phase_id=phase_id,
            operation_type=operation_type,
            operation_key=operation_key or response_type.__name__,
            model=model,
            timeout_seconds=int(timeout_seconds),
            detail={
                "response_type": response_type.__name__,
                **(operation_detail or {}),
            },
            detail_provider=operation_detail_provider,
        ) as operation:
            result = await self._run_structured(
                model=model,
                instructions=instructions,
                prompt=prompt,
                response_type=response_type,
                working_directory=working_directory,
                tools=tools,
                timeout_seconds=timeout_seconds,
            )
            operation.set_result({"response_type": response_type.__name__})
            return result

    async def _run_structured(
        self,
        *,
        model: str,
        instructions: str,
        prompt: str,
        response_type: type[OutputT],
        working_directory: Path,
        tools: Sequence[Callable[..., Any]],
        timeout_seconds: float,
    ) -> OutputT:
        client = CopilotClient(working_directory=str(working_directory))
        allowed_tools = frozenset(tool_name(tool) for tool in tools)
        options = StructuredCopilotOptions(
            model=model,
            timeout=timeout_seconds,
            available_tools=[f"custom:{name}" for name in sorted(allowed_tools)],
            on_permission_request=permission_handler(allowed_tools),
        )
        agent = GitHubCopilotAgent(
            client=client,
            instructions=instructions,
            tools=list(tools),
            default_options=options,
        )
        try:
            await agent.start()
            session = agent.create_session()
            schema = json.dumps(response_type.model_json_schema(), separators=(",", ":"))
            request = (
                f"{prompt}\n\n"
                "Your final response must be only one JSON object matching this JSON Schema. "
                "Do not wrap it in Markdown fences or add commentary.\n"
                f"{schema}"
            )
            last_error: ValueError | json.JSONDecodeError | None = None
            for _ in range(3):
                response = await agent.run(request, session=session)
                try:
                    return response_type.model_validate_json(extract_json_object(response.text))
                except (ValueError, json.JSONDecodeError) as error:
                    last_error = error
                    request = (
                        f"Your previous response was invalid: {error}. "
                        "Return a corrected JSON object only, matching the same schema."
                    )
            raise StructuredOutputError(
                f"Model {model} did not return valid {response_type.__name__} output: {last_error}"
            )
        finally:
            await agent.stop()
            await client.stop()


def extract_json_object(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        candidate = candidate[first_newline + 1 :] if first_newline >= 0 else candidate
        if candidate.endswith("```"):
            candidate = candidate[:-3].rstrip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Response does not contain a JSON object")
    return candidate[start : end + 1]

import re
from pathlib import Path

import httpx
import pytest
from mafia.domain.enums import OperationStatus, PhaseState, RunState


def _typescript_values(source: str, name: str) -> set[str]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const", source, re.S)
    assert match is not None
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_frontend_workflow_states_match_backend() -> None:
    source = Path("apps/web/src/lib/workflow-state.ts").read_text()
    assert _typescript_values(source, "RUN_STATES") == {state.value for state in RunState}
    assert _typescript_values(source, "PHASE_STATES") == {state.value for state in PhaseState}
    assert _typescript_values(source, "OPERATION_STATUSES") == {
        status.value for status in OperationStatus
    }


@pytest.mark.asyncio
async def test_ag_ui_endpoint_is_not_registered() -> None:
    from mafia.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/ag-ui")

    assert response.status_code == 404


def test_source_has_no_ag_ui_or_framework_workflow_imports() -> None:
    source_root = Path("apps/api/src")
    source = "\n".join(
        path.read_text() for path in sorted(source_root.rglob("*.py"))
    )

    for legacy_api in (
        "agent_framework.ag_ui",
        "FileCheckpointStorage",
        "WorkflowBuilder",
        "WorkflowContext",
        "request_info",
        "response_handler",
    ):
        assert legacy_api not in source


def test_frontend_source_has_no_copilotkit_or_ag_ui_route() -> None:
    source_root = Path("apps/web/src")
    source = "\n".join(
        path.read_text()
        for path in sorted(source_root.rglob("*"))
        if path.suffix in {".ts", ".tsx"}
    )

    assert "@copilotkit/" not in source
    assert "@ag-ui/" not in source
    assert not (source_root / "app" / "api" / "ag-ui").exists()

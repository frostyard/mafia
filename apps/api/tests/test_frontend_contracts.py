import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mafia.domain.enums import OperationStatus, PhaseState, RunState


LEGACY_DOC_TERMS = re.compile(
    r"\b(?:ag-ui|copilotkit|checkpoint(?:s)?|snapshot(?:s)?|thread(?:s)?|interrupt restoration)\b",
    re.I,
)


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


@pytest.mark.parametrize(
    "prose",
    (
        "The AG-UI route is obsolete.",
        "CopilotKit no longer owns workflow controls.",
        "Do not restore checkpoints during startup.",
        "Remove stale snapshots before deployment.",
        "Durable threads are not part of this control plane.",
        "Interrupt restoration is no longer supported.",
    ),
)
def test_legacy_document_pattern_matches_removed_control_plane_prose(prose: str) -> None:
    assert LEGACY_DOC_TERMS.search(prose)


def test_deployment_guides_use_current_release_paths_for_destructive_upgrade() -> None:
    for path in (
        Path("docs/deployment.md"),
        Path("site/content/operations/deployment.md"),
    ):
        source = path.read_text()
        assert "/opt/mafia/current/bin/reset-data" in source
        assert (
            "sudo -u mafia bash -c 'cd /opt/mafia/current && "
            "MAFIA_DATA_DIR=/var/lib/mafia ./.venv/bin/python -m alembic "
            "-c alembic.ini upgrade head'"
        ) in source


def test_alembic_requires_release_root_for_relative_paths(tmp_path: Path) -> None:
    repository_root = Path.cwd()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    environment = {**os.environ, "MAFIA_DATA_DIR": str(data_dir)}
    command = [sys.executable, "-m", "alembic", "-c", str(repository_root / "alembic.ini")]

    outside_result = subprocess.run(
        [*command, "heads"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert outside_result.returncode != 0
    assert "Path doesn't exist: apps/api/migrations" in (
        outside_result.stdout + outside_result.stderr
    )

    inside_result = subprocess.run(
        [*command, "upgrade", "head"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert inside_result.returncode == 0, inside_result.stderr


def test_repository_has_no_legacy_workflow_control_plane_surfaces() -> None:
    runtime_paths = [
        *Path("apps/api/src").rglob("*.py"),
        *Path("apps/web/src").rglob("*.ts"),
        *Path("apps/web/src").rglob("*.tsx"),
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path("package.json"),
        Path("apps/web/package.json"),
        Path("apps/web/package-lock.json"),
        Path(".env.example"),
        *Path("contrib/incus").glob("*.env.example"),
        Path("contrib/Caddyfile"),
    ]
    operator_docs = [
        *Path("docs").rglob("*.md"),
        *Path("site/content").rglob("*.md"),
    ]
    approved_history = {
        Path("docs/superpowers/specs/2026-07-30-remove-ag-ui-design.md"),
        Path("docs/superpowers/plans/2026-07-30-remove-ag-ui.md"),
    }
    legacy_runtime_terms = (
        "agent_framework.ag_ui",
        "FileCheckpointStorage",
        "WorkflowBuilder",
        "WorkflowContext",
        "request_info",
        "@copilotkit/",
        "@ag-ui/",
        "useAgent",
        "useInterrupt",
        "/ag-ui",
        "copilotkit",
        "thread_id",
        "checkpoints_dir",
        "AGENT_URL",
        "NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL",
    )

    for path in runtime_paths:
        source = path.read_text()
        assert all(term.lower() not in source.lower() for term in legacy_runtime_terms), path

    for path in operator_docs:
        if path in approved_history:
            continue
        assert not LEGACY_DOC_TERMS.search(path.read_text()), path

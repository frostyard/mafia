import re
from pathlib import Path

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

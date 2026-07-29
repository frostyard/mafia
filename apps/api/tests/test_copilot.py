import pytest
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionUserNotAvailable,
)
from copilot.session_events import (
    PermissionRequestCustomTool,
    PermissionRequestRead,
)
from mafia.agents.copilot import extract_json_object, permission_handler


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('{"status":"ok"}', '{"status":"ok"}'),
        ('```json\n{"status":"ok"}\n```', '{"status":"ok"}'),
        ('Result: {"status":"ok"}', '{"status":"ok"}'),
    ],
)
def test_extract_json_object(value: str, expected: str) -> None:
    assert extract_json_object(value) == expected


def test_extract_json_object_rejects_text() -> None:
    with pytest.raises(ValueError):
        extract_json_object("not json")


def test_permission_handler_approves_allowed_custom_tool() -> None:
    handler = permission_handler(frozenset({"read_source"}))

    result = handler(
        PermissionRequestCustomTool(
            tool_description="Read a repository file",
            tool_name="read_source",
        ),
        {},
    )

    assert isinstance(result, PermissionDecisionApproveOnce)


@pytest.mark.parametrize(
    "permission_request",
    [
        PermissionRequestCustomTool(
            tool_description="Unregistered tool",
            tool_name="other_tool",
        ),
        PermissionRequestRead(
            intention="Read outside the application tool boundary",
            path="/tmp/example",
        ),
    ],
)
def test_permission_handler_denies_other_permissions(
    permission_request: PermissionRequestCustomTool | PermissionRequestRead,
) -> None:
    handler = permission_handler(frozenset({"read_source"}))

    result = handler(permission_request, {})

    assert isinstance(result, PermissionDecisionUserNotAvailable)

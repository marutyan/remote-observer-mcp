import json

import pytest
from remote_observer_mcp.execution.model import ExecutionRequest

from remote_observer_mcp.errors import ObserverError


def _request_with(argv=None, script=None, reason="Routine task"):
    payload = {
        "schema_version": 1,
        "request_id": "exec-20260808-0002",
        "target": {"host": "emma"},
        "mode": "argv" if script is None else "shell",
        "timeout_seconds": 60,
        "risk": "R1" if script is None else "R4",
        "reason": reason,
    }
    if script is None:
        payload["argv"] = argv or ["pytest", "-q"]
    else:
        payload["script"] = script
    return json.dumps(payload).encode()


@pytest.mark.parametrize(
    "raw,marker",
    [
        (_request_with(["curl", "-H", "Authorization: Bearer secret-value"]), "secret-value"),
        (_request_with(["tool", "--token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"]), "ghp_"),
        (_request_with(["tool", "OPENAI_API_KEY=sk-proj-example-secret-value"]), "sk-proj"),
        (_request_with(script="cat <<'EOF'\n-----BEGIN PRIVATE KEY-----\nabc\nEOF"), "PRIVATE KEY"),
        (_request_with(reason="password=hunter2"), "hunter2"),
    ],
)
def test_secret_like_literals_are_rejected_without_echo(raw: bytes, marker: str):
    with pytest.raises(ObserverError) as error:
        ExecutionRequest.from_json_bytes(raw)

    assert error.value.code == "secret_in_execution_request"
    assert marker not in error.value.message


def test_normal_arguments_and_reason_do_not_false_positive():
    raw = _request_with(
        ["python", "-m", "pytest", "tests/test_api.py", "-q"],
        reason="Run tests for token parsing without embedding any credential value",
    )
    request = ExecutionRequest.from_json_bytes(raw)
    assert request.argv == ("python", "-m", "pytest", "tests/test_api.py", "-q")


def test_shell_script_has_bounded_size_and_rejects_nul():
    with pytest.raises(ObserverError) as nul_error:
        ExecutionRequest.from_json_bytes(_request_with(script="printf ok\x00bad"))
    assert nul_error.value.code == "invalid_execution_request"

    with pytest.raises(ObserverError) as large_error:
        ExecutionRequest.from_json_bytes(_request_with(script="x" * (32 * 1024 + 1)))
    assert large_error.value.code == "invalid_execution_request"

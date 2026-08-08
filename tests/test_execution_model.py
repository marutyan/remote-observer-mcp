import hashlib
import json

import pytest

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.execution.model import ExecutionRequest, request_digest


def _bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _valid_payload() -> dict:
    return {
        "schema_version": 1,
        "request_id": "exec-20260808-0001",
        "target": {"host": "emma", "workspace": "paperapp"},
        "mode": "argv",
        "argv": ["python", "-m", "pytest", "-q"],
        "timeout_seconds": 120,
        "risk": "R1",
        "reason": "Run repository tests",
    }


def test_execution_request_parses_exact_argv_without_command_string():
    request = ExecutionRequest.from_json_bytes(_bytes(_valid_payload()))

    assert request.schema_version == 1
    assert request.request_id == "exec-20260808-0001"
    assert request.target.host == "emma"
    assert request.target.workspace == "paperapp"
    assert request.mode == "argv"
    assert request.argv == ("python", "-m", "pytest", "-q")
    assert request.script is None
    assert request.timeout_seconds == 120
    assert request.risk == "R1"


def test_request_digest_is_sha256_of_original_bytes():
    raw = b'{"schema_version":1,"request_id":"exec-20260808-0001"}\n'
    assert request_digest(raw) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p | {"schema_version": 2},
        lambda p: p | {"request_id": "../escape"},
        lambda p: p | {"request_id": "exec-20260808-1"},
        lambda p: p | {"timeout_seconds": 0},
        lambda p: p | {"timeout_seconds": 901},
        lambda p: p | {"risk": "R0"},
        lambda p: p | {"mode": "argv", "argv": []},
        lambda p: p | {"mode": "argv", "argv": ["echo", "bad\x00arg"]},
        lambda p: p | {"unknown_key": True},
        lambda p: p | {"target": {"host": "bad\nname"}},
    ],
)
def test_execution_request_rejects_invalid_schema_shapes(mutate):
    payload = mutate(_valid_payload())

    with pytest.raises(ObserverError) as error:
        ExecutionRequest.from_json_bytes(_bytes(payload))

    assert error.value.code == "invalid_execution_request"


def test_argv_mode_rejects_script_field():
    payload = _valid_payload() | {"script": "echo nope"}
    with pytest.raises(ObserverError) as error:
        ExecutionRequest.from_json_bytes(_bytes(payload))
    assert error.value.code == "invalid_execution_request"


def test_shell_mode_requires_r4_and_script_only():
    payload = _valid_payload()
    payload.update({"mode": "shell", "risk": "R4", "script": "printf '%s\\n' ok"})
    payload.pop("argv")

    request = ExecutionRequest.from_json_bytes(_bytes(payload))
    assert request.mode == "shell"
    assert request.risk == "R4"
    assert request.argv is None
    assert request.script == "printf '%s\\n' ok"

    payload["risk"] = "R3"
    with pytest.raises(ObserverError) as error:
        ExecutionRequest.from_json_bytes(_bytes(payload))
    assert error.value.code == "invalid_execution_request"


def test_invalid_json_and_non_object_fail_closed():
    for raw in (b"not-json", b"[]", b"null"):
        with pytest.raises(ObserverError) as error:
            ExecutionRequest.from_json_bytes(raw)
        assert error.value.code == "invalid_execution_request"

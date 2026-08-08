import hashlib
import json
import sys
from pathlib import Path

import pytest

from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.execution.executor import execute_request
from remote_observer_mcp.execution.model import ExecutionRequest
from remote_observer_mcp.server import create_server


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _payload(*, host="gateway", workspace="app", mode="argv") -> dict:
    payload = {
        "schema_version": 1,
        "request_id": "exec-20260808-0100",
        "target": {"host": host, "workspace": workspace},
        "mode": mode,
        "timeout_seconds": 30,
        "risk": "R1" if mode == "argv" else "R4",
        "reason": "Acceptance test",
    }
    if mode == "argv":
        payload["argv"] = [sys.executable, "-c", "print('approved-run')"]
    else:
        payload["script"] = "printf '%s\\n' approved-shell"
    return payload


def _config(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[hosts.gateway]\ntransport = "local"\n\n[hosts.remote]\ntransport = "ssh"\nssh_alias = "safe-remote"\n\n[workspaces.app]\nhost = "gateway"\nroot = "{workspace}"\n\n[workspaces.remoteapp]\nhost = "remote"\nroot = "/srv/app"\n''',
        encoding="utf-8",
    )
    return load_config(config_path), workspace


@pytest.mark.asyncio
async def test_local_argv_executes_without_shell_and_returns_digest_evidence(tmp_path: Path):
    config, _ = _config(tmp_path)
    raw = _raw(_payload())
    request = ExecutionRequest.from_json_bytes(raw)

    evidence = await execute_request(config, request, hashlib.sha256(raw).hexdigest())

    assert evidence.request_id == request.request_id
    assert evidence.digest == hashlib.sha256(raw).hexdigest()
    assert evidence.target_host == "gateway"
    assert evidence.target_workspace == "app"
    assert evidence.mode == "argv"
    assert evidence.risk == "R1"
    assert evidence.exit_code == 0
    assert evidence.stdout.strip() == "approved-run"
    assert evidence.truncated is False


@pytest.mark.asyncio
async def test_workspace_target_must_belong_to_requested_host(tmp_path: Path):
    config, _ = _config(tmp_path)
    raw = _raw(_payload(host="remote", workspace="app"))
    request = ExecutionRequest.from_json_bytes(raw)

    with pytest.raises(ObserverError) as error:
        await execute_request(config, request, hashlib.sha256(raw).hexdigest())
    assert error.value.code == "invalid_execution_target"


@pytest.mark.asyncio
async def test_r4_shell_uses_fixed_bash_and_is_not_registered_as_mcp(tmp_path: Path):
    config, _ = _config(tmp_path)
    raw = _raw(_payload(mode="shell"))
    request = ExecutionRequest.from_json_bytes(raw)

    evidence = await execute_request(config, request, hashlib.sha256(raw).hexdigest())
    assert evidence.exit_code == 0
    assert evidence.stdout.strip() == "approved-shell"
    assert evidence.mode == "shell"
    assert evidence.risk == "R4"

    tools = {tool.name for tool in await create_server(config).list_tools()}
    assert {"execute", "shell", "run_command", "approved_execute"}.isdisjoint(tools)


@pytest.mark.asyncio
async def test_nonzero_exit_is_reported_without_retry(tmp_path: Path):
    config, _ = _config(tmp_path)
    payload = _payload()
    payload["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
    raw = _raw(payload)
    request = ExecutionRequest.from_json_bytes(raw)

    evidence = await execute_request(config, request, hashlib.sha256(raw).hexdigest())
    assert evidence.exit_code == 7


@pytest.mark.asyncio
async def test_output_is_redacted(tmp_path: Path):
    config, _ = _config(tmp_path)
    payload = _payload()
    payload["argv"] = [sys.executable, "-c", "print('token=super-secret-value')"]
    raw = _raw(payload)
    request = ExecutionRequest.from_json_bytes(raw)

    evidence = await execute_request(config, request, hashlib.sha256(raw).hexdigest())
    assert "super-secret-value" not in evidence.stdout
    assert "[REDACTED]" in evidence.stdout
    assert evidence.redacted is True

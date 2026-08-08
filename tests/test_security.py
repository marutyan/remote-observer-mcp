import json
from pathlib import Path

import pytest

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.server import create_server


@pytest.mark.asyncio
async def test_audit_emits_metadata_only_and_never_raw_result(capsys):
    async def operation():
        return {
            "lines": ["sensitive-observed-content"],
            "truncated": True,
            "redacted": True,
        }

    result = await run_observed_tool(
        tool="service_logs",
        host_id="emma",
        resource_id="callbot",
        operation=operation,
    )

    assert result["ok"] is True
    event = json.loads(capsys.readouterr().err.strip())
    assert event["tool"] == "service_logs"
    assert event["host_id"] == "emma"
    assert event["resource_id"] == "callbot"
    assert event["outcome"] == "success"
    assert event["truncated"] is True
    assert event["redacted"] is True
    serialized = json.dumps(event)
    assert "sensitive-observed-content" not in serialized
    assert "lines" not in event
    assert "argv" not in event


@pytest.mark.asyncio
async def test_audit_does_not_echo_invalid_logical_ids(capsys):
    async def operation():
        raise ObserverError("unknown_host", "unknown host")

    result = await run_observed_tool(
        tool="system_status",
        host_id="bad\nsecret-marker",
        resource_id=None,
        operation=operation,
    )

    assert result == {
        "ok": False,
        "error": {"code": "unknown_host", "message": "unknown host"},
    }
    event = json.loads(capsys.readouterr().err.strip())
    assert event["host_id"] == "<invalid>"
    assert "secret-marker" not in json.dumps(event)
    assert event["outcome"] == "unknown_host"


@pytest.mark.asyncio
async def test_unexpected_error_is_audited_without_being_swallowed(capsys):
    async def operation():
        raise RuntimeError("sensitive internal details")

    with pytest.raises(RuntimeError, match="sensitive internal details"):
        await run_observed_tool(
            tool="repo_diff",
            host_id="emma",
            resource_id="paperapp",
            operation=operation,
        )

    event = json.loads(capsys.readouterr().err.strip())
    assert event["outcome"] == "internal_error"
    assert event["tool"] == "repo_diff"
    assert "sensitive internal details" not in json.dumps(event)


@pytest.mark.asyncio
async def test_every_mcp_tool_is_closed_read_only_and_has_no_arbitrary_target(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.gateway]
transport = "local"
gpu = true

[hosts.gateway.services.app]
unit = "app.service"
logs = true

[hosts.gateway.containers.api]
name = "api"
logs = true

[hosts.gateway.repos.app]
path = "/srv/app"

[hosts.gateway.processes.worker]
name = "worker"
""",
        encoding="utf-8",
    )
    tools = await create_server(load_config(path)).list_tools()
    forbidden_fields = {"command", "hostname", "path", "ssh_alias", "unit", "container_name"}
    forbidden_tools = {"execute", "shell", "ssh", "read_file", "write_file", "restart", "stop"}

    names = {tool.name for tool in tools}
    assert names.isdisjoint(forbidden_tools)
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        properties = set(tool.inputSchema.get("properties", {}))
        assert properties.isdisjoint(forbidden_fields)


@pytest.mark.parametrize(
    "host_id",
    [
        "bad;id",
        "$(id)",
        "`id`",
        "../host",
        "bad\nname",
        "bad\x01name",
        "a" * 65,
    ],
)
def test_adversarial_host_ids_fail_closed_without_echo(tmp_path: Path, host_id: str):
    path = tmp_path / "config.toml"
    path.write_text("[hosts.safe]\ntransport = \"local\"\n", encoding="utf-8")
    config = load_config(path)

    with pytest.raises(ObserverError) as error:
        config.host(host_id)

    assert error.value.code == "unknown_host"
    assert host_id not in error.value.message

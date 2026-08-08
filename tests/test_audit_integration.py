import json
from pathlib import Path

import pytest

from remote_observer_mcp.config import load_config
from remote_observer_mcp.server import create_server


@pytest.mark.asyncio
async def test_list_hosts_tool_call_emits_one_metadata_audit_event(tmp_path: Path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[hosts.gateway]\ntransport = \"local\"\n", encoding="utf-8")
    server = create_server(load_config(path))

    await server.call_tool("list_hosts", {})

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["tool"] == "list_hosts"
    assert event["host_id"] is None
    assert event["resource_id"] is None
    assert event["outcome"] == "success"
    assert set(event) == {
        "timestamp",
        "tool",
        "host_id",
        "resource_id",
        "duration_ms",
        "outcome",
        "truncated",
        "redacted",
    }

import importlib
from pathlib import Path

import pytest

from remote_observer_mcp.config import load_config

_CORE_TOOLS = {
    "list_hosts",
    "host_overview",
    "system_status",
    "disk_usage",
    "process_status",
}


def _config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.gateway]
transport = "local"

[hosts.remote]
transport = "ssh"
ssh_alias = "remote"
gpu = true

[hosts.remote.processes.worker]
name = "paper-worker"
""",
        encoding="utf-8",
    )
    return load_config(path)


@pytest.mark.asyncio
async def test_server_keeps_core_tools_and_all_tools_read_only(tmp_path: Path):
    server_module = importlib.import_module("remote_observer_mcp.server")
    server = server_module.create_server(_config(tmp_path))

    tools = await server.list_tools()
    by_name = {tool.name: tool for tool in tools}

    assert _CORE_TOOLS.issubset(by_name)
    for tool in by_name.values():
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False


def test_tool_schemas_do_not_accept_arbitrary_remote_targets(tmp_path: Path):
    server_module = importlib.import_module("remote_observer_mcp.server")
    server = server_module.create_server(_config(tmp_path))

    async def collect_properties():
        tools = await server.list_tools()
        return {
            tool.name: set(tool.inputSchema.get("properties", {}))
            for tool in tools
        }

    import asyncio

    properties = asyncio.run(collect_properties())
    forbidden = {"command", "hostname", "path", "ssh_alias"}
    assert all(forbidden.isdisjoint(names) for names in properties.values())

from collections import deque
from pathlib import Path

import pytest

from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.observers.diagnostics import dns_lookup, process_list
from remote_observer_mcp.server import create_server


class FakeTransport:
    def __init__(self, results: list[CommandResult]):
        self.results = deque(results)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return self.results.popleft()


def _result(stdout: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, "", 1, False, False)


@pytest.mark.asyncio
async def test_process_list_falls_back_to_ps_without_environment_or_full_args():
    transport = FakeTransport([_result(exit_code=1), _result(), _result("1 root S init 0.1 0.2\n")])
    rows = await process_list(transport, 25)
    assert rows[0]["pid"] == 1
    argv = transport.commands[-1].argv
    assert argv[0] == "ps"
    joined = " ".join(argv)
    assert "environ" not in joined
    assert "args=" not in joined


@pytest.mark.asyncio
async def test_dns_lookup_accepts_name_only_and_does_not_shell_execute():
    transport = FakeTransport([_result("127.0.0.1 STREAM example.test\n")])
    rows = await dns_lookup(transport, "example.test")
    assert rows
    assert transport.commands[0].argv == ("getent", "ahosts", "example.test")

    with pytest.raises(ObserverError) as error:
        await dns_lookup(FakeTransport([]), "https://example.test/$(id)")
    assert error.value.code == "invalid_dns_name"


@pytest.mark.asyncio
async def test_host_diagnostic_tools_register_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("[hosts.gateway]\ntransport = \"local\"\n", encoding="utf-8")
    tools = {tool.name: tool for tool in await create_server(load_config(path)).list_tools()}
    expected = {
        "process_list",
        "process_tree",
        "network_listeners",
        "network_interfaces",
        "network_routes",
        "dns_lookup",
        "filesystem_status",
        "disk_hotspots",
        "user_sessions",
        "hardware_info",
        "sensor_status",
    }
    assert expected.issubset(tools)
    for name in expected:
        tool = tools[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        fields = set(tool.inputSchema.get("properties", {}))
        assert {"command", "script", "hostname", "path", "url"}.isdisjoint(fields)

from collections import deque
from pathlib import Path

import pytest
from remote_observer_mcp.observers.container_ext import container_stats
from remote_observer_mcp.observers.service_ext import journal_query

from remote_observer_mcp.config import ContainerConfig, ServiceConfig, load_config
from remote_observer_mcp.models import CommandResult, CommandSpec
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
async def test_journal_query_is_bound_to_service_and_clamps_filters():
    transport = FakeTransport([_result("line\n")])
    result = await journal_query(transport, ServiceConfig("app.service", logs=True), 99999, "err", 999)
    assert result["lines"] == ["line"]
    argv = transport.commands[0].argv
    assert "--unit=app.service" in argv
    assert "--since=-10080 minutes" in argv
    assert "--priority=err" in argv
    assert "--lines=500" in argv


@pytest.mark.asyncio
async def test_container_stats_never_uses_inspect_or_exec():
    transport = FakeTransport([_result("api\t2.0%\t10MiB / 1GiB\n")])
    rows = await container_stats(transport, {"api": ContainerConfig("api")})
    assert rows[0]["id"] == "api"
    argv = transport.commands[0].argv
    assert argv[:3] == ("docker", "stats", "--no-stream")
    assert "inspect" not in argv
    assert "exec" not in argv


@pytest.mark.asyncio
async def test_extension_tools_register_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        f'''[hosts.gateway]\ntransport = "local"\ngpu = true\n[hosts.gateway.services.app]\nunit = "app.service"\nlogs = true\n[hosts.gateway.containers.api]\nname = "api"\n[workspaces.app]\nhost = "gateway"\nroot = "{tmp_path}"\ncompose = true\n''',
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(path)).list_tools()}
    expected = {
        "service_failures",
        "systemd_timers",
        "journal_query",
        "container_stats",
        "compose_status",
        "gpu_processes",
    }
    assert expected.issubset(tools)
    for name in expected:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True

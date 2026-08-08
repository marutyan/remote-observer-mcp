import importlib
from collections import deque
from pathlib import Path

import pytest

from remote_observer_mcp.config import ServiceConfig, load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.server import create_server


class FakeTransport:
    def __init__(self, results: list[CommandResult]):
        self.results = deque(results)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return self.results.popleft()


def _result(stdout: str = "", *, exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, "", 1, False, False)


@pytest.mark.asyncio
async def test_service_status_parses_fixed_systemctl_properties():
    module = importlib.import_module("remote_observer_mcp.observers.systemd")
    transport = FakeTransport(
        [
            _result(
                "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
                "UnitFileState=enabled\nDescription=Example service\n"
            )
        ]
    )

    status = await module.service_status(transport, ServiceConfig("example.service"))

    assert status == {
        "load_state": "loaded",
        "active_state": "inactive",
        "sub_state": "dead",
        "unit_file_state": "enabled",
        "description": "Example service",
    }
    assert transport.commands[0].argv == (
        "systemctl",
        "show",
        "--no-pager",
        "--property=LoadState,ActiveState,SubState,UnitFileState,Description",
        "example.service",
    )


@pytest.mark.asyncio
async def test_service_logs_require_opt_in_and_clamp_lines():
    module = importlib.import_module("remote_observer_mcp.observers.systemd")
    denied = FakeTransport([])
    with pytest.raises(ObserverError) as error:
        await module.service_logs(denied, ServiceConfig("example.service", logs=False), 100)
    assert error.value.code == "unsupported_capability"
    assert denied.commands == []

    allowed = FakeTransport([_result("line one\nline two\n")])
    logs = await module.service_logs(allowed, ServiceConfig("example.service", logs=True), 9999)
    assert logs["lines"] == ["line one", "line two"]
    assert allowed.commands[0].argv == (
        "journalctl",
        "--no-pager",
        "--output=short-iso",
        "--lines=500",
        "--unit=example.service",
    )


@pytest.mark.asyncio
async def test_systemd_tools_are_registered_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.gateway]
transport = "local"
[hosts.gateway.services.example]
unit = "example.service"
logs = true
""",
        encoding="utf-8",
    )
    server = create_server(load_config(path))
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert {"service_status", "service_logs"}.issubset(tools)
    for name in ("service_status", "service_logs"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False

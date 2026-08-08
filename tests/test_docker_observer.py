import importlib
from collections import deque
from pathlib import Path

import pytest

from remote_observer_mcp.config import ContainerConfig, load_config
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


def _result(stdout: str = "", *, stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, stderr, 1, False, False)


@pytest.mark.asyncio
async def test_container_list_queries_only_registered_containers():
    module = importlib.import_module("remote_observer_mcp.observers.docker")
    transport = FakeTransport(
        [
            _result("api\tUp 2 hours\tapp:latest\n"),
            _result(""),
        ]
    )
    containers = {
        "api": ContainerConfig("api"),
        "worker": ContainerConfig("worker"),
    }

    result = await module.container_list(transport, containers)

    assert result == [
        {"id": "api", "present": True, "status": "Up 2 hours", "image": "app:latest"},
        {"id": "worker", "present": False, "status": None, "image": None},
    ]
    assert [command.argv for command in transport.commands] == [
        (
            "docker",
            "ps",
            "--all",
            "--filter",
            "name=^/api$",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}",
        ),
        (
            "docker",
            "ps",
            "--all",
            "--filter",
            "name=^/worker$",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}",
        ),
    ]
    assert all("inspect" not in command.argv for command in transport.commands)


@pytest.mark.asyncio
async def test_container_logs_require_opt_in_and_clamp_lines():
    module = importlib.import_module("remote_observer_mcp.observers.docker")
    denied = FakeTransport([])
    with pytest.raises(ObserverError) as error:
        await module.container_logs(denied, ContainerConfig("api", logs=False), 100)
    assert error.value.code == "unsupported_capability"
    assert denied.commands == []

    allowed = FakeTransport([_result("out\n", stderr="err\n")])
    result = await module.container_logs(allowed, ContainerConfig("api", logs=True), 0)
    assert result == {
        "stdout": ["out"],
        "stderr": ["err"],
        "truncated": False,
        "redacted": False,
    }
    assert allowed.commands[0].argv == ("docker", "logs", "--tail", "1", "api")


@pytest.mark.asyncio
async def test_docker_tools_are_registered_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.gateway]
transport = "local"
[hosts.gateway.containers.api]
name = "api"
logs = true
""",
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(path)).list_tools()}

    assert {"container_list", "container_logs"}.issubset(tools)
    for name in ("container_list", "container_logs"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False

import importlib
from collections import deque
from pathlib import Path

import pytest

from remote_observer_mcp.config import load_config
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
async def test_gpu_status_parses_fixed_nvidia_query():
    module = importlib.import_module("remote_observer_mcp.observers.gpu")
    transport = FakeTransport([_result("0, NVIDIA RTX 5080, 73, 2048, 16384, 61\n")])

    result = await module.gpu_status(transport)

    assert result == [
        {
            "index": 0,
            "name": "NVIDIA RTX 5080",
            "utilization_percent": 73,
            "memory_used_mib": 2048,
            "memory_total_mib": 16384,
            "temperature_c": 61,
        }
    ]
    assert transport.commands[0].argv == (
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    )


@pytest.mark.asyncio
async def test_gpu_status_normalizes_missing_binary():
    module = importlib.import_module("remote_observer_mcp.observers.gpu")
    transport = FakeTransport([_result(stderr="nvidia-smi: command not found", exit_code=127)])

    with pytest.raises(ObserverError) as error:
        await module.gpu_status(transport)

    assert error.value.code == "unsupported_capability"


@pytest.mark.asyncio
async def test_gpu_tool_requires_host_capability_and_is_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.no_gpu]
transport = "local"

[hosts.gpu]
transport = "local"
gpu = true
""",
        encoding="utf-8",
    )
    server = create_server(load_config(path))
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert "gpu_status" in tools
    assert tools["gpu_status"].annotations is not None
    assert tools["gpu_status"].annotations.readOnlyHint is True
    assert tools["gpu_status"].annotations.destructiveHint is False

    result = await server.call_tool("gpu_status", {"host": "no_gpu"})
    assert result.structuredContent == {
        "ok": False,
        "error": {
            "code": "unsupported_capability",
            "message": "GPU observation is not enabled for this host",
        },
    }

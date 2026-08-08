import importlib
from collections import deque

import pytest

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec


class FakeTransport:
    def __init__(self, results: list[CommandResult]):
        self.results = deque(results)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return self.results.popleft()


def _result(stdout: str = "", *, stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
        truncated=False,
        redacted=False,
    )


@pytest.mark.asyncio
async def test_system_status_returns_structured_linux_summary():
    system = importlib.import_module("remote_observer_mcp.observers.system")
    transport = FakeTransport(
        [
            _result("Linux\n"),
            _result("up 2 days, 3 hours\n"),
            _result("0.10 0.20 0.30 1/100 999\n"),
            _result(
                "total used free shared buff/cache available\n"
                "Mem: 1000 400 100 10 500 600\n"
            ),
        ]
    )

    result = await system.system_status(transport)

    assert result == {
        "os": "Linux",
        "uptime": "up 2 days, 3 hours",
        "load_average": {"1m": 0.1, "5m": 0.2, "15m": 0.3},
        "memory_bytes": {
            "total": 1000,
            "used": 400,
            "free": 100,
            "shared": 10,
            "buff_cache": 500,
            "available": 600,
        },
    }
    assert [command.argv for command in transport.commands] == [
        ("uname", "-s"),
        ("uptime", "-p"),
        ("cat", "/proc/loadavg"),
        ("free", "-b"),
    ]


@pytest.mark.asyncio
async def test_system_status_rejects_non_linux_target():
    system = importlib.import_module("remote_observer_mcp.observers.system")
    transport = FakeTransport([_result("Darwin\n")])

    with pytest.raises(ObserverError) as error:
        await system.system_status(transport)

    assert error.value.code == "unsupported_capability"


@pytest.mark.asyncio
async def test_disk_usage_parses_posix_output():
    system = importlib.import_module("remote_observer_mcp.observers.system")
    transport = FakeTransport(
        [
            _result(
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/sda1 1000 400 600 40% /\n"
                "tmpfs 100 10 90 10% /run\n"
            )
        ]
    )

    result = await system.disk_usage(transport)

    assert result == [
        {
            "filesystem": "/dev/sda1",
            "size_bytes": 1024000,
            "used_bytes": 409600,
            "available_bytes": 614400,
            "capacity": "40%",
            "mount": "/",
        },
        {
            "filesystem": "tmpfs",
            "size_bytes": 102400,
            "used_bytes": 10240,
            "available_bytes": 92160,
            "capacity": "10%",
            "mount": "/run",
        },
    ]
    assert transport.commands[0].argv == ("df", "-Pk")


@pytest.mark.asyncio
async def test_process_status_returns_only_exact_match_pids():
    system = importlib.import_module("remote_observer_mcp.observers.system")
    running_transport = FakeTransport([_result("42\n43\n")])
    stopped_transport = FakeTransport([_result(exit_code=1)])

    running = await system.process_status(running_transport, "paper-worker")
    stopped = await system.process_status(stopped_transport, "paper-worker")

    assert running == {"running": True, "pids": [42, 43]}
    assert stopped == {"running": False, "pids": []}
    assert running_transport.commands[0].argv == ("pgrep", "-x", "paper-worker")

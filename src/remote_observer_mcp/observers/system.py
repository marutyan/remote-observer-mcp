from __future__ import annotations

from typing import Any

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports.base import Transport


async def system_status(transport: Transport) -> dict[str, Any]:
    os_result = await _run_checked(transport, CommandSpec(argv=("uname", "-s")))
    operating_system = os_result.stdout.strip()
    if operating_system != "Linux":
        raise ObserverError("unsupported_capability", "system observer supports Linux only")

    uptime = await _run_checked(transport, CommandSpec(argv=("uptime", "-p")))
    load = await _run_checked(transport, CommandSpec(argv=("cat", "/proc/loadavg")))
    memory = await _run_checked(transport, CommandSpec(argv=("free", "-b")))

    try:
        load_fields = load.stdout.split()
        load_average = {
            "1m": float(load_fields[0]),
            "5m": float(load_fields[1]),
            "15m": float(load_fields[2]),
        }
        memory_line = next(line for line in memory.stdout.splitlines() if line.startswith("Mem:"))
        memory_fields = memory_line.split()
        if len(memory_fields) < 7:
            raise ValueError("incomplete memory output")
        memory_bytes = {
            "total": int(memory_fields[1]),
            "used": int(memory_fields[2]),
            "free": int(memory_fields[3]),
            "shared": int(memory_fields[4]),
            "buff_cache": int(memory_fields[5]),
            "available": int(memory_fields[6]),
        }
    except (IndexError, StopIteration, ValueError) as error:
        raise ObserverError("command_failed", "unexpected system command output") from error

    return {
        "os": operating_system,
        "uptime": uptime.stdout.strip(),
        "load_average": load_average,
        "memory_bytes": memory_bytes,
    }


async def disk_usage(transport: Transport) -> list[dict[str, Any]]:
    result = await _run_checked(transport, CommandSpec(argv=("df", "-Pk")))
    entries: list[dict[str, Any]] = []
    lines = result.stdout.splitlines()
    if not lines:
        raise ObserverError("command_failed", "unexpected disk command output")

    try:
        for line in lines[1:]:
            if not line.strip():
                continue
            fields = line.split(maxsplit=5)
            if len(fields) != 6:
                raise ValueError("unexpected disk row")
            filesystem, size, used, available, capacity, mount = fields
            entries.append(
                {
                    "filesystem": filesystem,
                    "size_bytes": int(size) * 1024,
                    "used_bytes": int(used) * 1024,
                    "available_bytes": int(available) * 1024,
                    "capacity": capacity,
                    "mount": mount,
                }
            )
    except ValueError as error:
        raise ObserverError("command_failed", "unexpected disk command output") from error
    return entries


async def process_status(transport: Transport, process_name: str) -> dict[str, Any]:
    result = await transport.run(CommandSpec(argv=("pgrep", "-x", process_name)))
    if result.exit_code == 1:
        return {"running": False, "pids": []}
    if result.exit_code != 0:
        raise ObserverError("command_failed", "process observation failed")

    try:
        pids = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    except ValueError as error:
        raise ObserverError("command_failed", "unexpected process command output") from error
    return {"running": bool(pids), "pids": pids}


async def _run_checked(transport: Transport, command: CommandSpec) -> CommandResult:
    result = await transport.run(command)
    if result.exit_code != 0:
        raise ObserverError("command_failed", "observer command failed")
    return result

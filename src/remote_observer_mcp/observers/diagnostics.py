from __future__ import annotations

import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.backends import resolve_backend
from remote_observer_mcp.config import AppConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_DNS_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$")


def _ok(result: CommandResult, message: str) -> CommandResult:
    if result.exit_code != 0:
        raise ObserverError("command_failed", message)
    return result


async def _available(transport: Transport, candidates: tuple[str, ...]) -> str | None:
    for executable in candidates:
        result = await transport.run(
            CommandSpec(
                argv=("sh", "-c", f"command -v {executable} >/dev/null 2>&1"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
        )
        if result.exit_code == 0:
            return executable
    return None


async def process_list(transport: Transport, limit: int = 100) -> list[dict[str, Any]]:
    bounded = min(max(int(limit), 1), 500)
    backend = await resolve_backend(transport, "process_list")
    if backend.variant == "procs":
        result = await transport.run(
            CommandSpec(argv=("procs", "--json", "--no-header"))
        )
        if result.exit_code != 0:
            raise ObserverError("command_failed", "process observation failed")
        rows: list[dict[str, Any]] = []
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            payload = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "pid": item.get("PID") or item.get("pid"),
                    "user": item.get("User") or item.get("user"),
                    "state": item.get("State") or item.get("state"),
                    "name": item.get("Command") or item.get("name"),
                    "cpu_percent": item.get("CPU") or item.get("cpu"),
                    "memory_percent": item.get("Memory") or item.get("memory"),
                }
            )
            if len(rows) >= bounded:
                break
        return rows

    result = _ok(
        await transport.run(
            CommandSpec(
                argv=(
                    "ps",
                    "-eo",
                    "pid=,user=,stat=,comm=,%cpu=,%mem=",
                    "--sort=-%cpu",
                )
            )
        ),
        "process observation failed",
    )
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid,
                "user": fields[1],
                "state": fields[2],
                "name": fields[3],
                "cpu_percent": fields[4],
                "memory_percent": fields[5],
            }
        )
        if len(rows) >= bounded:
            break
    return rows


async def process_tree(transport: Transport, process: int | None = None) -> dict[str, Any]:
    argv = ("ps", "-eo", "pid=,ppid=,user=,stat=,comm=", "--forest")
    result = _ok(await transport.run(CommandSpec(argv=argv)), "process tree observation failed")
    lines = result.stdout.splitlines()
    if process is not None:
        needle = str(max(int(process), 1))
        lines = [line for line in lines if line.lstrip().startswith(needle + " ")]
    return {"lines": lines[:500], "truncated": len(lines) > 500 or result.truncated}


async def network_listeners(transport: Transport) -> dict[str, Any]:
    executable = await _available(transport, ("ss", "lsof"))
    if executable == "ss":
        argv = ("ss", "-lntup", "-H")
    elif executable == "lsof":
        argv = ("lsof", "-nP", "-i", "-sTCP:LISTEN")
    else:
        raise ObserverError("unsupported_capability", "listener tooling is unavailable")
    result = _ok(await transport.run(CommandSpec(argv=argv)), "listener observation failed")
    return {"backend": executable, "lines": result.stdout.splitlines()[:500], "truncated": result.truncated}


async def network_interfaces(transport: Transport) -> dict[str, Any]:
    executable = await _available(transport, ("ip", "ifconfig"))
    if executable == "ip":
        argv = ("ip", "-brief", "address", "show")
    elif executable == "ifconfig":
        argv = ("ifconfig", "-a")
    else:
        raise ObserverError("unsupported_capability", "interface tooling is unavailable")
    result = _ok(await transport.run(CommandSpec(argv=argv)), "interface observation failed")
    return {"backend": executable, "lines": result.stdout.splitlines()[:500]}


async def network_routes(transport: Transport) -> dict[str, Any]:
    executable = await _available(transport, ("ip", "netstat"))
    if executable == "ip":
        argv = ("ip", "route", "show")
    elif executable == "netstat":
        argv = ("netstat", "-rn")
    else:
        raise ObserverError("unsupported_capability", "route tooling is unavailable")
    result = _ok(await transport.run(CommandSpec(argv=argv)), "route observation failed")
    return {"backend": executable, "lines": result.stdout.splitlines()[:500]}


async def dns_lookup(transport: Transport, name: str) -> list[str]:
    if not isinstance(name, str) or not _DNS_RE.fullmatch(name):
        raise ObserverError("invalid_dns_name", "invalid DNS name")
    result = _ok(
        await transport.run(CommandSpec(argv=("getent", "ahosts", name))),
        "DNS lookup failed",
    )
    return result.stdout.splitlines()[:100]


async def filesystem_status(transport: Transport) -> dict[str, Any]:
    result = _ok(
        await transport.run(CommandSpec(argv=("df", "-P", "-h"))),
        "filesystem observation failed",
    )
    data: dict[str, Any] = {"df": result.stdout.splitlines()[:300]}
    for name, argv in (
        ("mounts", ("findmnt", "--list", "--noheadings", "--output", "TARGET,SOURCE,FSTYPE,OPTIONS")),
        ("block_devices", ("lsblk", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINTS", "--noheadings")),
    ):
        probe = await transport.run(CommandSpec(argv=argv))
        if probe.exit_code == 0:
            data[name] = probe.stdout.splitlines()[:300]
    return data


async def disk_hotspots(transport: Transport, depth: int = 1) -> dict[str, Any]:
    level = min(max(int(depth), 1), 3)
    backend = await resolve_backend(transport, "disk_hotspots")
    if backend.variant == "dust":
        argv = ("dust", "-d", str(level), "-n", "100", "/")
    else:
        argv = ("du", "-x", "-h", f"--max-depth={level}", "/")
    result = await transport.run(CommandSpec(argv=argv, timeout_seconds=15))
    if result.exit_code not in {0, 1}:
        raise ObserverError("command_failed", "disk hotspot observation failed")
    return {"backend": backend.variant, "lines": result.stdout.splitlines()[-200:], "truncated": result.truncated}


async def user_sessions(transport: Transport) -> list[str]:
    result = _ok(await transport.run(CommandSpec(argv=("who",))), "session observation failed")
    return result.stdout.splitlines()[:200]


async def hardware_info(transport: Transport) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name, argv in (
        ("kernel", ("uname", "-a")),
        ("cpu", ("lscpu",)),
        ("memory", ("free", "-m")),
    ):
        result = await transport.run(CommandSpec(argv=argv))
        if result.exit_code == 0:
            data[name] = result.stdout.splitlines()[:300]
    if not data:
        raise ObserverError("unsupported_capability", "hardware tooling is unavailable")
    return data


async def sensor_status(transport: Transport) -> list[str]:
    result = await transport.run(CommandSpec(argv=("sensors",)))
    if result.exit_code == 127 or "command not found" in result.stderr.lower():
        raise ObserverError("unsupported_capability", "sensor tooling is unavailable")
    _ok(result, "sensor observation failed")
    return result.stdout.splitlines()[:300]


def register_tools(server: FastMCP, config: AppConfig) -> None:
    def tx(host: str) -> Transport:
        return transport_for_host(config.host(host))

    def register(name: str, function: Any) -> None:
        server.tool(name=name, annotations=_READ_ONLY, structured_output=True)(function)

    async def process_list_tool(host: str, limit: int = 100) -> dict[str, Any]:
        return await run_observed_tool(tool="process_list", host_id=host, resource_id=None, operation=lambda: process_list(tx(host), limit))

    async def process_tree_tool(host: str, process: int | None = None) -> dict[str, Any]:
        return await run_observed_tool(tool="process_tree", host_id=host, resource_id=None, operation=lambda: process_tree(tx(host), process))

    async def network_listeners_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="network_listeners", host_id=host, resource_id=None, operation=lambda: network_listeners(tx(host)))

    async def network_interfaces_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="network_interfaces", host_id=host, resource_id=None, operation=lambda: network_interfaces(tx(host)))

    async def network_routes_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="network_routes", host_id=host, resource_id=None, operation=lambda: network_routes(tx(host)))

    async def dns_lookup_tool(host: str, name: str) -> dict[str, Any]:
        return await run_observed_tool(tool="dns_lookup", host_id=host, resource_id=None, operation=lambda: dns_lookup(tx(host), name))

    async def filesystem_status_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="filesystem_status", host_id=host, resource_id=None, operation=lambda: filesystem_status(tx(host)))

    async def disk_hotspots_tool(host: str, depth: int = 1) -> dict[str, Any]:
        return await run_observed_tool(tool="disk_hotspots", host_id=host, resource_id=None, operation=lambda: disk_hotspots(tx(host), depth))

    async def user_sessions_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="user_sessions", host_id=host, resource_id=None, operation=lambda: user_sessions(tx(host)))

    async def hardware_info_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="hardware_info", host_id=host, resource_id=None, operation=lambda: hardware_info(tx(host)))

    async def sensor_status_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(tool="sensor_status", host_id=host, resource_id=None, operation=lambda: sensor_status(tx(host)))

    for name, function in (
        ("process_list", process_list_tool),
        ("process_tree", process_tree_tool),
        ("network_listeners", network_listeners_tool),
        ("network_interfaces", network_interfaces_tool),
        ("network_routes", network_routes_tool),
        ("dns_lookup", dns_lookup_tool),
        ("filesystem_status", filesystem_status_tool),
        ("disk_hotspots", disk_hotspots_tool),
        ("user_sessions", user_sessions_tool),
        ("hardware_info", hardware_info_tool),
        ("sensor_status", sensor_status_tool),
    ):
        register(name, function)

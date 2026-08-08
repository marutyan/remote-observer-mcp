from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.config import AppConfig, HostConfig, load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.observers import register_extension_tools
from remote_observer_mcp.observers.system import (
    disk_usage as observe_disk_usage,
)
from remote_observer_mcp.observers.system import (
    process_status as observe_process_status,
)
from remote_observer_mcp.observers.system import (
    system_status as observe_system_status,
)
from remote_observer_mcp.transports import transport_for_host

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_server(config: AppConfig) -> FastMCP:
    server = FastMCP(
        "remote-observer-mcp",
        instructions=(
            "Read-only observation of explicitly registered hosts and resources. "
            "No tool provides arbitrary shell, hostname, or filesystem access."
        ),
    )

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def list_hosts() -> dict[str, Any]:
        """List registered logical host IDs and enabled observer capabilities."""
        return {
            "ok": True,
            "data": {
                "hosts": [
                    _public_host_summary(config.hosts[host_id])
                    for host_id in sorted(config.hosts)
                ]
            },
        }

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def system_status(host: str) -> dict[str, Any]:
        """Observe load, uptime, and memory for a registered Linux host."""
        return await _host_call(config, host, observe_system_status)

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def disk_usage(host: str) -> dict[str, Any]:
        """Observe bounded filesystem usage for a registered host."""
        return await _host_call(config, host, observe_disk_usage)

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def process_status(host: str, process: str) -> dict[str, Any]:
        """Observe an exact registered process name without command-line details."""
        try:
            host_config = config.host(host)
            process_config = host_config.process(process)
            transport = transport_for_host(host_config)
            data = await observe_process_status(transport, process_config.name)
            return _success(data)
        except ObserverError as error:
            return _failure(error)

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def host_overview(host: str) -> dict[str, Any]:
        """Return a minimal system and disk summary for one registered host."""
        try:
            host_config = config.host(host)
            transport = transport_for_host(host_config)
            system = await observe_system_status(transport)
            disks = await observe_disk_usage(transport)
            return _success({"system": system, "disk_usage": disks})
        except ObserverError as error:
            return _failure(error)

    register_extension_tools(server, config)
    return server


async def _host_call(
    config: AppConfig,
    host_id: str,
    observer: Callable[[Any], Awaitable[Any]],
) -> dict[str, Any]:
    try:
        host_config = config.host(host_id)
        transport = transport_for_host(host_config)
        return _success(await observer(transport))
    except ObserverError as error:
        return _failure(error)


def _public_host_summary(host: HostConfig) -> dict[str, Any]:
    return {
        "id": host.host_id,
        "capabilities": {
            "system": True,
            "disk": True,
            "processes": sorted(host.processes),
            "services": sorted(host.services),
            "repositories": sorted(host.repos),
            "containers": sorted(host.containers),
            "gpu": host.gpu,
        },
    }


def _success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _failure(error: ObserverError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
        },
    }


def config_path_from_environment() -> Path:
    configured = os.environ.get("REMOTE_OBSERVER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "remote-observer-mcp" / "config.toml"


def main() -> None:
    config = load_config(config_path_from_environment())
    create_server(config).run(transport="stdio")

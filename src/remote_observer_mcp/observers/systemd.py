from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, ServiceConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_PROPERTIES = "LoadState,ActiveState,SubState,UnitFileState,Description"


async def service_status(transport: Transport, service: ServiceConfig) -> dict[str, str]:
    result = await transport.run(
        CommandSpec(
            argv=(
                "systemctl",
                "show",
                "--no-pager",
                f"--property={_PROPERTIES}",
                service.unit,
            )
        )
    )
    if result.exit_code != 0:
        raise ObserverError("command_failed", "service observation failed")

    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key] = value

    required = {"LoadState", "ActiveState", "SubState", "UnitFileState", "Description"}
    if not required.issubset(properties):
        raise ObserverError("command_failed", "unexpected systemd output")
    return {
        "load_state": properties["LoadState"],
        "active_state": properties["ActiveState"],
        "sub_state": properties["SubState"],
        "unit_file_state": properties["UnitFileState"],
        "description": properties["Description"],
    }


async def service_logs(
    transport: Transport,
    service: ServiceConfig,
    lines: int = 100,
) -> dict[str, Any]:
    if not service.logs:
        raise ObserverError("unsupported_capability", "logs are not enabled for this service")
    bounded_lines = min(max(lines, 1), 500)
    result = await transport.run(
        CommandSpec(
            argv=(
                "journalctl",
                "--no-pager",
                "--output=short-iso",
                f"--lines={bounded_lines}",
                f"--unit={service.unit}",
            )
        )
    )
    if result.exit_code != 0:
        raise ObserverError("command_failed", "service log observation failed")
    return {
        "lines": result.stdout.splitlines(),
        "truncated": result.truncated,
        "redacted": result.redacted,
    }


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="service_status", annotations=_READ_ONLY, structured_output=True)
    async def service_status_tool(host: str, service: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            service_config = host_config.service(service)
            return await service_status(transport_for_host(host_config), service_config)

        return await run_observed_tool(
            tool="service_status",
            host_id=host,
            resource_id=service,
            operation=operation,
        )

    @server.tool(name="service_logs", annotations=_READ_ONLY, structured_output=True)
    async def service_logs_tool(host: str, service: str, lines: int = 100) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            service_config = host_config.service(service)
            return await service_logs(transport_for_host(host_config), service_config, lines)

        return await run_observed_tool(
            tool="service_logs",
            host_id=host,
            resource_id=service,
            operation=operation,
        )

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
_PRIORITIES = {"emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"}


async def service_failures(transport: Transport) -> list[str]:
    result = await transport.run(
        CommandSpec(argv=("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"))
    )
    if result.exit_code != 0:
        raise ObserverError("command_failed", "failed-unit observation failed")
    return result.stdout.splitlines()[:300]


async def systemd_timers(transport: Transport) -> list[str]:
    result = await transport.run(
        CommandSpec(argv=("systemctl", "list-timers", "--all", "--no-legend", "--no-pager"))
    )
    if result.exit_code != 0:
        raise ObserverError("command_failed", "timer observation failed")
    return result.stdout.splitlines()[:300]


async def journal_query(
    transport: Transport,
    service: ServiceConfig,
    since_minutes: int = 60,
    priority: str | None = None,
    lines: int = 100,
) -> dict[str, Any]:
    if not service.logs:
        raise ObserverError("unsupported_capability", "logs are not enabled for this service")
    since = min(max(int(since_minutes), 1), 10080)
    bounded_lines = min(max(int(lines), 1), 500)
    if priority is not None and priority not in _PRIORITIES:
        raise ObserverError("invalid_filter", "invalid journal priority")
    argv = [
        "journalctl",
        "--no-pager",
        "--output=short-iso",
        f"--unit={service.unit}",
        f"--since=-{since} minutes",
        f"--lines={bounded_lines}",
    ]
    if priority is not None:
        argv.append(f"--priority={priority}")
    result = await transport.run(CommandSpec(argv=tuple(argv), timeout_seconds=15))
    if result.exit_code != 0:
        raise ObserverError("command_failed", "journal observation failed")
    return {
        "lines": result.stdout.splitlines(),
        "truncated": result.truncated,
        "redacted": result.redacted,
    }


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="service_failures", annotations=_READ_ONLY, structured_output=True)
    async def service_failures_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(
            tool="service_failures", host_id=host, resource_id=None,
            operation=lambda: service_failures(transport_for_host(config.host(host))),
        )

    @server.tool(name="systemd_timers", annotations=_READ_ONLY, structured_output=True)
    async def systemd_timers_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(
            tool="systemd_timers", host_id=host, resource_id=None,
            operation=lambda: systemd_timers(transport_for_host(config.host(host))),
        )

    @server.tool(name="journal_query", annotations=_READ_ONLY, structured_output=True)
    async def journal_query_tool(
        host: str,
        service: str,
        since_minutes: int = 60,
        priority: str | None = None,
        lines: int = 100,
    ) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            service_config = host_config.service(service)
            return await journal_query(
                transport_for_host(host_config), service_config, since_minutes, priority, lines
            )
        return await run_observed_tool(
            tool="journal_query", host_id=host, resource_id=service, operation=operation
        )

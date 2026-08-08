from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, ContainerConfig, WorkspaceConfig
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


def _docker_error(result: CommandResult) -> None:
    if result.exit_code == 0:
        return
    text = result.stderr.lower()
    if result.exit_code == 127 or "command not found" in text:
        raise ObserverError("unsupported_capability", "Docker is unavailable")
    if "permission denied" in text:
        raise ObserverError("permission_denied", "Docker access is denied")
    raise ObserverError("command_failed", "Docker observation failed")


async def container_stats(
    transport: Transport,
    containers: Mapping[str, ContainerConfig],
) -> list[dict[str, Any]]:
    if not containers:
        return []
    by_name = {container.name: resource_id for resource_id, container in containers.items()}
    argv = (
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
        *tuple(sorted(by_name)),
    )
    result = await transport.run(CommandSpec(argv=argv, timeout_seconds=15))
    _docker_error(result)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3 or fields[0] not in by_name:
            continue
        rows.append(
            {
                "id": by_name[fields[0]],
                "cpu": fields[1],
                "memory": fields[2],
            }
        )
    return rows


async def compose_status(
    transport: Transport,
    workspace: WorkspaceConfig,
) -> list[dict[str, Any]]:
    if not workspace.compose:
        raise ObserverError("unsupported_capability", "Compose observation is not enabled")
    result = await transport.run(
        CommandSpec(
            argv=(
                "docker",
                "compose",
                "--project-directory",
                workspace.root,
                "ps",
                "--format",
                "json",
            ),
            timeout_seconds=15,
        )
    )
    _docker_error(result)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(
                {
                    "service": item.get("Service"),
                    "name": item.get("Name"),
                    "state": item.get("State"),
                    "status": item.get("Status"),
                }
            )
    return rows


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="container_stats", annotations=_READ_ONLY, structured_output=True)
    async def container_stats_tool(host: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            return await container_stats(
                transport_for_host(host_config), host_config.containers
            )
        return await run_observed_tool(
            tool="container_stats", host_id=host, resource_id=None, operation=operation
        )

    @server.tool(name="compose_status", annotations=_READ_ONLY, structured_output=True)
    async def compose_status_tool(workspace: str) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            host_config = config.host(item.host_id)
            return await compose_status(transport_for_host(host_config), item)
        return await run_observed_tool(
            tool="compose_status", host_id=None, resource_id=workspace, operation=operation
        )

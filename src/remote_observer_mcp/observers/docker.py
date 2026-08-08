from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, ContainerConfig
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


async def container_list(
    transport: Transport,
    containers: Mapping[str, ContainerConfig],
) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    for resource_id in sorted(containers):
        container = containers[resource_id]
        result = await transport.run(
            CommandSpec(
                argv=(
                    "docker",
                    "ps",
                    "--all",
                    "--filter",
                    f"name=^/{container.name}$",
                    "--format",
                    "{{.Names}}\t{{.Status}}\t{{.Image}}",
                )
            )
        )
        _check_docker_result(result)
        rows = [line for line in result.stdout.splitlines() if line.strip()]
        if not rows:
            observed.append(
                {"id": resource_id, "present": False, "status": None, "image": None}
            )
            continue
        fields = rows[0].split("\t", 2)
        if len(fields) != 3 or fields[0] != container.name:
            raise ObserverError("command_failed", "unexpected Docker output")
        observed.append(
            {
                "id": resource_id,
                "present": True,
                "status": fields[1],
                "image": fields[2],
            }
        )
    return observed


async def container_logs(
    transport: Transport,
    container: ContainerConfig,
    lines: int = 100,
) -> dict[str, Any]:
    if not container.logs:
        raise ObserverError("unsupported_capability", "logs are not enabled for this container")
    bounded_lines = min(max(lines, 1), 500)
    result = await transport.run(
        CommandSpec(
            argv=("docker", "logs", "--tail", str(bounded_lines), container.name),
            timeout_seconds=15,
        )
    )
    _check_docker_result(result)
    return {
        "stdout": result.stdout.splitlines(),
        "stderr": result.stderr.splitlines(),
        "truncated": result.truncated,
        "redacted": result.redacted,
    }


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="container_list", annotations=_READ_ONLY, structured_output=True)
    async def container_list_tool(host: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            return await container_list(
                transport_for_host(host_config), host_config.containers
            )

        return await run_observed_tool(
            tool="container_list",
            host_id=host,
            resource_id=None,
            operation=operation,
        )

    @server.tool(name="container_logs", annotations=_READ_ONLY, structured_output=True)
    async def container_logs_tool(
        host: str,
        container: str,
        lines: int = 100,
    ) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            container_config = host_config.container(container)
            return await container_logs(
                transport_for_host(host_config), container_config, lines
            )

        return await run_observed_tool(
            tool="container_logs",
            host_id=host,
            resource_id=container,
            operation=operation,
        )


def _check_docker_result(result: CommandResult) -> None:
    if result.exit_code == 0:
        return
    diagnostic = result.stderr.lower()
    if result.exit_code == 127 or "command not found" in diagnostic:
        raise ObserverError("unsupported_capability", "Docker is unavailable")
    if "permission denied" in diagnostic:
        raise ObserverError("permission_denied", "Docker access is denied")
    raise ObserverError("command_failed", "Docker observation failed")

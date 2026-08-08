from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
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
_QUERY = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu"


async def gpu_status(transport: Transport) -> list[dict[str, Any]]:
    result = await transport.run(
        CommandSpec(
            argv=(
                "nvidia-smi",
                f"--query-gpu={_QUERY}",
                "--format=csv,noheader,nounits",
            )
        )
    )
    _check_gpu_result(result)

    rows = csv.reader(StringIO(result.stdout), skipinitialspace=True)
    observed: list[dict[str, Any]] = []
    try:
        for row in rows:
            if not row:
                continue
            if len(row) != 6:
                raise ValueError("unexpected column count")
            observed.append(
                {
                    "index": int(row[0].strip()),
                    "name": row[1].strip(),
                    "utilization_percent": int(row[2].strip()),
                    "memory_used_mib": int(row[3].strip()),
                    "memory_total_mib": int(row[4].strip()),
                    "temperature_c": int(row[5].strip()),
                }
            )
    except ValueError as error:
        raise ObserverError("command_failed", "unexpected NVIDIA GPU output") from error
    return observed


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="gpu_status", annotations=_READ_ONLY, structured_output=True)
    async def gpu_status_tool(host: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            if not host_config.gpu:
                raise ObserverError(
                    "unsupported_capability",
                    "GPU observation is not enabled for this host",
                )
            return await gpu_status(transport_for_host(host_config))

        return await run_observed_tool(
            tool="gpu_status",
            host_id=host,
            resource_id=None,
            operation=operation,
        )


def _check_gpu_result(result: CommandResult) -> None:
    if result.exit_code == 0:
        return
    diagnostic = result.stderr.lower()
    if result.exit_code == 127 or "command not found" in diagnostic:
        raise ObserverError("unsupported_capability", "NVIDIA GPU tooling is unavailable")
    if "permission denied" in diagnostic:
        raise ObserverError("permission_denied", "GPU observation is denied")
    raise ObserverError("command_failed", "GPU observation failed")

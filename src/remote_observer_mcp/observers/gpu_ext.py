from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig
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
_QUERY = "gpu_uuid,pid,process_name,used_gpu_memory"


async def gpu_processes(transport: Transport) -> list[dict[str, Any]]:
    result = await transport.run(
        CommandSpec(
            argv=(
                "nvidia-smi",
                f"--query-compute-apps={_QUERY}",
                "--format=csv,noheader,nounits",
            )
        )
    )
    if result.exit_code == 127 or "command not found" in result.stderr.lower():
        raise ObserverError("unsupported_capability", "NVIDIA tooling is unavailable")
    if result.exit_code != 0:
        raise ObserverError("command_failed", "GPU process observation failed")
    rows: list[dict[str, Any]] = []
    for row in csv.reader(StringIO(result.stdout), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 4:
            raise ObserverError("command_failed", "unexpected NVIDIA process output")
        try:
            pid = int(row[1].strip())
            memory = int(row[3].strip())
        except ValueError as error:
            raise ObserverError("command_failed", "unexpected NVIDIA process output") from error
        rows.append(
            {
                "gpu_uuid": row[0].strip(),
                "pid": pid,
                "process_name": row[2].strip(),
                "memory_used_mib": memory,
            }
        )
    return rows


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="gpu_processes", annotations=_READ_ONLY, structured_output=True)
    async def gpu_processes_tool(host: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            if not host_config.gpu:
                raise ObserverError("unsupported_capability", "GPU observation is not enabled")
            return await gpu_processes(transport_for_host(host_config))
        return await run_observed_tool(
            tool="gpu_processes", host_id=host, resource_id=None, operation=operation
        )

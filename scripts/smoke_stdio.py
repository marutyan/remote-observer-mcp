from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_EXPECTED_TOOLS = {
    "list_hosts",
    "host_overview",
    "system_status",
    "disk_usage",
    "process_status",
}


async def _run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "config.toml"
        config_path.write_text(
            """
[hosts.gateway]
transport = "local"
""",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["REMOTE_OBSERVER_CONFIG"] = str(config_path)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "remote_observer_mcp"],
            env=env,
        )

        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            if set(tools) != _EXPECTED_TOOLS:
                raise RuntimeError(f"unexpected MCP tools: {sorted(tools)}")
            for tool in tools.values():
                annotations = tool.annotations
                if annotations is None or not annotations.readOnlyHint:
                    raise RuntimeError(f"tool is not read-only: {tool.name}")
                if annotations.destructiveHint is not False:
                    raise RuntimeError(f"tool may be destructive: {tool.name}")

            result = await session.call_tool("list_hosts", arguments={})
            if result.isError:
                raise RuntimeError("list_hosts returned an MCP error")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports.base import run_process


class LocalTransport:
    async def run(self, command: CommandSpec) -> CommandResult:
        return await run_process(command)

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports.base import run_process

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class SshTransport:
    alias: str
    connect_timeout_seconds: int = 5
    ssh_executable: str = "ssh"

    def __post_init__(self) -> None:
        if not _ALIAS_RE.fullmatch(self.alias):
            raise ValueError("SSH alias contains unsupported characters")
        if not 1 <= self.connect_timeout_seconds <= 5:
            raise ValueError("SSH connect timeout exceeds the hard limit")
        if not self.ssh_executable or "\x00" in self.ssh_executable:
            raise ValueError("SSH executable is invalid")

    def build_argv(self, command: CommandSpec) -> tuple[str, ...]:
        remote_command = shlex.join(command.argv)
        return (
            self.ssh_executable,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={self.connect_timeout_seconds}",
            self.alias,
            remote_command,
        )

    async def run(self, command: CommandSpec) -> CommandResult:
        wrapped = CommandSpec(
            argv=self.build_argv(command),
            timeout_seconds=command.timeout_seconds,
            max_output_bytes=command.max_output_bytes,
        )
        result = await run_process(wrapped, missing_code="connection_failed")
        if result.exit_code != 255:
            return result

        diagnostic = result.stderr.lower()
        if "host key verification failed" in diagnostic:
            code = "host_key_failure"
            message = "SSH host key verification failed"
        elif "permission denied" in diagnostic or "authentication failed" in diagnostic:
            code = "authentication_failed"
            message = "SSH authentication failed"
        else:
            code = "connection_failed"
            message = "SSH connection failed"
        raise ObserverError(code, message)

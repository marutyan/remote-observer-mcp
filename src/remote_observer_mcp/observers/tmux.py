from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, HostConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport, run_process

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_WINDOW_RE = re.compile(r"^@[0-9]{1,10}$")
_PANE_RE = re.compile(r"^%[0-9]{1,10}$")
_CAPTURE_LINES_RE = re.compile(r"^-[1-9][0-9]{0,2}$")

_SESSION_FORMAT = "#{session_name}\t#{session_windows}\t#{session_attached}"
_WINDOW_FORMAT = "#{window_id}\t#{window_index}\t#{window_name}\t#{window_active}"
_PANE_FORMAT = "#{pane_id}\t#{pane_index}\t#{pane_active}\t#{pane_current_command}"
_HELPER_PATH = "/usr/local/libexec/remote-observer-tmux-read"
_SUDO_PATH = "/usr/bin/sudo"


def _no_server(result: CommandResult) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return result.exit_code == 1 and ("no server running" in text or "no sessions" in text)


def _check(result: CommandResult) -> None:
    if result.exit_code == 0:
        return
    if _no_server(result):
        raise ObserverError("no_tmux_server", "tmux has no running server")
    if result.exit_code == 127 or "command not found" in result.stderr.lower():
        raise ObserverError("unsupported_capability", "tmux is unavailable")
    raise ObserverError("command_failed", "tmux observation failed")


def _session(value: str) -> str:
    if not _SESSION_RE.fullmatch(value):
        raise ObserverError("invalid_tmux_target", "invalid tmux target")
    return value


def _window(value: str) -> str:
    if not _WINDOW_RE.fullmatch(value):
        raise ObserverError("invalid_tmux_target", "invalid tmux target")
    return value


def _pane(value: str) -> str:
    if not _PANE_RE.fullmatch(value):
        raise ObserverError("invalid_tmux_target", "invalid tmux target")
    return value


def _helper_args(command: CommandSpec) -> tuple[str, ...]:
    argv = command.argv

    if argv == ("tmux", "list-sessions", "-F", _SESSION_FORMAT):
        return ("sessions",)

    if (
        len(argv) == 6
        and argv[:3] == ("tmux", "list-windows", "-t")
        and argv[4:] == ("-F", _WINDOW_FORMAT)
        and _SESSION_RE.fullmatch(argv[3])
    ):
        return ("windows", argv[3])

    if (
        len(argv) == 6
        and argv[:3] == ("tmux", "list-panes", "-t")
        and argv[4:] == ("-F", _PANE_FORMAT)
        and (_SESSION_RE.fullmatch(argv[3]) or _WINDOW_RE.fullmatch(argv[3]))
    ):
        return ("panes", argv[3])

    if (
        len(argv) == 7
        and argv[:4] == ("tmux", "capture-pane", "-p", "-t")
        and argv[5] == "-S"
        and _PANE_RE.fullmatch(argv[4])
        and _CAPTURE_LINES_RE.fullmatch(argv[6])
    ):
        lines = int(argv[6][1:])
        if 1 <= lines <= 500:
            return ("capture", argv[4], str(lines))

    raise ObserverError("invalid_tmux_command", "unsupported tmux observation command")


class CrossUserTmuxTransport:
    """Run only known read-only tmux operations through the privileged helper."""

    def __init__(self, user: str):
        self.user = user

    async def run(self, command: CommandSpec) -> CommandResult:
        helper_args = _helper_args(command)
        helper_command = CommandSpec(
            argv=(
                _SUDO_PATH,
                "-n",
                "-u",
                self.user,
                "--",
                _HELPER_PATH,
                *helper_args,
            ),
            timeout_seconds=command.timeout_seconds,
            max_output_bytes=command.max_output_bytes,
        )
        return await run_process(helper_command, missing_code="unsupported_capability")


def transport_for_tmux_host(host: HostConfig) -> Transport:
    if host.tmux_user is not None:
        if host.transport != "local":
            raise ObserverError(
                "invalid_configuration",
                "cross-user tmux observation requires local transport",
            )
        return CrossUserTmuxTransport(host.tmux_user)
    return transport_for_host(host)


async def tmux_sessions(transport: Transport) -> list[dict[str, Any]]:
    result = await transport.run(
        CommandSpec(argv=("tmux", "list-sessions", "-F", _SESSION_FORMAT))
    )
    if _no_server(result):
        return []
    _check(result)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        try:
            windows = int(fields[1])
            attached = int(fields[2]) > 0
        except ValueError:
            continue
        rows.append({"session": fields[0], "windows": windows, "attached": attached})
    return rows


async def tmux_windows(transport: Transport, session: str) -> list[dict[str, Any]]:
    target = _session(session)
    result = await transport.run(
        CommandSpec(
            argv=("tmux", "list-windows", "-t", target, "-F", _WINDOW_FORMAT)
        )
    )
    _check(result)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t", 3)
        if len(fields) == 4:
            rows.append(
                {
                    "window_id": fields[0],
                    "index": int(fields[1]) if fields[1].isdigit() else None,
                    "name": fields[2],
                    "active": fields[3] == "1",
                }
            )
    return rows


async def tmux_panes(
    transport: Transport,
    session: str,
    window: str | None = None,
) -> list[dict[str, Any]]:
    session_target = _session(session)
    target = _window(window) if window is not None else session_target
    result = await transport.run(
        CommandSpec(argv=("tmux", "list-panes", "-t", target, "-F", _PANE_FORMAT))
    )
    _check(result)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t", 3)
        if len(fields) == 4:
            rows.append(
                {
                    "pane_id": fields[0],
                    "index": int(fields[1]) if fields[1].isdigit() else None,
                    "active": fields[2] == "1",
                    "current_command": fields[3],
                }
            )
    return rows


async def tmux_capture(
    transport: Transport,
    pane: str,
    lines: int = 100,
) -> dict[str, Any]:
    target = _pane(pane)
    bounded = min(max(int(lines), 1), 500)
    result = await transport.run(
        CommandSpec(
            argv=("tmux", "capture-pane", "-p", "-t", target, "-S", f"-{bounded}"),
            timeout_seconds=10,
        )
    )
    _check(result)
    return {
        "lines": result.stdout.splitlines(),
        "truncated": result.truncated,
        "redacted": result.redacted,
    }


def register_tools(server: FastMCP, config: AppConfig) -> None:
    def transport(host: str) -> Transport:
        return transport_for_tmux_host(config.host(host))

    @server.tool(name="tmux_sessions", annotations=_READ_ONLY, structured_output=True)
    async def tmux_sessions_tool(host: str) -> dict[str, Any]:
        return await run_observed_tool(
            tool="tmux_sessions",
            host_id=host,
            resource_id=None,
            operation=lambda: tmux_sessions(transport(host)),
        )

    @server.tool(name="tmux_windows", annotations=_READ_ONLY, structured_output=True)
    async def tmux_windows_tool(host: str, session: str) -> dict[str, Any]:
        return await run_observed_tool(
            tool="tmux_windows",
            host_id=host,
            resource_id=session,
            operation=lambda: tmux_windows(transport(host), session),
        )

    @server.tool(name="tmux_panes", annotations=_READ_ONLY, structured_output=True)
    async def tmux_panes_tool(
        host: str, session: str, window: str | None = None
    ) -> dict[str, Any]:
        return await run_observed_tool(
            tool="tmux_panes",
            host_id=host,
            resource_id=session,
            operation=lambda: tmux_panes(transport(host), session, window),
        )

    @server.tool(name="tmux_capture", annotations=_READ_ONLY, structured_output=True)
    async def tmux_capture_tool(
        host: str, pane: str, lines: int = 100
    ) -> dict[str, Any]:
        return await run_observed_tool(
            tool="tmux_capture",
            host_id=host,
            resource_id=pane,
            operation=lambda: tmux_capture(transport(host), pane, lines),
        )

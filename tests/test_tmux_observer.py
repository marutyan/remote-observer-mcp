from collections import deque
from pathlib import Path

import pytest
from remote_observer_mcp.observers.tmux import tmux_capture, tmux_sessions

from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.server import create_server


class FakeTransport:
    def __init__(self, results: list[CommandResult]):
        self.results = deque(results)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return self.results.popleft()


def _result(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, stderr, 1, False, False)


@pytest.mark.asyncio
async def test_tmux_sessions_uses_fixed_format_and_parses_rows():
    transport = FakeTransport([_result("work\t2\t1\n")])
    rows = await tmux_sessions(transport)
    assert rows == [{"session": "work", "windows": 2, "attached": True}]
    assert transport.commands[0].argv == (
        "tmux",
        "list-sessions",
        "-F",
        "#{session_name}\t#{session_windows}\t#{session_attached}",
    )


@pytest.mark.asyncio
async def test_tmux_capture_clamps_lines_and_never_sends_keys():
    transport = FakeTransport([_result("a\nb\n")])
    result = await tmux_capture(transport, "%3", 9999)
    assert result["lines"] == ["a", "b"]
    argv = transport.commands[0].argv
    assert argv == ("tmux", "capture-pane", "-p", "-t", "%3", "-S", "-500")
    forbidden = {"send-keys", "run-shell", "new-session", "kill-pane", "set-option"}
    assert forbidden.isdisjoint(argv)


@pytest.mark.asyncio
async def test_invalid_tmux_target_fails_before_transport():
    transport = FakeTransport([])
    with pytest.raises(ObserverError) as error:
        await tmux_capture(transport, "bad;send-keys", 10)
    assert error.value.code == "invalid_tmux_target"
    assert transport.commands == []


@pytest.mark.asyncio
async def test_tmux_tools_register_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("[hosts.gateway]\ntransport = \"local\"\n", encoding="utf-8")
    tools = {tool.name: tool for tool in await create_server(load_config(path)).list_tools()}
    for name in ("tmux_sessions", "tmux_windows", "tmux_panes", "tmux_capture"):
        assert name in tools
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert "command" not in set(tools[name].inputSchema.get("properties", {}))

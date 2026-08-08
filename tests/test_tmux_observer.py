from collections import deque
from pathlib import Path

import pytest

import remote_observer_mcp.observers.tmux as tmux_observer
from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.observers.tmux import tmux_capture, tmux_sessions
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


@pytest.mark.asyncio
async def test_cross_user_transport_maps_only_existing_tmux_read_commands(monkeypatch):
    adapter_type = getattr(tmux_observer, "CrossUserTmuxTransport", None)
    assert adapter_type is not None, "cross-user tmux adapter is not implemented"

    observed: list[CommandSpec] = []

    async def fake_run_process(
        command: CommandSpec, *, missing_code: str = "unsupported_capability"
    ) -> CommandResult:
        assert missing_code == "unsupported_capability"
        observed.append(command)
        return _result()

    monkeypatch.setattr(tmux_observer, "run_process", fake_run_process, raising=False)
    transport = adapter_type("emma")
    cases = [
        (
            CommandSpec(
                argv=(
                    "tmux",
                    "list-sessions",
                    "-F",
                    "#{session_name}\t#{session_windows}\t#{session_attached}",
                )
            ),
            ("sessions",),
        ),
        (
            CommandSpec(
                argv=(
                    "tmux",
                    "list-windows",
                    "-t",
                    "work",
                    "-F",
                    "#{window_id}\t#{window_index}\t#{window_name}\t#{window_active}",
                )
            ),
            ("windows", "work"),
        ),
        (
            CommandSpec(
                argv=(
                    "tmux",
                    "list-panes",
                    "-t",
                    "@12",
                    "-F",
                    "#{pane_id}\t#{pane_index}\t#{pane_active}\t#{pane_current_command}",
                )
            ),
            ("panes", "@12"),
        ),
        (
            CommandSpec(
                argv=("tmux", "capture-pane", "-p", "-t", "%3", "-S", "-125"),
                timeout_seconds=10,
            ),
            ("capture", "%3", "125"),
        ),
    ]

    for command, helper_args in cases:
        await transport.run(command)
        assert observed[-1].argv == (
            "/usr/bin/sudo",
            "-n",
            "-u",
            "emma",
            "--",
            "/usr/local/libexec/remote-observer-tmux-read",
            *helper_args,
        )
        assert observed[-1].timeout_seconds == command.timeout_seconds
        assert observed[-1].max_output_bytes == command.max_output_bytes


@pytest.mark.asyncio
async def test_cross_user_transport_rejects_mutation_before_sudo(monkeypatch):
    adapter_type = getattr(tmux_observer, "CrossUserTmuxTransport", None)
    assert adapter_type is not None, "cross-user tmux adapter is not implemented"

    called = False

    async def fake_run_process(
        command: CommandSpec, *, missing_code: str = "unsupported_capability"
    ) -> CommandResult:
        nonlocal called
        called = True
        return _result()

    monkeypatch.setattr(tmux_observer, "run_process", fake_run_process, raising=False)
    transport = adapter_type("emma")

    with pytest.raises(ObserverError) as error:
        await transport.run(CommandSpec(argv=("tmux", "send-keys", "-t", "%1", "id")))

    assert error.value.code == "invalid_tmux_command"
    assert called is False


def test_tmux_transport_selection_preserves_default_and_uses_registered_user(tmp_path: Path):
    selector = getattr(tmux_observer, "transport_for_tmux_host", None)
    assert selector is not None, "tmux transport selector is not implemented"

    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.gateway]
transport = "local"

[hosts.emma]
transport = "local"
tmux_user = "emma"
""",
        encoding="utf-8",
    )
    config = load_config(path)

    direct = selector(config.host("gateway"))
    cross_user = selector(config.host("emma"))

    assert direct.__class__.__name__ == "LocalTransport"
    assert cross_user.__class__.__name__ == "CrossUserTmuxTransport"
    assert cross_user.user == "emma"


@pytest.mark.asyncio
async def test_tmux_sessions_treats_missing_default_socket_as_empty():
    transport = FakeTransport(
        [
            _result(
                stderr=(
                    "error connecting to /tmp/tmux-1000/default "
                    "(No such file or directory)\n"
                ),
                exit_code=1,
            )
        ]
    )

    assert await tmux_sessions(transport) == []


@pytest.mark.asyncio
async def test_tmux_sessions_does_not_hide_permission_denied():
    transport = FakeTransport(
        [_result(stderr="error connecting to /tmp/tmux-1000/default (Permission denied)\n", exit_code=1)]
    )

    with pytest.raises(ObserverError) as error:
        await tmux_sessions(transport)

    assert error.value.code == "command_failed"

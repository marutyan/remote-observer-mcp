from collections import deque
from pathlib import Path

import pytest
from remote_observer_mcp.observers.workspace import workspace_find, workspace_search

from remote_observer_mcp.config import WorkspaceConfig, load_config
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.server import create_server


class FakeTransport:
    def __init__(self, results: list[CommandResult]):
        self.results = deque(results)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return self.results.popleft()


def _result(stdout: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, "", 1, False, False)


@pytest.mark.asyncio
async def test_find_uses_fd_without_exec_and_filters_sensitive_paths():
    transport = FakeTransport([_result(), _result("/srv/app/src/a.py\n/srv/app/.env\n")])
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    result = await workspace_find(transport, workspace, pattern="*.py", max_results=20)
    assert result["paths"] == ["src/a.py"]
    argv = transport.commands[-1].argv
    assert argv[0] == "fd"
    assert "--exec" not in argv
    assert "-x" not in argv


@pytest.mark.asyncio
async def test_search_falls_back_to_grep_with_default_secret_excludes():
    transport = FakeTransport(
        [_result(exit_code=1), _result(), _result("/srv/app/src/a.py:4:needle\n")]
    )
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    result = await workspace_search(transport, workspace, query="needle", max_results=10)
    assert result["matches"][0]["path"] == "src/a.py"
    argv = transport.commands[-1].argv
    assert argv[0] == "grep"
    assert "--exclude=.env" in argv
    assert "--exclude=*.pem" in argv
    assert "--" in argv


@pytest.mark.asyncio
async def test_workspace_tools_register_read_only_without_raw_path_input(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[hosts.gateway]\ntransport = "local"\n[workspaces.app]\nhost = "gateway"\nroot = "{tmp_path}"\n''',
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(config_path)).list_tools()}
    expected = {
        "list_workspaces",
        "workspace_find",
        "workspace_search",
        "workspace_read",
        "workspace_tree",
        "file_info",
        "checksum",
    }
    assert expected.issubset(tools)
    for name in expected:
        tool = tools[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert "path" not in set(tool.inputSchema.get("properties", {}))
        assert "command" not in set(tool.inputSchema.get("properties", {}))

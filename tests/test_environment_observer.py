from collections import deque
from pathlib import Path

import pytest
from remote_observer_mcp.observers.environment import archive_list, runtime_versions

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


def _result(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, stderr, 1, False, False)


@pytest.mark.asyncio
async def test_runtime_versions_uses_fixed_catalog_only():
    results = [_result("v\n") for _ in range(12)]
    transport = FakeTransport(results)
    versions = await runtime_versions(transport)
    assert "python" in versions
    assert "rustc" in versions
    assert "nix" in versions
    for command in transport.commands:
        assert command.argv[0] in {
            "python3", "uv", "node", "npm", "pnpm", "rustc", "cargo", "rustup",
            "nix", "git", "docker", "tmux",
        }


@pytest.mark.asyncio
async def test_archive_list_is_list_only():
    transport = FakeTransport([_result("a.txt\nb.txt\n")])
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    rows = await archive_list(transport, workspace, "bundle.tar")
    assert rows == ["a.txt", "b.txt"]
    assert transport.commands[0].argv == ("tar", "-tf", "/srv/app/bundle.tar")
    assert "-x" not in transport.commands[0].argv


@pytest.mark.asyncio
async def test_environment_tools_register_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        f'''[hosts.gateway]\ntransport = "local"\n[workspaces.app]\nhost = "gateway"\nroot = "{tmp_path}"\n''',
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(path)).list_tools()}
    expected = {
        "tool_availability",
        "runtime_versions",
        "python_environment",
        "node_environment",
        "rust_environment",
        "cargo_metadata",
        "nix_environment",
        "mise_environment",
        "package_info",
        "archive_list",
    }
    assert expected.issubset(tools)
    for name in expected:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert "command" not in set(tools[name].inputSchema.get("properties", {}))

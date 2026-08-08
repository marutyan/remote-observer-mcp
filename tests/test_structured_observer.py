from collections import deque
from pathlib import Path

import pytest
from remote_observer_mcp.observers.structured import code_search, query_document

from remote_observer_mcp.config import WorkspaceConfig, load_config
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


def _result(stdout: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, "", 1, False, False)


@pytest.mark.asyncio
async def test_code_search_uses_ast_grep_search_only():
    transport = FakeTransport([_result('{"file":"/srv/app/src/a.py","line":2,"text":"print(x)"}\n')])
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    rows = await code_search(transport, workspace, "python", "print($A)", 20)
    assert rows[0]["path"] == "src/a.py"
    argv = transport.commands[0].argv
    assert argv[0] == "ast-grep"
    assert "--rewrite" not in argv
    assert "--fix" not in argv
    assert "-r" not in argv


@pytest.mark.asyncio
async def test_query_document_parses_json_after_bounded_cat():
    transport = FakeTransport([_result('{"a":{"items":[{"name":"ok"}]}}')])
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    value = await query_document(transport, workspace, "data.json", "a.items[0].name")
    assert value == "ok"
    assert transport.commands[0].argv == ("cat", "/srv/app/data.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["a|keys", "a=1", "..", "$()", "a[nope]"])
async def test_selector_grammar_rejects_programs(selector: str):
    with pytest.raises(ObserverError) as error:
        await query_document(FakeTransport([]), WorkspaceConfig("app", "g", "/srv/app"), "data.json", selector)
    assert error.value.code == "invalid_selector"


@pytest.mark.asyncio
async def test_structured_tools_are_read_only_and_use_relative_path_field(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[hosts.gateway]\ntransport = "local"\n[workspaces.app]\nhost = "gateway"\nroot = "{tmp_path}"\n''',
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(config_path)).list_tools()}
    for name in ("code_search", "json_query", "structured_query"):
        assert name in tools
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        fields = set(tools[name].inputSchema.get("properties", {}))
        assert "command" not in fields
        assert "script" not in fields
        assert "absolute_path" not in fields

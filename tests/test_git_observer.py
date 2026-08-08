import importlib
from collections import deque
from pathlib import Path

import pytest

from remote_observer_mcp.config import RepoConfig, load_config
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.server import create_server


class FakeTransport:
    def __init__(self, results: list[CommandResult]):
        self.results = deque(results)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return self.results.popleft()


def _result(stdout: str = "", *, exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code, stdout, "", 1, False, False)


@pytest.mark.asyncio
async def test_repo_status_filters_sensitive_paths_and_never_queries_remote_urls():
    module = importlib.import_module("remote_observer_mcp.observers.git")
    transport = FakeTransport(
        [_result("## main\n M src/app.py\n?? .env\n?? secrets/token.txt\n?? notes.txt\n")]
    )
    repo = RepoConfig("/srv/repo", secret_patterns=("secrets/**",))

    result = await module.repo_status(transport, repo)

    assert result == {
        "branch": "main",
        "changes": [
            {"code": " M", "path": "src/app.py"},
            {"code": "??", "path": "notes.txt"},
        ],
    }
    assert transport.commands[0].argv == (
        "git",
        "-C",
        "/srv/repo",
        "status",
        "--short",
        "--branch",
        "--untracked-files=normal",
    )
    assert "remote" not in transport.commands[0].argv


@pytest.mark.asyncio
async def test_repo_diff_excludes_sensitive_paths_in_primary_git_command():
    module = importlib.import_module("remote_observer_mcp.observers.git")
    transport = FakeTransport([_result("diff --git a/src/app.py b/src/app.py\n")])
    repo = RepoConfig("/srv/repo", secret_patterns=("secrets/**",))

    result = await module.repo_diff(transport, repo)

    assert result["diff"].startswith("diff --git a/src/app.py")
    argv = transport.commands[0].argv
    assert argv[:8] == (
        "git",
        "-C",
        "/srv/repo",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
    )
    exclusions = argv[9:]
    assert ":(exclude,glob).env" in exclusions
    assert ":(exclude,glob)**/.env" in exclusions
    assert ":(exclude,glob)**/*.pem" in exclusions
    assert ":(exclude,glob)**/*.key" in exclusions
    assert ":(exclude,glob)secrets/**" in exclusions
    assert len(transport.commands) == 1


@pytest.mark.asyncio
async def test_repo_log_clamps_count_and_returns_no_remote_metadata():
    module = importlib.import_module("remote_observer_mcp.observers.git")
    transport = FakeTransport([_result("abc123\t2026-08-08T00:00:00+00:00\tFix thing\n")])

    result = await module.repo_log(transport, RepoConfig("/srv/repo"), 999)

    assert result == [
        {"sha": "abc123", "date": "2026-08-08T00:00:00+00:00", "subject": "Fix thing"}
    ]
    argv = transport.commands[0].argv
    assert argv == (
        "git",
        "-C",
        "/srv/repo",
        "log",
        "--no-decorate",
        "--date=iso-strict",
        "--format=%H%x09%ad%x09%s",
        "-n",
        "100",
    )
    assert "remote" not in argv


@pytest.mark.asyncio
async def test_git_tools_are_registered_read_only(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[hosts.gateway]
transport = "local"
[hosts.gateway.repos.app]
path = "/srv/app"
""",
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(path)).list_tools()}

    assert {"repo_status", "repo_diff", "repo_log"}.issubset(tools)
    for name in ("repo_status", "repo_diff", "repo_log"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False

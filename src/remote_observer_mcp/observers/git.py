from __future__ import annotations

import fnmatch
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, RepoConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_DEFAULT_SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "*.key",
    "**/*.pem",
    "**/*.key",
)


async def repo_status(transport: Transport, repo: RepoConfig) -> dict[str, Any]:
    result = await transport.run(
        CommandSpec(
            argv=(
                "git",
                "-C",
                repo.path,
                "status",
                "--short",
                "--branch",
                "--untracked-files=normal",
            )
        )
    )
    _check_git_result(result)

    branch = "unknown"
    changes: list[dict[str, str]] = []
    patterns = _secret_patterns(repo)
    for line in result.stdout.splitlines():
        if line.startswith("## "):
            branch = line[3:].strip()
            continue
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:]
        if _is_sensitive_change(path, patterns):
            continue
        changes.append({"code": code, "path": path})
    return {"branch": branch, "changes": changes}


async def repo_diff(transport: Transport, repo: RepoConfig) -> dict[str, Any]:
    pathspecs = tuple(f":(exclude,glob){pattern}" for pattern in _secret_patterns(repo))
    result = await transport.run(
        CommandSpec(
            argv=(
                "git",
                "-C",
                repo.path,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
                ".",
                *pathspecs,
            )
        )
    )
    _check_git_result(result)
    return {
        "diff": result.stdout,
        "truncated": result.truncated,
        "redacted": result.redacted,
    }


async def repo_log(
    transport: Transport,
    repo: RepoConfig,
    count: int = 20,
) -> list[dict[str, str]]:
    bounded_count = min(max(count, 1), 100)
    result = await transport.run(
        CommandSpec(
            argv=(
                "git",
                "-C",
                repo.path,
                "log",
                "--no-decorate",
                "--date=iso-strict",
                "--format=%H%x09%ad%x09%s",
                "-n",
                str(bounded_count),
            )
        )
    )
    _check_git_result(result)

    entries: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        fields = line.split("\t", 2)
        if len(fields) != 3:
            raise ObserverError("command_failed", "unexpected Git log output")
        entries.append({"sha": fields[0], "date": fields[1], "subject": fields[2]})
    return entries


def register_tools(server: FastMCP, config: AppConfig) -> None:
    @server.tool(name="repo_status", annotations=_READ_ONLY, structured_output=True)
    async def repo_status_tool(host: str, repo: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            repo_config = host_config.repo(repo)
            return await repo_status(transport_for_host(host_config), repo_config)

        return await run_observed_tool(
            tool="repo_status",
            host_id=host,
            resource_id=repo,
            operation=operation,
        )

    @server.tool(name="repo_diff", annotations=_READ_ONLY, structured_output=True)
    async def repo_diff_tool(host: str, repo: str) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            repo_config = host_config.repo(repo)
            return await repo_diff(transport_for_host(host_config), repo_config)

        return await run_observed_tool(
            tool="repo_diff",
            host_id=host,
            resource_id=repo,
            operation=operation,
        )

    @server.tool(name="repo_log", annotations=_READ_ONLY, structured_output=True)
    async def repo_log_tool(host: str, repo: str, count: int = 20) -> dict[str, Any]:
        async def operation() -> Any:
            host_config = config.host(host)
            repo_config = host_config.repo(repo)
            return await repo_log(transport_for_host(host_config), repo_config, count)

        return await run_observed_tool(
            tool="repo_log",
            host_id=host,
            resource_id=repo,
            operation=operation,
        )


def _secret_patterns(repo: RepoConfig) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*_DEFAULT_SECRET_PATTERNS, *repo.secret_patterns)))


def _is_sensitive_change(path: str, patterns: tuple[str, ...]) -> bool:
    candidates = [part.strip() for part in path.split(" -> ")]
    return any(
        fnmatch.fnmatchcase(candidate, pattern)
        for candidate in candidates
        for pattern in patterns
    )


def _check_git_result(result: CommandResult) -> None:
    if result.exit_code == 0:
        return
    if result.exit_code == 127:
        raise ObserverError("unsupported_capability", "Git is unavailable")
    if "permission denied" in result.stderr.lower():
        raise ObserverError("permission_denied", "Git repository access is denied")
    raise ObserverError("command_failed", "Git observation failed")

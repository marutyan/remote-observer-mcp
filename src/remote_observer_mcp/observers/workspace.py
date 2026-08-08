from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.backends import resolve_backend
from remote_observer_mcp.config import AppConfig, WorkspaceConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport
from remote_observer_mcp.workspace import ensure_visible_relative_path, is_visible_relative_path

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_SEARCH_EXCLUDES = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "credentials*", "secrets*")
_GLOB_RE = re.compile(r"^[A-Za-z0-9_./*?+@{}!,\[\]-]{1,128}$")


def _absolute(workspace: WorkspaceConfig, relative: str) -> str:
    return workspace.root if relative == "." else f"{workspace.root.rstrip('/')}/{relative}"


def _relative(workspace: WorkspaceConfig, value: str) -> str:
    prefix = workspace.root.rstrip("/") + "/"
    if value == workspace.root:
        return "."
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value.lstrip("./")


def _check_result(result: CommandResult, message: str) -> None:
    if result.exit_code not in {0, 1}:
        raise ObserverError("command_failed", message)


async def workspace_find(
    transport: Transport,
    workspace: WorkspaceConfig,
    pattern: str | None = None,
    kind: str = "all",
    max_results: int = 100,
) -> dict[str, Any]:
    if kind not in {"all", "file", "directory"}:
        raise ObserverError("invalid_filter", "invalid workspace find filter")
    if pattern is not None and (
        not _GLOB_RE.fullmatch(pattern)
        or any(ch in pattern for ch in (";", "`", "$", "|", "&"))
    ):
        raise ObserverError("invalid_filter", "invalid workspace find pattern")
    limit = min(max(int(max_results), 1), 500)
    backend = await resolve_backend(transport, "find")
    if backend.variant == "fd":
        argv: list[str] = ["fd", "--color", "never", "--absolute-path"]
        if kind == "file":
            argv += ["--type", "f"]
        elif kind == "directory":
            argv += ["--type", "d"]
        argv += ["--", pattern or ".", workspace.root]
    else:
        argv = ["find", workspace.root]
        if kind == "file":
            argv += ["-type", "f"]
        elif kind == "directory":
            argv += ["-type", "d"]
        if pattern:
            argv += ["-name", pattern]
        argv += ["-print"]
    result = await transport.run(CommandSpec(argv=tuple(argv)))
    _check_result(result, "workspace find failed")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        rel = _relative(workspace, line.strip())
        if rel and is_visible_relative_path(workspace, rel):
            paths.append(rel)
        if len(paths) >= limit:
            break
    return {
        "backend": backend.variant,
        "paths": paths,
        "truncated": len(paths) >= limit or result.truncated,
    }


async def workspace_search(
    transport: Transport,
    workspace: WorkspaceConfig,
    query: str,
    glob: str | None = None,
    max_results: int = 100,
) -> dict[str, Any]:
    if (
        not isinstance(query, str)
        or not query
        or len(query) > 512
        or any(ord(ch) < 32 for ch in query)
    ):
        raise ObserverError("invalid_query", "invalid workspace search query")
    if glob is not None and not _GLOB_RE.fullmatch(glob):
        raise ObserverError("invalid_filter", "invalid workspace search glob")
    limit = min(max(int(max_results), 1), 500)
    backend = await resolve_backend(transport, "search")
    if backend.variant == "rg":
        argv: list[str] = ["rg", "--line-number", "--no-heading", "--color", "never"]
        for pattern in (*_SEARCH_EXCLUDES, *workspace.secret_patterns):
            argv += ["--glob", f"!{pattern}"]
        if glob:
            argv += ["--glob", glob]
        argv += ["--", query, workspace.root]
    else:
        if any("/" in pattern for pattern in workspace.secret_patterns):
            raise ObserverError(
                "unsupported_capability",
                "grep fallback cannot safely express configured path exclusions",
            )
        argv = ["grep", "-R", "-n", "-I"]
        for pattern in (*_SEARCH_EXCLUDES, *workspace.secret_patterns):
            argv.append(f"--exclude={pattern}")
        if glob:
            argv.append(f"--include={glob}")
        argv += ["--", query, workspace.root]
    result = await transport.run(CommandSpec(argv=tuple(argv)))
    _check_result(result, "workspace search failed")
    matches: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        rel = _relative(workspace, parts[0])
        if not is_visible_relative_path(workspace, rel):
            continue
        try:
            line_number = int(parts[1])
        except ValueError:
            continue
        matches.append({"path": rel, "line": line_number, "text": parts[2]})
        if len(matches) >= limit:
            break
    return {
        "backend": backend.variant,
        "matches": matches,
        "truncated": len(matches) >= limit or result.truncated,
    }


async def workspace_read(
    transport: Transport,
    workspace: WorkspaceConfig,
    relative_path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict[str, Any]:
    relative = ensure_visible_relative_path(workspace, relative_path)
    start = max(int(start_line), 1)
    end = start + 999 if end_line is None else max(int(end_line), start)
    end = min(end, start + 999)
    result = await transport.run(
        CommandSpec(argv=("sed", "-n", f"{start},{end}p", _absolute(workspace, relative)))
    )
    if result.exit_code != 0:
        raise ObserverError("command_failed", "workspace read failed")
    return {
        "relative_path": relative,
        "start_line": start,
        "end_line": end,
        "text": result.stdout,
        "truncated": result.truncated,
        "redacted": result.redacted,
    }


async def workspace_tree(
    transport: Transport,
    workspace: WorkspaceConfig,
    relative_path: str = ".",
    depth: int = 3,
) -> dict[str, Any]:
    relative = ensure_visible_relative_path(workspace, relative_path)
    level = min(max(int(depth), 1), 8)
    root = _absolute(workspace, relative)
    backend = await resolve_backend(transport, "tree")
    if backend.variant == "eza":
        argv = ("eza", "--tree", "--color", "never", "--level", str(level), root)
    else:
        argv = ("find", root, "-maxdepth", str(level), "-print")
    result = await transport.run(CommandSpec(argv=argv))
    if result.exit_code != 0:
        raise ObserverError("command_failed", "workspace tree failed")
    rows: list[str] = []
    for line in result.stdout.splitlines():
        candidate = _relative(workspace, line.strip()) if backend.variant == "find" else line.rstrip()
        if backend.variant != "find" or is_visible_relative_path(workspace, candidate):
            rows.append(candidate)
        if len(rows) >= 500:
            break
    return {
        "backend": backend.variant,
        "lines": rows,
        "truncated": len(rows) >= 500 or result.truncated,
    }


async def file_info(
    transport: Transport,
    workspace: WorkspaceConfig,
    relative_path: str,
) -> dict[str, Any]:
    relative = ensure_visible_relative_path(workspace, relative_path)
    absolute = _absolute(workspace, relative)
    stat_result = await transport.run(CommandSpec(argv=("stat", "-c", "%s\t%Y\t%F", absolute)))
    if stat_result.exit_code != 0:
        raise ObserverError("command_failed", "file metadata observation failed")
    fields = stat_result.stdout.strip().split("\t", 2)
    if len(fields) != 3:
        raise ObserverError("command_failed", "unexpected file metadata output")
    return {
        "relative_path": relative,
        "size_bytes": int(fields[0]),
        "mtime_epoch": int(fields[1]),
        "type": fields[2],
    }


async def checksum(
    transport: Transport,
    workspace: WorkspaceConfig,
    relative_path: str,
) -> dict[str, Any]:
    relative = ensure_visible_relative_path(workspace, relative_path)
    absolute = _absolute(workspace, relative)
    backend = await resolve_backend(transport, "checksum")
    if backend.variant == "shasum":
        argv = ("shasum", "-a", "256", absolute)
        algorithm = "sha256"
    else:
        assert backend.executable is not None
        argv = (backend.executable, absolute)
        algorithm = "blake3" if backend.variant == "b3sum" else "sha256"
    result = await transport.run(CommandSpec(argv=argv))
    if result.exit_code != 0 or not result.stdout.strip():
        raise ObserverError("command_failed", "checksum observation failed")
    return {
        "relative_path": relative,
        "algorithm": algorithm,
        "digest": result.stdout.split()[0],
    }


def register_tools(server: FastMCP, config: AppConfig) -> None:
    async def workspace_call(
        tool: str,
        workspace_id: str,
        operation: Any,
    ) -> dict[str, Any]:
        return await run_observed_tool(
            tool=tool,
            host_id=None,
            resource_id=workspace_id,
            operation=operation,
        )

    @server.tool(name="list_workspaces", annotations=_READ_ONLY, structured_output=True)
    async def list_workspaces_tool() -> dict[str, Any]:
        async def operation() -> Any:
            return [
                {"id": item.workspace_id, "host": item.host_id, "compose": item.compose}
                for item in sorted(config.workspaces.values(), key=lambda item: item.workspace_id)
            ]

        return await run_observed_tool(
            tool="list_workspaces",
            host_id=None,
            resource_id=None,
            operation=operation,
        )

    @server.tool(name="workspace_find", annotations=_READ_ONLY, structured_output=True)
    async def workspace_find_tool(
        workspace: str,
        pattern: str | None = None,
        kind: str = "all",
        max_results: int = 100,
    ) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await workspace_find(
                transport_for_host(config.host(item.host_id)), item, pattern, kind, max_results
            )

        return await workspace_call("workspace_find", workspace, operation)

    @server.tool(name="workspace_search", annotations=_READ_ONLY, structured_output=True)
    async def workspace_search_tool(
        workspace: str,
        query: str,
        glob: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await workspace_search(
                transport_for_host(config.host(item.host_id)), item, query, glob, max_results
            )

        return await workspace_call("workspace_search", workspace, operation)

    @server.tool(name="workspace_read", annotations=_READ_ONLY, structured_output=True)
    async def workspace_read_tool(
        workspace: str,
        relative_path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await workspace_read(
                transport_for_host(config.host(item.host_id)),
                item,
                relative_path,
                start_line,
                end_line,
            )

        return await workspace_call("workspace_read", workspace, operation)

    @server.tool(name="workspace_tree", annotations=_READ_ONLY, structured_output=True)
    async def workspace_tree_tool(
        workspace: str,
        relative_path: str = ".",
        depth: int = 3,
    ) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await workspace_tree(
                transport_for_host(config.host(item.host_id)), item, relative_path, depth
            )

        return await workspace_call("workspace_tree", workspace, operation)

    @server.tool(name="file_info", annotations=_READ_ONLY, structured_output=True)
    async def file_info_tool(workspace: str, relative_path: str) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await file_info(
                transport_for_host(config.host(item.host_id)), item, relative_path
            )

        return await workspace_call("file_info", workspace, operation)

    @server.tool(name="checksum", annotations=_READ_ONLY, structured_output=True)
    async def checksum_tool(workspace: str, relative_path: str) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await checksum(
                transport_for_host(config.host(item.host_id)), item, relative_path
            )

        return await workspace_call("checksum", workspace, operation)

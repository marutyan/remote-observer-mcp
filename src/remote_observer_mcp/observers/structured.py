from __future__ import annotations

import json
import re
import tomllib
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, WorkspaceConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport
from remote_observer_mcp.workspace import ensure_visible_relative_path, is_visible_relative_path

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_LANGUAGES = {
    "python": "python",
    "rust": "rust",
    "javascript": "javascript",
    "typescript": "typescript",
    "json": "json",
    "yaml": "yaml",
}
_SELECTOR_TOKEN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)(\[[0-9]+\])?")
_PATTERN_MAX = 2048


def _absolute(workspace: WorkspaceConfig, relative: str) -> str:
    return f"{workspace.root.rstrip('/')}/{relative}" if relative != "." else workspace.root


def _relative(workspace: WorkspaceConfig, value: str) -> str:
    prefix = workspace.root.rstrip("/") + "/"
    return value[len(prefix) :] if value.startswith(prefix) else value.lstrip("./")


def _selector_parts(selector: str) -> list[str | int]:
    if not isinstance(selector, str) or not selector or len(selector) > 512:
        raise ObserverError("invalid_selector", "invalid structured selector")
    result: list[str | int] = []
    for chunk in selector.split("."):
        match = _SELECTOR_TOKEN_RE.fullmatch(chunk)
        if not match:
            raise ObserverError("invalid_selector", "invalid structured selector")
        result.append(match.group(1))
        bracket = match.group(2)
        if bracket:
            result.append(int(bracket[1:-1]))
    return result


def _select(document: Any, selector: str) -> Any:
    value = document
    for part in _selector_parts(selector):
        try:
            if isinstance(part, int):
                if not isinstance(value, list):
                    raise TypeError
                value = value[part]
            else:
                if not isinstance(value, dict):
                    raise TypeError
                value = value[part]
        except (KeyError, IndexError, TypeError) as error:
            raise ObserverError("selector_not_found", "structured selector did not match") from error
    return value


async def code_search(
    transport: Transport,
    workspace: WorkspaceConfig,
    language: str,
    pattern: str,
    max_results: int = 100,
) -> list[dict[str, Any]]:
    lang = _LANGUAGES.get(language)
    if lang is None:
        raise ObserverError("invalid_language", "unsupported code-search language")
    if (
        not isinstance(pattern, str)
        or not pattern
        or len(pattern) > _PATTERN_MAX
        or any(ord(ch) < 32 and ch not in "\t" for ch in pattern)
    ):
        raise ObserverError("invalid_query", "invalid AST search pattern")
    limit = min(max(int(max_results), 1), 500)
    result = await transport.run(
        CommandSpec(
            argv=(
                "ast-grep",
                "run",
                "--pattern",
                pattern,
                "--lang",
                lang,
                "--json=stream",
                workspace.root,
            )
        )
    )
    if result.exit_code == 127 or "command not found" in result.stderr.lower():
        raise ObserverError("unsupported_capability", "ast-grep is unavailable")
    if result.exit_code not in {0, 1}:
        raise ObserverError("command_failed", "AST search failed")
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = item.get("file") or item.get("path")
        if not isinstance(path, str):
            continue
        relative = _relative(workspace, path)
        if not is_visible_relative_path(workspace, relative):
            continue
        line_number = item.get("line")
        if not isinstance(line_number, int):
            start = item.get("range", {}).get("start", {}) if isinstance(item.get("range"), dict) else {}
            raw_line = start.get("line") if isinstance(start, dict) else None
            line_number = raw_line + 1 if isinstance(raw_line, int) else None
        text = item.get("text") or item.get("metaVariables", {}).get("single") or ""
        rows.append({"path": relative, "line": line_number, "text": text})
        if len(rows) >= limit:
            break
    return rows


async def query_document(
    transport: Transport,
    workspace: WorkspaceConfig,
    relative_path: str,
    selector: str,
) -> Any:
    relative = ensure_visible_relative_path(workspace, relative_path)
    parts = _selector_parts(selector)
    del parts  # validation occurs before collection; selection reparses for one source of truth.
    absolute = _absolute(workspace, relative)
    lower = relative.lower()
    if lower.endswith(".json"):
        result = await transport.run(CommandSpec(argv=("cat", absolute)))
        if result.exit_code != 0:
            raise ObserverError("command_failed", "JSON read failed")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ObserverError("invalid_document", "invalid JSON document") from error
    elif lower.endswith(".toml"):
        result = await transport.run(CommandSpec(argv=("cat", absolute)))
        if result.exit_code != 0:
            raise ObserverError("command_failed", "TOML read failed")
        try:
            document = tomllib.loads(result.stdout)
        except tomllib.TOMLDecodeError as error:
            raise ObserverError("invalid_document", "invalid TOML document") from error
    elif lower.endswith((".yaml", ".yml")):
        result = await transport.run(CommandSpec(argv=("yq", "-o=json", ".", absolute)))
        if result.exit_code == 127 or "command not found" in result.stderr.lower():
            raise ObserverError("unsupported_capability", "safe YAML backend is unavailable")
        if result.exit_code != 0:
            raise ObserverError("command_failed", "YAML read failed")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ObserverError("invalid_document", "invalid YAML conversion output") from error
    else:
        raise ObserverError("unsupported_capability", "unsupported structured document type")
    return _select(document, selector)


def register_tools(server: FastMCP, config: AppConfig) -> None:
    async def call(tool: str, workspace_id: str, operation: Any) -> dict[str, Any]:
        return await run_observed_tool(tool=tool, host_id=None, resource_id=workspace_id, operation=operation)

    @server.tool(name="code_search", annotations=_READ_ONLY, structured_output=True)
    async def code_search_tool(workspace: str, language: str, pattern: str, max_results: int = 100) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return await code_search(transport_for_host(config.host(item.host_id)), item, language, pattern, max_results)
        return await call("code_search", workspace, operation)

    @server.tool(name="json_query", annotations=_READ_ONLY, structured_output=True)
    async def json_query_tool(workspace: str, relative_path: str, selector: str) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            if not relative_path.lower().endswith(".json"):
                raise ObserverError("unsupported_capability", "json_query requires a JSON file")
            return {"value": await query_document(transport_for_host(config.host(item.host_id)), item, relative_path, selector)}
        return await call("json_query", workspace, operation)

    @server.tool(name="structured_query", annotations=_READ_ONLY, structured_output=True)
    async def structured_query_tool(workspace: str, relative_path: str, selector: str) -> dict[str, Any]:
        async def operation() -> Any:
            item = config.workspace(workspace)
            return {"value": await query_document(transport_for_host(config.host(item.host_id)), item, relative_path, selector)}
        return await call("structured_query", workspace, operation)

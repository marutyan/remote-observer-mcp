from __future__ import annotations

import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from remote_observer_mcp.audit import run_observed_tool
from remote_observer_mcp.config import AppConfig, WorkspaceConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandSpec
from remote_observer_mcp.transports import transport_for_host
from remote_observer_mcp.transports.base import Transport
from remote_observer_mcp.workspace import ensure_visible_relative_path

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_RUNTIME_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", ("python3", "--version")),
    ("uv", ("uv", "--version")),
    ("node", ("node", "--version")),
    ("npm", ("npm", "--version")),
    ("pnpm", ("pnpm", "--version")),
    ("rustc", ("rustc", "--version")),
    ("cargo", ("cargo", "--version")),
    ("rustup", ("rustup", "--version")),
    ("nix", ("nix", "--version")),
    ("git", ("git", "--version")),
    ("docker", ("docker", "--version")),
    ("tmux", ("tmux", "-V")),
)
_AVAILABILITY = (
    "rg", "grep", "fd", "find", "eza", "dust", "du", "procs", "ps", "jq", "yq",
    "ast-grep", "tmux", "ss", "ip", "getent", "docker", "nvidia-smi", "python3", "uv",
    "node", "npm", "pnpm", "rustc", "cargo", "rustup", "nix", "mise", "tar", "unzip", "7z",
)
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:_@/-]{0,127}$")


def _absolute(workspace: WorkspaceConfig, relative: str) -> str:
    return f"{workspace.root.rstrip('/')}/{relative}" if relative != "." else workspace.root


async def tool_availability(transport: Transport) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for executable in _AVAILABILITY:
        probe = await transport.run(
            CommandSpec(
                argv=("sh", "-c", f"command -v {executable} >/dev/null 2>&1"),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
        )
        result[executable] = probe.exit_code == 0
    return result


async def runtime_versions(transport: Transport) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name, argv in _RUNTIME_COMMANDS:
        result = await transport.run(CommandSpec(argv=argv, timeout_seconds=5, max_output_bytes=4096))
        text = (result.stdout or result.stderr).strip().splitlines()
        versions[name] = text[0] if result.exit_code == 0 and text else None
    return versions


async def python_environment(transport: Transport, workspace: WorkspaceConfig) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name, argv in (
        ("python", ("python3", "--version")),
        ("pip", ("python3", "-m", "pip", "--version")),
        ("uv", ("uv", "--version")),
    ):
        result = await transport.run(CommandSpec(argv=argv, timeout_seconds=5, max_output_bytes=4096))
        if result.exit_code == 0:
            data[name] = (result.stdout or result.stderr).strip().splitlines()[:1]
    data["workspace"] = workspace.workspace_id
    return data


async def node_environment(transport: Transport, workspace: WorkspaceConfig) -> dict[str, Any]:
    data: dict[str, Any] = {"workspace": workspace.workspace_id}
    for name, argv in (
        ("node", ("node", "--version")),
        ("npm", ("npm", "--version")),
        ("pnpm", ("pnpm", "--version")),
    ):
        result = await transport.run(CommandSpec(argv=argv, timeout_seconds=5, max_output_bytes=4096))
        if result.exit_code == 0:
            data[name] = (result.stdout or result.stderr).strip()
    return data


async def rust_environment(transport: Transport, workspace: WorkspaceConfig) -> dict[str, Any]:
    data: dict[str, Any] = {"workspace": workspace.workspace_id}
    for name, argv in (
        ("rustc", ("rustc", "--version")),
        ("cargo", ("cargo", "--version")),
        ("rustup", ("rustup", "show", "active-toolchain")),
    ):
        result = await transport.run(CommandSpec(argv=argv, timeout_seconds=5, max_output_bytes=4096))
        if result.exit_code == 0:
            data[name] = (result.stdout or result.stderr).strip()
    return data


async def cargo_metadata(transport: Transport, workspace: WorkspaceConfig) -> dict[str, Any]:
    result = await transport.run(
        CommandSpec(
            argv=(
                "cargo", "metadata", "--offline", "--format-version", "1", "--no-deps",
                "--manifest-path", f"{workspace.root.rstrip('/')}/Cargo.toml",
            ),
            timeout_seconds=15,
        )
    )
    if result.exit_code != 0:
        raise ObserverError("command_failed", "cargo metadata observation failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ObserverError("command_failed", "unexpected cargo metadata output") from error
    if not isinstance(payload, dict):
        raise ObserverError("command_failed", "unexpected cargo metadata output")
    payload.pop("target_directory", None)
    return payload


async def nix_environment(transport: Transport, workspace: WorkspaceConfig) -> dict[str, Any]:
    version = await transport.run(CommandSpec(argv=("nix", "--version"), timeout_seconds=5))
    if version.exit_code != 0:
        raise ObserverError("unsupported_capability", "Nix is unavailable")
    metadata = await transport.run(
        CommandSpec(
            argv=(
                "nix", "flake", "metadata", "--offline", "--no-write-lock-file", "--json", workspace.root
            ),
            timeout_seconds=15,
        )
    )
    data: dict[str, Any] = {"version": (version.stdout or version.stderr).strip()}
    if metadata.exit_code == 0:
        try:
            parsed = json.loads(metadata.stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("description", "revision", "lastModified", "locked"):
                if key in parsed:
                    data[key] = parsed[key]
    return data


async def mise_environment(transport: Transport, workspace: WorkspaceConfig) -> dict[str, Any]:
    result = await transport.run(
        CommandSpec(argv=("mise", "ls", "--json", "--current"), timeout_seconds=10)
    )
    if result.exit_code != 0:
        raise ObserverError("unsupported_capability", "mise environment is unavailable")
    try:
        tools = json.loads(result.stdout)
    except json.JSONDecodeError:
        tools = result.stdout.splitlines()[:200]
    return {"workspace": workspace.workspace_id, "tools": tools}


async def package_info(transport: Transport, package: str) -> dict[str, Any]:
    if not isinstance(package, str) or not _PACKAGE_RE.fullmatch(package) or ".." in package:
        raise ObserverError("invalid_package", "invalid package name")
    dpkg = await transport.run(
        CommandSpec(argv=("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n", package), timeout_seconds=10)
    )
    if dpkg.exit_code == 0:
        return {"backend": "dpkg", "lines": dpkg.stdout.splitlines()[:100]}
    brew = await transport.run(
        CommandSpec(
            argv=("env", "HOMEBREW_NO_AUTO_UPDATE=1", "brew", "info", "--json=v2", package),
            timeout_seconds=15,
        )
    )
    if brew.exit_code == 0:
        try:
            return {"backend": "brew", "data": json.loads(brew.stdout)}
        except json.JSONDecodeError:
            pass
    raise ObserverError("unsupported_capability", "package metadata is unavailable")


async def archive_list(
    transport: Transport,
    workspace: WorkspaceConfig,
    relative_path: str,
) -> list[str]:
    relative = ensure_visible_relative_path(workspace, relative_path)
    absolute = _absolute(workspace, relative)
    lower = relative.lower()
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")):
        argv = ("tar", "-tf", absolute)
    elif lower.endswith(".zip"):
        argv = ("unzip", "-Z1", absolute)
    elif lower.endswith((".7z", ".rar")):
        argv = ("7z", "l", "-ba", absolute)
    else:
        raise ObserverError("unsupported_capability", "unsupported archive type")
    result = await transport.run(CommandSpec(argv=argv, timeout_seconds=15))
    if result.exit_code != 0:
        raise ObserverError("command_failed", "archive listing failed")
    return result.stdout.splitlines()[:1000]


def register_tools(server: FastMCP, config: AppConfig) -> None:
    def host_tx(host: str) -> Transport:
        return transport_for_host(config.host(host))

    def workspace_parts(workspace: str) -> tuple[WorkspaceConfig, Transport]:
        item = config.workspace(workspace)
        return item, transport_for_host(config.host(item.host_id))

    async def host_call(name: str, host: str, operation: Any) -> dict[str, Any]:
        return await run_observed_tool(tool=name, host_id=host, resource_id=None, operation=operation)

    async def ws_call(name: str, workspace: str, operation: Any) -> dict[str, Any]:
        return await run_observed_tool(tool=name, host_id=None, resource_id=workspace, operation=operation)

    @server.tool(name="tool_availability", annotations=_READ_ONLY, structured_output=True)
    async def tool_availability_tool(host: str) -> dict[str, Any]:
        return await host_call("tool_availability", host, lambda: tool_availability(host_tx(host)))

    @server.tool(name="runtime_versions", annotations=_READ_ONLY, structured_output=True)
    async def runtime_versions_tool(host: str) -> dict[str, Any]:
        return await host_call("runtime_versions", host, lambda: runtime_versions(host_tx(host)))

    @server.tool(name="python_environment", annotations=_READ_ONLY, structured_output=True)
    async def python_environment_tool(workspace: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("python_environment", workspace, lambda: python_environment(tx, item))

    @server.tool(name="node_environment", annotations=_READ_ONLY, structured_output=True)
    async def node_environment_tool(workspace: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("node_environment", workspace, lambda: node_environment(tx, item))

    @server.tool(name="rust_environment", annotations=_READ_ONLY, structured_output=True)
    async def rust_environment_tool(workspace: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("rust_environment", workspace, lambda: rust_environment(tx, item))

    @server.tool(name="cargo_metadata", annotations=_READ_ONLY, structured_output=True)
    async def cargo_metadata_tool(workspace: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("cargo_metadata", workspace, lambda: cargo_metadata(tx, item))

    @server.tool(name="nix_environment", annotations=_READ_ONLY, structured_output=True)
    async def nix_environment_tool(workspace: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("nix_environment", workspace, lambda: nix_environment(tx, item))

    @server.tool(name="mise_environment", annotations=_READ_ONLY, structured_output=True)
    async def mise_environment_tool(workspace: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("mise_environment", workspace, lambda: mise_environment(tx, item))

    @server.tool(name="package_info", annotations=_READ_ONLY, structured_output=True)
    async def package_info_tool(host: str, package: str) -> dict[str, Any]:
        return await host_call("package_info", host, lambda: package_info(host_tx(host), package))

    @server.tool(name="archive_list", annotations=_READ_ONLY, structured_output=True)
    async def archive_list_tool(workspace: str, relative_path: str) -> dict[str, Any]:
        item, tx = workspace_parts(workspace)
        return await ws_call("archive_list", workspace, lambda: archive_list(tx, item, relative_path))

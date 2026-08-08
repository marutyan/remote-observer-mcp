from pathlib import Path

import pytest

from remote_observer_mcp.config import WorkspaceConfig, load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.observers.diagnostics import dns_lookup
from remote_observer_mcp.observers.environment import package_info
from remote_observer_mcp.observers.structured import query_document
from remote_observer_mcp.observers.tmux import tmux_capture
from remote_observer_mcp.server import create_server
from remote_observer_mcp.workspace import ensure_visible_relative_path

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_TOOL_NAMES = {
    "execute",
    "shell",
    "ssh",
    "run_command",
    "approved_execute",
    "read_any_file",
    "write_file",
}
_FORBIDDEN_FIELDS = {
    "command",
    "argv",
    "hostname",
    "absolute_path",
    "shell",
    "script",
    "url",
    "method",
    "tmux_user",
    "user",
    "sudo",
    "socket",
    "helper",
    "helper_path",
}


class FailTransport:
    async def run(self, command):
        raise AssertionError(f"transport must not run: {command!r}")


@pytest.mark.asyncio
async def test_every_integrated_mcp_tool_keeps_closed_read_only_annotations(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[hosts.gateway]\ntransport = "local"\ngpu = true\n\n[hosts.gateway.services.app]\nunit = "app.service"\nlogs = true\n\n[hosts.gateway.containers.api]\nname = "api"\nlogs = true\n\n[hosts.gateway.repos.app]\npath = "{tmp_path}"\n\n[hosts.gateway.processes.worker]\nname = "worker"\n\n[workspaces.app]\nhost = "gateway"\nroot = "{tmp_path}"\ncompose = true\n''',
        encoding="utf-8",
    )
    tools = await create_server(load_config(config_path)).list_tools()
    names = {tool.name for tool in tools}

    assert names.isdisjoint(_FORBIDDEN_TOOL_NAMES)
    assert {
        "workspace_search",
        "code_search",
        "tmux_capture",
        "network_listeners",
        "journal_query",
        "container_stats",
        "gpu_processes",
        "runtime_versions",
        "nix_environment",
        "archive_list",
    }.issubset(names)

    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        fields = set(tool.inputSchema.get("properties", {}))
        assert fields.isdisjoint(_FORBIDDEN_FIELDS), (tool.name, fields & _FORBIDDEN_FIELDS)


@pytest.mark.asyncio
async def test_cross_user_tmux_config_does_not_change_mcp_schema(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[hosts.emma]\ntransport = "local"\ntmux_user = "emma"\n',
        encoding="utf-8",
    )
    tools = {tool.name: tool for tool in await create_server(load_config(config_path)).list_tools()}

    for name in ("tmux_sessions", "tmux_windows", "tmux_panes", "tmux_capture"):
        fields = set(tools[name].inputSchema.get("properties", {}))
        assert fields.isdisjoint(
            {"tmux_user", "user", "sudo", "socket", "command", "helper", "helper_path"}
        )


@pytest.mark.asyncio
async def test_injection_shaped_inputs_fail_before_transport():
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    transport = FailTransport()

    with pytest.raises(ObserverError):
        ensure_visible_relative_path(workspace, "../.env")
    with pytest.raises(ObserverError):
        await tmux_capture(transport, "%1;send-keys", 10)
    with pytest.raises(ObserverError):
        await dns_lookup(transport, "example.test/$(id)")
    with pytest.raises(ObserverError):
        await package_info(transport, "pkg;id")
    with pytest.raises(ObserverError):
        await query_document(transport, workspace, "data.json", "a|keys")


@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", "keys/server.pem", "private/id_rsa.key", "credentials.json"],
)
def test_workspace_secret_content_is_blocked_before_collection(path: str):
    workspace = WorkspaceConfig("app", "gateway", "/srv/app")
    with pytest.raises(ObserverError) as error:
        ensure_visible_relative_path(workspace, path)
    assert error.value.code == "sensitive_path"


def test_approval_workflow_gate_is_on_self_hosted_execution_job():
    text = (_ROOT / ".github" / "workflows" / "approved-execution.yml").read_text(
        encoding="utf-8"
    )
    execute = text.split("  execute:\n", 1)[1]
    assert "environment: remote-execution" in execute
    assert "runs-on: [self-hosted, remote-observer]" in execute
    assert "remote-observer-exec" in execute
    assert "command:" not in text
    assert "script:" not in text
    assert "argv:" not in text
    assert "issues." not in text
    assert "github.event.comment" not in text


def test_completion_docs_cover_generic_tools_and_external_approval():
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    actions = (_ROOT / "USER_ACTIONS.md").read_text(encoding="utf-8")

    for marker in (
        "workspace_search",
        "tmux_capture",
        "runtime_versions",
        "nix_environment",
        "Execution Bridge",
        "remote-observer-exec",
    ):
        assert marker in readme

    assert "remote-execution" in actions
    assert "required reviewer" in actions.lower()
    assert "self-hosted" in actions
    assert "追加費用" in actions


def test_cross_user_tmux_deployment_documents_narrow_sudo_boundary():
    sudoers_path = _ROOT / "deploy" / "remote-observer-tmux-read.sudoers.example"
    assert sudoers_path.exists(), "narrow tmux sudoers example is missing"

    sudoers = sudoers_path.read_text(encoding="utf-8")
    deploy = (_ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    actions = (_ROOT / "USER_ACTIONS.md").read_text(encoding="utf-8")

    assert (
        "remote-observer ALL=(emma) NOPASSWD: "
        "/usr/local/libexec/remote-observer-tmux-read *"
    ) in sudoers
    assert "/usr/bin/tmux" not in sudoers
    assert "/bin/sh" not in sudoers
    assert "/bin/bash" not in sudoers

    for marker in (
        "/usr/local/libexec/remote-observer-tmux-read",
        "root:root",
        "visudo -cf",
        'tmux_user = "emma"',
        "sudo -n -u emma",
    ):
        assert marker in deploy or marker in actions

    assert "direct /usr/bin/tmux" in deploy or "direct `/usr/bin/tmux`" in deploy
    assert "general sudo -u emma" in deploy or "general `sudo -u emma`" in deploy

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICE = _REPO_ROOT / "deploy" / "tunnel-client.service.example"
_RUNBOOK = _REPO_ROOT / "deploy" / "README.md"
_USER_ACTIONS = _REPO_ROOT / "USER_ACTIONS.md"


def test_deployment_uses_tunnel_client_as_stdio_supervisor():
    service = _SERVICE.read_text(encoding="utf-8")

    assert "User=remote-observer" in service
    assert "Environment=REMOTE_OBSERVER_CONFIG=/etc/remote-observer-mcp/config.toml" in service
    assert "ExecStart=/usr/local/bin/tunnel-client run" in service
    assert "--profile remote-observer" in service
    assert "--control-plane.api-key=file:/etc/remote-observer-mcp/runtime-api-key" in service
    assert "RuntimeDirectory=remote-observer-mcp" in service
    assert "StateDirectory=remote-observer-mcp" in service
    assert not (_REPO_ROOT / "deploy" / "remote-observer-mcp.service").exists()


def test_deployment_examples_do_not_embed_privileged_or_literal_credentials():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_SERVICE, _RUNBOOK, _USER_ACTIONS)
    )

    assert "OPENAI_ADMIN_KEY=" not in combined
    assert "sk-" not in combined
    assert "BEGIN OPENSSH PRIVATE KEY" not in combined
    assert "BEGIN PRIVATE KEY" not in combined
    assert "CONTROL_PLANE_API_KEY=" not in _SERVICE.read_text(encoding="utf-8")
    assert "file:/etc/remote-observer-mcp/runtime-api-key" in combined


def test_user_actions_preserve_no_extra_cost_and_read_only_gates():
    actions = _USER_ACTIONS.read_text(encoding="utf-8")

    assert "追加費用" in actions
    assert "STOP" in actions
    assert "Tunnels Read + Use" in actions
    assert "Restricted" in actions
    assert "tunnel-client init --sample sample_mcp_stdio_local" in actions
    assert "tunnel-client doctor" in actions
    assert "--profile remote-observer" in actions
    assert "StrictHostKeyChecking=yes" in actions
    assert "systemctl enable --now tunnel-client.service" in actions

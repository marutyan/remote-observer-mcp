from pathlib import Path

import pytest
from remote_observer_mcp.workspace import ensure_visible_relative_path, normalize_relative_path

from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_workspace_registry_resolves_registered_host_and_policy(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        """
[hosts.emma]
transport = "ssh"
ssh_alias = "emma"

[workspaces.paperapp]
host = "emma"
root = "/srv/paperapp"
secret_patterns = ["private/**"]
compose = true
""",
    )

    config = load_config(path)
    workspace = config.workspace("paperapp")

    assert workspace.workspace_id == "paperapp"
    assert workspace.host_id == "emma"
    assert workspace.root == "/srv/paperapp"
    assert workspace.secret_patterns == ("private/**",)
    assert workspace.compose is True


def test_workspace_registry_rejects_unknown_host_reference(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        """
[hosts.gateway]
transport = "local"

[workspaces.bad]
host = "missing"
root = "/srv/example"
""",
    )

    with pytest.raises(ObserverError) as error:
        load_config(path)

    assert error.value.code == "invalid_configuration"
    assert "missing" not in error.value.message


def test_workspace_lookup_rejects_unknown_and_unsafe_ids_without_echo(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        """
[hosts.gateway]
transport = "local"

[workspaces.safe]
host = "gateway"
root = "/srv/example"
""",
    )
    config = load_config(path)

    with pytest.raises(ObserverError) as missing:
        config.workspace("missing")
    assert missing.value.code == "unknown_workspace"

    with pytest.raises(ObserverError) as unsafe:
        config.workspace("bad\nsecret-marker")
    assert unsafe.value.code == "unknown_workspace"
    assert "secret-marker" not in unsafe.value.message


@pytest.mark.parametrize(
    "value",
    [
        "/absolute/path",
        "../escape",
        "nested/../../escape",
        "bad\\windows",
        "bad\nname",
        "bad\x01name",
        "",
    ],
)
def test_relative_path_normalization_fails_closed(value: str):
    with pytest.raises(ObserverError) as error:
        normalize_relative_path(value)
    assert error.value.code == "invalid_path"


def test_relative_path_normalization_accepts_safe_posix_segments():
    assert normalize_relative_path("src/./app.py") == "src/app.py"
    assert normalize_relative_path(".") == "."


def test_workspace_secret_paths_are_denied_before_content_access(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        """
[hosts.gateway]
transport = "local"

[workspaces.app]
host = "gateway"
root = "/srv/app"
secret_patterns = ["private/**"]
""",
    )
    workspace = load_config(path).workspace("app")

    for value in (
        ".env",
        ".env.local",
        "config/id_rsa.key",
        "certs/server.pem",
        "private/token.txt",
        "credentials.json",
    ):
        with pytest.raises(ObserverError) as error:
            ensure_visible_relative_path(workspace, value)
        assert error.value.code == "sensitive_path"
        assert value not in error.value.message

    assert ensure_visible_relative_path(workspace, "src/app.py") == "src/app.py"


@pytest.mark.parametrize(
    "config_text",
    [
        """
[hosts.gateway]
transport = "local"
[workspaces.bad]
host = "gateway"
root = "relative/path"
""",
        """
[hosts.gateway]
transport = "local"
[workspaces.bad]
host = "gateway"
root = "/srv/app"
secret_patterns = ["'; touch /tmp/owned #"]
""",
        """
[hosts.gateway]
transport = "local"
[workspaces.bad]
host = "gateway"
root = "/srv/app"
compose = "yes"
""",
    ],
)
def test_workspace_config_rejects_unsafe_values(tmp_path: Path, config_text: str):
    path = _write(tmp_path / "unsafe.toml", config_text)

    with pytest.raises(ObserverError) as error:
        load_config(path)
    assert error.value.code == "invalid_configuration"

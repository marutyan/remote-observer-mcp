from pathlib import Path

import pytest

from remote_observer_mcp.config import load_config
from remote_observer_mcp.errors import ObserverError


def _write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_registry_resolves_registered_resources(tmp_path: Path):
    path = _write_config(
        tmp_path / "config.toml",
        """
[hosts.gateway]
transport = "local"

[hosts.emma]
transport = "ssh"
ssh_alias = "emma"
gpu = true

[hosts.emma.services.callbot]
unit = "callbot.service"
logs = true

[hosts.emma.repos.paperapp]
path = "/srv/paperapp"

[hosts.emma.containers.api]
name = "paperapp-api"
logs = true

[hosts.emma.processes.worker]
name = "paper-worker"
""",
    )

    config = load_config(path)

    assert config.host("gateway").transport == "local"
    emma = config.host("emma")
    assert emma.ssh_alias == "emma"
    assert emma.gpu is True
    assert emma.service("callbot").unit == "callbot.service"
    assert emma.service("callbot").logs is True
    assert emma.repo("paperapp").path == "/srv/paperapp"
    assert emma.container("api").name == "paperapp-api"
    assert emma.process("worker").name == "paper-worker"


def test_registry_rejects_unknown_host_and_resource(tmp_path: Path):
    path = _write_config(
        tmp_path / "config.toml",
        """
[hosts.gateway]
transport = "local"
""",
    )
    config = load_config(path)

    with pytest.raises(ObserverError) as host_error:
        config.host("missing")
    assert host_error.value.code == "unknown_host"

    with pytest.raises(ObserverError) as resource_error:
        config.host("gateway").service("missing")
    assert resource_error.value.code == "unknown_resource"


def test_registry_does_not_echo_unsafe_lookup_identifiers(tmp_path: Path):
    path = _write_config(
        tmp_path / "config.toml",
        """
[hosts.gateway]
transport = "local"
""",
    )
    config = load_config(path)

    with pytest.raises(ObserverError) as error:
        config.host("bad\nidentifier")

    assert error.value.code == "unknown_host"
    assert "\n" not in error.value.message
    assert "bad" not in error.value.message


@pytest.mark.parametrize(
    "config_text",
    [
        """
[hosts.bad]
transport = "ssh"
ssh_alias = "bad;host"
""",
        """
[hosts.bad]
transport = "local"
[hosts.bad.repos.repo]
path = "relative/path"
""",
        """
[hosts.bad]
transport = "local"
[hosts.bad.repos.repo]
path = "/srv/repo"
secret_patterns = ["'; touch /tmp/owned #"]
""",
        """
[hosts.bad]
transport = "local"
[hosts.bad.services.app]
unit = "app$(id).service"
""",
    ],
)
def test_config_rejects_values_that_could_become_remote_syntax(
    tmp_path: Path, config_text: str
):
    path = _write_config(tmp_path / "unsafe.toml", config_text)

    with pytest.raises(ObserverError) as error:
        load_config(path)
    assert error.value.code == "invalid_configuration"

def test_local_host_accepts_tmux_user(tmp_path: Path):
    path = _write_config(
        tmp_path / "config.toml",
        """
[hosts.emma]
transport = "local"
tmux_user = "emma"
""",
    )

    config = load_config(path)
    assert config.host("emma").tmux_user == "emma"


def test_ssh_host_rejects_tmux_user(tmp_path: Path):
    path = _write_config(
        tmp_path / "config.toml",
        """
[hosts.remote]
transport = "ssh"
ssh_alias = "remote"
tmux_user = "emma"
""",
    )

    with pytest.raises(ObserverError) as error:
        load_config(path)
    assert error.value.code == "invalid_configuration"


def test_tmux_user_rejects_unsafe_identifier(tmp_path: Path):
    path = _write_config(
        tmp_path / "config.toml",
        """
[hosts.emma]
transport = "local"
tmux_user = "emma;id"
""",
    )

    with pytest.raises(ObserverError) as error:
        load_config(path)
    assert error.value.code == "invalid_configuration"

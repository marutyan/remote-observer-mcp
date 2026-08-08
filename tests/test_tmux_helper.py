from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _helper_module():
    try:
        return importlib.import_module("remote_observer_mcp.tmux_helper")
    except ModuleNotFoundError:
        pytest.fail("tmux helper module is not implemented")


def test_helper_builds_only_fixed_read_commands():
    helper = _helper_module()

    assert helper.build_tmux_command(["sessions"]) == (
        "/usr/bin/tmux",
        "list-sessions",
        "-F",
        "#{session_name}\t#{session_windows}\t#{session_attached}",
    )
    assert helper.build_tmux_command(["windows", "work"]) == (
        "/usr/bin/tmux",
        "list-windows",
        "-t",
        "work",
        "-F",
        "#{window_id}\t#{window_index}\t#{window_name}\t#{window_active}",
    )
    assert helper.build_tmux_command(["panes", "@12"]) == (
        "/usr/bin/tmux",
        "list-panes",
        "-t",
        "@12",
        "-F",
        "#{pane_id}\t#{pane_index}\t#{pane_active}\t#{pane_current_command}",
    )
    assert helper.build_tmux_command(["capture", "%3", "125"]) == (
        "/usr/bin/tmux",
        "capture-pane",
        "-p",
        "-t",
        "%3",
        "-S",
        "-125",
    )


@pytest.mark.parametrize(
    "args",
    [
        ["send-keys", "%1", "id"],
        ["run-shell", "id"],
        ["new-session", "work"],
        ["kill-session", "work"],
        ["set-option", "status", "off"],
        ["sessions", "extra"],
        ["windows"],
        ["windows", "bad;name"],
        ["panes", "%1"],
        ["panes", "bad;name"],
        ["capture", "bad", "10"],
        ["capture", "%1", "0"],
        ["capture", "%1", "501"],
        ["capture", "%1", "not-a-number"],
        ["capture", "%1", "10", "extra"],
    ],
)
def test_helper_rejects_mutating_or_malformed_arguments(args: list[str]):
    helper = _helper_module()

    with pytest.raises(ValueError):
        helper.build_tmux_command(args)


def test_helper_environment_is_minimal_and_drops_tmux_influence(monkeypatch):
    helper = _helper_module()
    monkeypatch.setenv("TMUX", "/tmp/attacker")
    monkeypatch.setenv("TMUX_PANE", "%999")
    monkeypatch.setenv("TMUX_TMPDIR", "/tmp/attacker")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-propagate")

    env = helper.build_clean_environment()

    assert "TMUX" not in env
    assert "TMUX_PANE" not in env
    assert "TMUX_TMPDIR" not in env
    assert "UNRELATED_SECRET" not in env
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == os.path.expanduser("~")
    assert set(env) == {"HOME", "USER", "LOGNAME", "PATH", "LANG"}


def test_deploy_helper_uses_isolated_python_entrypoint_for_installed_package():
    script = _ROOT / "deploy" / "remote-observer-tmux-read"
    assert script.exists(), "deployment helper is not implemented"

    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/opt/remote-observer-mcp/.venv/bin/python -I\n")
    assert "remote_observer_mcp.tmux_helper" in text
    assert "sh " not in text
    assert "bash" not in text
    assert "eval" not in text

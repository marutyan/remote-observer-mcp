from __future__ import annotations

import os
import pwd
import re
import sys
from collections.abc import Sequence

_TMUX = "/usr/bin/tmux"
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_WINDOW_RE = re.compile(r"^@[0-9]{1,10}$")
_PANE_RE = re.compile(r"^%[0-9]{1,10}$")

_SESSION_FORMAT = "#{session_name}\t#{session_windows}\t#{session_attached}"
_WINDOW_FORMAT = "#{window_id}\t#{window_index}\t#{window_name}\t#{window_active}"
_PANE_FORMAT = "#{pane_id}\t#{pane_index}\t#{pane_active}\t#{pane_current_command}"


def _require_session(value: str) -> str:
    if not _SESSION_RE.fullmatch(value):
        raise ValueError("invalid session")
    return value


def _require_window_or_session(value: str) -> str:
    if not (_SESSION_RE.fullmatch(value) or _WINDOW_RE.fullmatch(value)):
        raise ValueError("invalid pane-list target")
    return value


def _require_pane(value: str) -> str:
    if not _PANE_RE.fullmatch(value):
        raise ValueError("invalid pane")
    return value


def _require_lines(value: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError("invalid line count")
    lines = int(value)
    if not 1 <= lines <= 500:
        raise ValueError("line count outside allowed range")
    return lines


def build_tmux_command(args: Sequence[str]) -> tuple[str, ...]:
    """Translate one semantic read operation into a fixed tmux argv."""
    if not all(isinstance(value, str) for value in args):
        raise ValueError("invalid helper arguments")

    if list(args) == ["sessions"]:
        return (_TMUX, "list-sessions", "-F", _SESSION_FORMAT)

    if len(args) == 2 and args[0] == "windows":
        session = _require_session(args[1])
        return (_TMUX, "list-windows", "-t", session, "-F", _WINDOW_FORMAT)

    if len(args) == 2 and args[0] == "panes":
        target = _require_window_or_session(args[1])
        return (_TMUX, "list-panes", "-t", target, "-F", _PANE_FORMAT)

    if len(args) == 3 and args[0] == "capture":
        pane = _require_pane(args[1])
        lines = _require_lines(args[2])
        return (_TMUX, "capture-pane", "-p", "-t", pane, "-S", f"-{lines}")

    raise ValueError("unsupported helper operation")


def build_clean_environment() -> dict[str, str]:
    """Build a small environment from the effective uid, not inherited process state."""
    identity = pwd.getpwuid(os.getuid())
    return {
        "HOME": identity.pw_dir,
        "USER": identity.pw_name,
        "LOGNAME": identity.pw_name,
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        command = build_tmux_command(args)
    except ValueError:
        print("invalid tmux helper arguments", file=sys.stderr)
        return 64

    try:
        os.execve(command[0], command, build_clean_environment())
    except OSError:
        print("tmux execution failed", file=sys.stderr)
        return 126
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

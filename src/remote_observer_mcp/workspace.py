from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from remote_observer_mcp.config import WorkspaceConfig
from remote_observer_mcp.errors import ObserverError

_DEFAULT_SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    "id_rsa*",
    "**/id_rsa*",
    "credentials*",
    "**/credentials*",
    "secrets*",
    "**/secrets*",
)


def normalize_relative_path(value: str) -> str:
    """Normalize a model-provided POSIX relative path without allowing root escape."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ObserverError("invalid_path", "invalid relative path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ObserverError("invalid_path", "invalid relative path")

    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ObserverError("invalid_path", "invalid relative path")

    normalized = str(path)
    if not normalized:
        raise ObserverError("invalid_path", "invalid relative path")
    return normalized


def ensure_visible_relative_path(workspace: WorkspaceConfig, value: str) -> str:
    """Return a safe relative path or fail before sensitive content can be collected."""
    normalized = normalize_relative_path(value)
    patterns = tuple(
        dict.fromkeys((*_DEFAULT_SECRET_PATTERNS, *workspace.secret_patterns))
    )
    if _matches_sensitive_path(normalized, patterns):
        raise ObserverError("sensitive_path", "sensitive path is not accessible")
    return normalized


def is_visible_relative_path(workspace: WorkspaceConfig, value: str) -> bool:
    try:
        ensure_visible_relative_path(workspace, value)
    except ObserverError:
        return False
    return True


def _matches_sensitive_path(path: str, patterns: tuple[str, ...]) -> bool:
    candidates = (path, path.lstrip("./"))
    return any(
        fnmatch.fnmatchcase(candidate, pattern)
        for candidate in candidates
        for pattern in patterns
    )

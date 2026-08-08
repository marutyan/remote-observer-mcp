from __future__ import annotations

import re

from remote_observer_mcp.errors import ObserverError

_SECRET_PATTERNS = (
    re.compile(r"authorization\s*:\s*bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?:password|passwd|secret|token|api[_-]?key)\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
)


def reject_secret_literals(values: tuple[str, ...]) -> None:
    """Reject likely literal credentials without reflecting matched content."""
    for value in values:
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise ObserverError(
                "secret_in_execution_request",
                "execution request contains secret-like literal content",
            )

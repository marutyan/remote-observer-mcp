from __future__ import annotations

import re
from dataclasses import dataclass

from remote_observer_mcp.models import HARD_OUTPUT_BYTES

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)(Authorization:\s*Bearer\s+)([^\s]+)")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")
_GENERIC_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret)\s*([:=])\s*([^\s,;]+)"
)


@dataclass(frozen=True, slots=True)
class SanitizedOutput:
    text: str
    truncated: bool
    redacted: bool


@dataclass(frozen=True, slots=True)
class SanitizedStreams:
    stdout: str
    stderr: str
    truncated: bool
    redacted: bool


def sanitize_output(text: str, max_bytes: int = HARD_OUTPUT_BYTES) -> SanitizedOutput:
    _validate_output_limit(max_bytes)
    redacted_text, redacted = _redact(text)
    bounded_text, truncated = _truncate_utf8(redacted_text, max_bytes)
    return SanitizedOutput(text=bounded_text, truncated=truncated, redacted=redacted)


def sanitize_streams(
    stdout: str,
    stderr: str,
    *,
    max_bytes: int,
    source_truncated: bool = False,
) -> SanitizedStreams:
    """Redact both streams and keep their combined encoded size within max_bytes."""
    _validate_output_limit(max_bytes)
    safe_stdout, stdout_redacted = _redact(stdout)
    safe_stderr, stderr_redacted = _redact(stderr)

    stdout_bytes = safe_stdout.encode("utf-8")
    stderr_bytes = safe_stderr.encode("utf-8")
    if len(stdout_bytes) + len(stderr_bytes) <= max_bytes:
        return SanitizedStreams(
            stdout=safe_stdout,
            stderr=safe_stderr,
            truncated=source_truncated,
            redacted=stdout_redacted or stderr_redacted,
        )

    stderr_budget = min(len(stderr_bytes), max_bytes // 4)
    stdout_budget = min(len(stdout_bytes), max_bytes - stderr_budget)
    remaining = max_bytes - stdout_budget - stderr_budget
    if remaining:
        extra_stderr = min(len(stderr_bytes) - stderr_budget, remaining)
        stderr_budget += extra_stderr
        remaining -= extra_stderr
    if remaining:
        stdout_budget += min(len(stdout_bytes) - stdout_budget, remaining)

    bounded_stdout, stdout_cut = _truncate_utf8(safe_stdout, stdout_budget)
    bounded_stderr, stderr_cut = _truncate_utf8(safe_stderr, stderr_budget)
    return SanitizedStreams(
        stdout=bounded_stdout,
        stderr=bounded_stderr,
        truncated=source_truncated or stdout_cut or stderr_cut,
        redacted=stdout_redacted or stderr_redacted,
    )


def _redact(text: str) -> tuple[str, bool]:
    value = text
    value = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", value)
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    value = _OPENAI_KEY_RE.sub("[REDACTED]", value)
    value = _GITHUB_TOKEN_RE.sub("[REDACTED]", value)
    value = _GENERIC_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    return value, value != text


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _validate_output_limit(max_bytes: int) -> None:
    if not 0 < max_bytes <= HARD_OUTPUT_BYTES:
        raise ValueError("output limit exceeds the hard limit")

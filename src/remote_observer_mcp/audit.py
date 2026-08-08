from __future__ import annotations

import inspect
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from remote_observer_mcp.errors import ObserverError

_LOGICAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    timestamp: str
    tool: str
    host_id: str | None
    resource_id: str | None
    duration_ms: int
    outcome: str
    truncated: bool
    redacted: bool


async def run_observed_tool(
    *,
    tool: str,
    host_id: str | None,
    resource_id: str | None,
    operation: Callable[[], Awaitable[Any] | Any],
) -> dict[str, Any]:
    """Run a semantic tool operation and emit only sanitized metadata to stderr."""
    started = time.monotonic()
    data: Any = None
    try:
        maybe_awaitable = operation()
        data = await maybe_awaitable if inspect.isawaitable(maybe_awaitable) else maybe_awaitable
    except ObserverError as error:
        _emit_for_call(
            started=started,
            tool=tool,
            host_id=host_id,
            resource_id=resource_id,
            outcome=error.code,
            data=None,
        )
        return {
            "ok": False,
            "error": {"code": error.code, "message": error.message},
        }
    except Exception:
        _emit_for_call(
            started=started,
            tool=tool,
            host_id=host_id,
            resource_id=resource_id,
            outcome="internal_error",
            data=None,
        )
        raise

    _emit_for_call(
        started=started,
        tool=tool,
        host_id=host_id,
        resource_id=resource_id,
        outcome="success",
        data=data,
    )
    return {"ok": True, "data": data}


def _emit_for_call(
    *,
    started: float,
    tool: str,
    host_id: str | None,
    resource_id: str | None,
    outcome: str,
    data: Any,
) -> None:
    _emit(
        AuditEvent(
            timestamp=datetime.now(UTC).isoformat(),
            tool=_safe_tool_name(tool),
            host_id=_safe_logical_id(host_id),
            resource_id=_safe_logical_id(resource_id),
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            outcome=outcome,
            truncated=_contains_true_flag(data, "truncated"),
            redacted=_contains_true_flag(data, "redacted"),
        )
    )


def _contains_true_flag(value: Any, flag: str) -> bool:
    if isinstance(value, dict):
        if value.get(flag) is True:
            return True
        return any(_contains_true_flag(item, flag) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_true_flag(item, flag) for item in value)
    return False


def _safe_logical_id(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _LOGICAL_ID_RE.fullmatch(value) else "<invalid>"


def _safe_tool_name(value: str) -> str:
    return value if _LOGICAL_ID_RE.fullmatch(value) else "<invalid>"


def _emit(event: AuditEvent) -> None:
    print(
        json.dumps(asdict(event), sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )

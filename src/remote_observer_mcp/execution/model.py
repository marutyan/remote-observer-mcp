from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.execution.secrets import reject_secret_literals

_REQUEST_ID_RE = re.compile(r"^exec-[0-9]{8}-[0-9]{4}$")
_LOGICAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ALLOWED_RISKS = {"R1", "R2", "R3", "R4"}
_ALLOWED_MODES = {"argv", "shell"}
_MAX_SCRIPT_BYTES = 32 * 1024
_MAX_REASON_CHARS = 1024
_MAX_ARGV_ITEMS = 256
_MAX_ARG_CHARS = 8192
_ROOT_KEYS = {
    "schema_version",
    "request_id",
    "target",
    "mode",
    "argv",
    "script",
    "timeout_seconds",
    "risk",
    "reason",
}
_TARGET_KEYS = {"host", "workspace"}


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    host: str
    workspace: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    schema_version: int
    request_id: str
    target: ExecutionTarget
    mode: str
    argv: tuple[str, ...] | None
    script: str | None
    timeout_seconds: int
    risk: str
    reason: str

    @classmethod
    def from_json_bytes(cls, data: bytes) -> ExecutionRequest:
        raw = _decode_json_object(data)
        _ensure_exact_keys(raw, _ROOT_KEYS, optional={"argv", "script"})

        if raw.get("schema_version") != 1:
            _invalid()

        request_id = _required_string(raw.get("request_id"))
        if not _REQUEST_ID_RE.fullmatch(request_id):
            _invalid()

        target = _parse_target(raw.get("target"))
        mode = _required_string(raw.get("mode"))
        if mode not in _ALLOWED_MODES:
            _invalid()

        timeout = raw.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 900:
            _invalid()

        risk = _required_string(raw.get("risk"))
        if risk not in _ALLOWED_RISKS:
            _invalid()

        reason = _required_string(raw.get("reason"))
        if len(reason) > _MAX_REASON_CHARS or _has_forbidden_control(reason, allow_lines=False):
            _invalid()

        argv: tuple[str, ...] | None = None
        script: str | None = None
        if mode == "argv":
            if "script" in raw:
                _invalid()
            argv = _parse_argv(raw.get("argv"))
        else:
            if "argv" in raw or risk != "R4":
                _invalid()
            script = _parse_script(raw.get("script"))

        secret_values = tuple(argv or ()) + ((script,) if script is not None else ()) + (reason,)
        reject_secret_literals(secret_values)

        return cls(
            schema_version=1,
            request_id=request_id,
            target=target,
            mode=mode,
            argv=argv,
            script=script,
            timeout_seconds=timeout,
            risk=risk,
            reason=reason,
        )


def request_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode_json_object(data: bytes) -> dict[str, Any]:
    try:
        decoded = data.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid()
    if not isinstance(value, dict):
        _invalid()
    return value


def _parse_target(value: Any) -> ExecutionTarget:
    if not isinstance(value, dict):
        _invalid()
    _ensure_exact_keys(value, _TARGET_KEYS, optional={"workspace"})

    host = _required_string(value.get("host"))
    if not _LOGICAL_ID_RE.fullmatch(host):
        _invalid()

    workspace_value = value.get("workspace")
    workspace: str | None = None
    if workspace_value is not None:
        workspace = _required_string(workspace_value)
        if not _LOGICAL_ID_RE.fullmatch(workspace):
            _invalid()
    return ExecutionTarget(host=host, workspace=workspace)


def _parse_argv(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_ARGV_ITEMS:
        _invalid()
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > _MAX_ARG_CHARS
            or _has_forbidden_control(item, allow_lines=False)
        ):
            _invalid()
        result.append(item)
    return tuple(result)


def _parse_script(value: Any) -> str:
    script = _required_string(value)
    if len(script.encode("utf-8")) > _MAX_SCRIPT_BYTES:
        _invalid()
    if _has_forbidden_control(script, allow_lines=True):
        _invalid()
    return script


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        _invalid()
    return value


def _has_forbidden_control(value: str, *, allow_lines: bool) -> bool:
    allowed = {9, 10, 13} if allow_lines else set()
    return any((ord(character) < 32 and ord(character) not in allowed) or ord(character) == 127 for character in value)


def _ensure_exact_keys(
    raw: dict[str, Any],
    allowed: set[str],
    *,
    optional: set[str],
) -> None:
    if set(raw) - allowed:
        _invalid()
    required = allowed - optional
    if not required.issubset(raw):
        _invalid()


def _invalid() -> None:
    raise ObserverError("invalid_execution_request", "invalid execution request")

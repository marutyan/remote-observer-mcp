from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from remote_observer_mcp.config import AppConfig, HostConfig, load_config
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.execution.model import ExecutionRequest, request_digest
from remote_observer_mcp.models import HARD_OUTPUT_BYTES
from remote_observer_mcp.policy import sanitize_streams

_REQUEST_ID_RE = re.compile(r"^exec-[0-9]{8}-[0-9]{4}$")
_READ_CHUNK_BYTES = 8192


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    request_id: str
    digest: str
    target_host: str
    target_workspace: str | None
    mode: str
    risk: str
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str
    truncated: bool
    redacted: bool


async def execute_request(
    config: AppConfig,
    request: ExecutionRequest,
    digest: str,
) -> ExecutionEvidence:
    host = config.host(request.target.host)
    cwd: str | None = None
    if request.target.workspace is not None:
        workspace = config.workspace(request.target.workspace)
        if workspace.host_id != host.host_id:
            raise ObserverError(
                "invalid_execution_target",
                "execution workspace does not belong to target host",
            )
        cwd = workspace.root

    command = _request_argv(request)
    if host.transport == "local":
        argv = command
        local_cwd = cwd
    else:
        argv = _ssh_argv(host, command, cwd=cwd)
        local_cwd = None

    result = await _run_process(
        argv,
        cwd=local_cwd,
        timeout_seconds=request.timeout_seconds,
    )
    if host.transport == "ssh" and result[0] == 255:
        raise ObserverError("connection_failed", "SSH execution connection failed")

    exit_code, stdout, stderr, duration_ms, truncated, redacted = result
    return ExecutionEvidence(
        request_id=request.request_id,
        digest=digest,
        target_host=host.host_id,
        target_workspace=request.target.workspace,
        mode=request.mode,
        risk=request.risk,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
        redacted=redacted,
    )


def _request_argv(request: ExecutionRequest) -> tuple[str, ...]:
    if request.mode == "argv":
        assert request.argv is not None
        return request.argv
    if request.risk != "R4" or request.script is None:
        raise ObserverError("invalid_execution_request", "invalid execution request")
    return ("bash", "-lc", request.script)


def _ssh_argv(
    host: HostConfig,
    command: tuple[str, ...],
    *,
    cwd: str | None,
) -> tuple[str, ...]:
    if not host.ssh_alias:
        raise ObserverError("invalid_execution_target", "SSH target is not configured")
    remote = shlex.join(command)
    if cwd is not None:
        remote = f"cd -- {shlex.quote(cwd)} && exec {remote}"
    return (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
        host.ssh_alias,
        remote,
    )


async def _run_process(
    argv: tuple[str, ...],
    *,
    cwd: str | None,
    timeout_seconds: int,
) -> tuple[int, str, str, int, bool, bool]:
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise ObserverError("unsupported_capability", "required executable is unavailable") from error
    except PermissionError as error:
        raise ObserverError("permission_denied", "execution permission denied") from error
    except OSError as error:
        raise ObserverError("command_failed", "failed to start approved execution") from error

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(process.stdout))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr))
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError as error:
        process.kill()
        await process.wait()
        await stdout_task
        await stderr_task
        raise ObserverError("timeout", "approved execution timed out") from error

    stdout_bytes, stdout_truncated = await stdout_task
    stderr_bytes, stderr_truncated = await stderr_task
    sanitized = sanitize_streams(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        max_bytes=HARD_OUTPUT_BYTES,
        source_truncated=stdout_truncated or stderr_truncated,
    )
    return (
        process.returncode,
        sanitized.stdout,
        sanitized.stderr,
        int((time.monotonic() - started) * 1000),
        sanitized.truncated,
        sanitized.redacted,
    )


async def _read_bounded(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
    retained = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        remaining = HARD_OUTPUT_BYTES - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    return bytes(retained), truncated


def _config_path() -> Path:
    value = os.environ.get("REMOTE_OBSERVER_CONFIG")
    if value:
        return Path(value).expanduser()
    return Path.home() / ".config" / "remote-observer-mcp" / "config.toml"


def _request_path(request_id: str) -> Path:
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise ObserverError("invalid_execution_request", "invalid execution request")
    root = Path(os.environ.get("REMOTE_OBSERVER_REQUESTS", "execution_requests"))
    return root / f"{request_id}.json"


def _evidence_json(evidence: ExecutionEvidence) -> str:
    return json.dumps(asdict(evidence), sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run an externally approved execution request")
    parser.add_argument("request_id")
    args = parser.parse_args(argv)

    try:
        path = _request_path(args.request_id)
        raw = path.read_bytes()
        request = ExecutionRequest.from_json_bytes(raw)
        if request.request_id != args.request_id:
            raise ObserverError("invalid_execution_request", "invalid execution request")
        config = load_config(_config_path())
        evidence = asyncio.run(execute_request(config, request, request_digest(raw)))
    except (OSError, ObserverError) as error:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": error.code if isinstance(error, ObserverError) else "request_read_failed",
                "message": error.message if isinstance(error, ObserverError) else "cannot read execution request",
            },
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        raise SystemExit(1) from error

    print(_evidence_json(evidence))
    if evidence.exit_code != 0:
        raise SystemExit(evidence.exit_code)

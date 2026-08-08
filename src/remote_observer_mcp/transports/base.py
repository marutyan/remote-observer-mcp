from __future__ import annotations

import asyncio
import time
from typing import Protocol

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.policy import sanitize_streams

_READ_CHUNK_BYTES = 8192


class Transport(Protocol):
    async def run(self, command: CommandSpec) -> CommandResult: ...


async def run_process(
    command: CommandSpec,
    *,
    missing_code: str = "unsupported_capability",
) -> CommandResult:
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise ObserverError(missing_code, "required executable is unavailable") from error
    except PermissionError as error:
        raise ObserverError("permission_denied", "executable permission denied") from error
    except OSError as error:
        raise ObserverError("command_failed", "failed to start command") from error

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(process.stdout, command.max_output_bytes))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, command.max_output_bytes))

    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=command.timeout_seconds)
    except TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()

    stdout_bytes, stdout_truncated = await stdout_task
    stderr_bytes, stderr_truncated = await stderr_task
    if timed_out:
        raise ObserverError("timeout", "command timed out")

    sanitized = sanitize_streams(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        max_bytes=command.max_output_bytes,
        source_truncated=stdout_truncated or stderr_truncated,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        exit_code=process.returncode,
        stdout=sanitized.stdout,
        stderr=sanitized.stderr,
        duration_ms=duration_ms,
        truncated=sanitized.truncated,
        redacted=sanitized.redacted,
    )


async def _read_bounded(
    stream: asyncio.StreamReader,
    max_bytes: int,
) -> tuple[bytes, bool]:
    retained = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        remaining = max_bytes - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    return bytes(retained), truncated

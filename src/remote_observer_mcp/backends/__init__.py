from __future__ import annotations

from dataclasses import dataclass

from remote_observer_mcp.backends.catalog import CATALOG
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandSpec
from remote_observer_mcp.transports.base import Transport


@dataclass(frozen=True, slots=True)
class BackendChoice:
    capability: str
    executable: str | None
    variant: str


async def resolve_backend(transport: Transport, capability: str) -> BackendChoice:
    """Resolve a package-owned capability to the first available safe backend."""
    candidates = CATALOG.get(capability)
    if candidates is None:
        raise ObserverError("unsupported_capability", "unsupported backend capability")

    for candidate in candidates:
        if candidate.executable is None:
            return BackendChoice(capability, None, candidate.variant)
        result = await transport.run(
            CommandSpec(
                argv=(
                    "sh",
                    "-c",
                    f"command -v {candidate.executable} >/dev/null 2>&1",
                ),
                timeout_seconds=5,
                max_output_bytes=1024,
            )
        )
        if result.exit_code == 0:
            return BackendChoice(capability, candidate.executable, candidate.variant)

    raise ObserverError("unsupported_capability", "no safe backend is available")

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackendCandidate:
    executable: str | None
    variant: str


CATALOG: dict[str, tuple[BackendCandidate, ...]] = {
    "search": (BackendCandidate("rg", "rg"), BackendCandidate("grep", "grep")),
    "find": (BackendCandidate("fd", "fd"), BackendCandidate("find", "find")),
    "tree": (BackendCandidate("eza", "eza"), BackendCandidate("find", "find")),
    "disk_hotspots": (
        BackendCandidate("dust", "dust"),
        BackendCandidate("du", "du"),
    ),
    "process_list": (
        BackendCandidate("procs", "procs"),
        BackendCandidate("ps", "ps"),
    ),
    "checksum": (
        BackendCandidate("b3sum", "b3sum"),
        BackendCandidate("sha256sum", "sha256sum"),
        BackendCandidate("shasum", "shasum"),
    ),
    "json": (BackendCandidate(None, "python-native"),),
    "structured": (
        BackendCandidate("yq", "yq"),
        BackendCandidate(None, "python-native"),
    ),
}

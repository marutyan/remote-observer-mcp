from collections import deque

import pytest
from remote_observer_mcp.backends import BackendChoice, resolve_backend

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec


class FakeTransport:
    def __init__(self, exit_codes: list[int]):
        self.exit_codes = deque(exit_codes)
        self.commands: list[CommandSpec] = []

    async def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        return CommandResult(self.exit_codes.popleft(), "", "", 1, False, False)


@pytest.mark.asyncio
async def test_search_prefers_rg_then_falls_back_to_grep():
    preferred = FakeTransport([0])
    assert await resolve_backend(preferred, "search") == BackendChoice("search", "rg", "rg")
    assert preferred.commands[0].argv == (
        "sh",
        "-c",
        "command -v rg >/dev/null 2>&1",
    )

    fallback = FakeTransport([1, 0])
    assert await resolve_backend(fallback, "search") == BackendChoice("search", "grep", "grep")
    assert [command.argv[2] for command in fallback.commands] == [
        "command -v rg >/dev/null 2>&1",
        "command -v grep >/dev/null 2>&1",
    ]


@pytest.mark.asyncio
async def test_catalog_has_expected_preferred_fallback_chains():
    cases = {
        "find": ([1, 0], "find"),
        "tree": ([1, 0], "find"),
        "disk_hotspots": ([1, 0], "du"),
        "process_list": ([1, 0], "ps"),
        "checksum": ([1, 1, 0], "shasum"),
    }
    for capability, (codes, expected) in cases.items():
        transport = FakeTransport(codes)
        choice = await resolve_backend(transport, capability)
        assert choice.executable == expected


@pytest.mark.asyncio
async def test_native_fallbacks_need_no_executable_probe():
    transport = FakeTransport([])
    assert await resolve_backend(transport, "json") == BackendChoice(
        "json", None, "python-native"
    )
    assert transport.commands == []


@pytest.mark.asyncio
async def test_unknown_capability_fails_closed_without_echo():
    with pytest.raises(ObserverError) as error:
        await resolve_backend(FakeTransport([]), "bad\nsecret-marker")

    assert error.value.code == "unsupported_capability"
    assert "secret-marker" not in error.value.message


def test_backend_choice_rejects_model_selected_executable_surface():
    fields = set(BackendChoice.__dataclass_fields__)
    assert fields == {"capability", "executable", "variant"}

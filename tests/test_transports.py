import sys

import pytest

from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.models import CommandResult, CommandSpec
from remote_observer_mcp.transports.local import LocalTransport
from remote_observer_mcp.transports.ssh import SshTransport


@pytest.mark.asyncio
async def test_local_transport_captures_success_and_nonzero_exit():
    transport = LocalTransport()

    success = await transport.run(
        CommandSpec(argv=(sys.executable, "-c", "print('ok')"), timeout_seconds=2)
    )
    failure = await transport.run(
        CommandSpec(
            argv=(sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"),
            timeout_seconds=2,
        )
    )

    assert success.exit_code == 0
    assert success.stdout.strip() == "ok"
    assert failure.exit_code == 3
    assert failure.stderr.strip() == "bad"


@pytest.mark.asyncio
async def test_local_transport_times_out_and_kills_process():
    transport = LocalTransport()

    with pytest.raises(ObserverError) as error:
        await transport.run(
            CommandSpec(
                argv=(sys.executable, "-c", "import time; time.sleep(5)"),
                timeout_seconds=0.05,
            )
        )

    assert error.value.code == "timeout"


@pytest.mark.asyncio
async def test_local_transport_bounds_retained_output():
    result = await LocalTransport().run(
        CommandSpec(
            argv=(sys.executable, "-c", "print('x' * 200000)"),
            timeout_seconds=2,
            max_output_bytes=1024,
        )
    )

    assert len((result.stdout + result.stderr).encode("utf-8")) <= 1024
    assert result.truncated is True


def test_ssh_transport_uses_strict_options_and_quotes_remote_tokens():
    transport = SshTransport(alias="emma", connect_timeout_seconds=5)
    command = CommandSpec(argv=("printf", "%s", "$(id)"), timeout_seconds=2)

    argv = transport.build_argv(command)

    assert argv[:7] == (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
    )
    assert argv[7] == "emma"
    assert argv[8] == "printf %s '$(id)'"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected_code"),
    [
        ("Host key verification failed.", "host_key_failure"),
        ("Permission denied (publickey).", "authentication_failed"),
        ("Connection timed out", "connection_failed"),
    ],
)
async def test_ssh_transport_normalizes_connection_failures(
    monkeypatch, stderr: str, expected_code: str
):
    async def fake_run_process(*args, **kwargs):
        return CommandResult(
            exit_code=255,
            stdout="",
            stderr=stderr,
            duration_ms=1,
            truncated=False,
            redacted=False,
        )

    monkeypatch.setattr("remote_observer_mcp.transports.ssh.run_process", fake_run_process)

    with pytest.raises(ObserverError) as error:
        await SshTransport(alias="emma").run(CommandSpec(argv=("true",)))

    assert error.value.code == expected_code

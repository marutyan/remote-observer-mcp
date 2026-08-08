import pytest
from remote_observer_mcp.models import CommandSpec
from remote_observer_mcp.policy import HARD_OUTPUT_BYTES, sanitize_output


def test_sanitize_output_redacts_secret_before_truncation():
    raw = "Authorization: Bearer sk-test-secret\n" + ("x" * 1000)

    result = sanitize_output(raw, max_bytes=128)

    assert "sk-test-secret" not in result.text
    assert "[REDACTED]" in result.text
    assert len(result.text.encode("utf-8")) <= 128
    assert result.truncated is True
    assert result.redacted is True


def test_sanitize_output_keeps_valid_utf8_boundary():
    result = sanitize_output("あ" * 100, max_bytes=10)

    assert len(result.text.encode("utf-8")) <= 10
    assert result.truncated is True
    result.text.encode("utf-8").decode("utf-8")


@pytest.mark.parametrize(
    ("timeout_seconds", "max_output_bytes"),
    [(0, 1024), (16, 1024), (1, 0), (1, HARD_OUTPUT_BYTES + 1)],
)
def test_command_spec_rejects_values_above_hard_caps(timeout_seconds, max_output_bytes):
    with pytest.raises(ValueError):
        CommandSpec(
            argv=("true",),
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

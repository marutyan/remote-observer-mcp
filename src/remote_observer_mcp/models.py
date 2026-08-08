from dataclasses import dataclass

HARD_TIMEOUT_SECONDS = 15.0
HARD_OUTPUT_BYTES = 128 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = HARD_OUTPUT_BYTES

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0]:
            raise ValueError("command argv must contain an executable")
        if any(not isinstance(token, str) or "\x00" in token for token in self.argv):
            raise ValueError("command argv contains an invalid token")
        if not 0 < self.timeout_seconds <= HARD_TIMEOUT_SECONDS:
            raise ValueError("command timeout exceeds the hard limit")
        if not 0 < self.max_output_bytes <= HARD_OUTPUT_BYTES:
            raise ValueError("command output limit exceeds the hard limit")


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool
    redacted: bool

# remote-observer-mcp

Read-only MCP server for observing registered local and SSH-accessible hosts from ChatGPT without exposing arbitrary shell execution.

The project is under active implementation. The architecture and security baseline are documented under `docs/superpowers/specs/`, and the implementation sequence is under `docs/superpowers/plans/`.

## Development

Requirements: Python 3.12.

```bash
python -m pip install -e '.[dev]'
python -m ruff check .
python -m pytest -q
```

The deterministic test suite must not require real SSH hosts, credentials, or Secure MCP Tunnel access.

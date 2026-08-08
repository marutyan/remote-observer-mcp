# remote-observer-mcp

`remote-observer-mcp` is a read-only MCP server for observing explicitly registered local and SSH-accessible hosts from ChatGPT without exposing arbitrary shell execution, hostnames, or filesystem paths.

The intended production path is:

```text
ChatGPT
  -> OpenAI Secure MCP Tunnel
  -> tunnel-client on a gateway
  -> remote-observer-mcp (stdio child)
  -> local or strict SSH transport
  -> allowlisted hosts/resources
```

The architecture/security baseline is in `docs/superpowers/specs/`. The implementation sequence is in `docs/superpowers/plans/`. Gateway deployment is documented in `deploy/README.md`, while all remaining credential/account/host operations are batched in `USER_ACTIONS.md`.

## Safety boundary

The public MCP surface is semantic and read-only. It intentionally does **not** provide:

- arbitrary `execute`, shell, or SSH command tools;
- arbitrary hostname/IP or filesystem-path arguments;
- arbitrary file reads;
- service/container/Git/GPU mutation;
- environment-variable or Docker-inspect access;
- package installation or privilege elevation.

Hosts and resources are referenced by logical IDs from a local TOML registry. Unknown or unsafe identifiers fail closed before transport execution. Local processes use `asyncio.create_subprocess_exec`; SSH fixes `BatchMode=yes`, `StrictHostKeyChecking=yes`, and a bounded connect timeout, then POSIX-quotes validated remote argv tokens.

Read-only access is **not** equivalent to secret-free access. Service/container logs and Git diffs can contain sensitive data. Collection is allowlisted and bounded; dangerous sources are excluded where possible; output passes best-effort redaction before it crosses the MCP boundary. Metadata-only audit events go to stderr and never store raw observed output.

## Tools

Core:

- `list_hosts`
- `host_overview`
- `system_status`
- `disk_usage`
- `process_status`

Registered services:

- `service_status`
- `service_logs`

Registered Docker containers:

- `container_list`
- `container_logs`

Registered Git repositories:

- `repo_status`
- `repo_diff`
- `repo_log`

NVIDIA-capable hosts:

- `gpu_status`

Every tool is annotated read-only, non-destructive, idempotent, and closed-world. Log tools require per-resource opt-in. Git diff collection excludes common sensitive paths plus configured validated patterns before output is returned.

## Runtime configuration

Runtime configuration is stored outside the repository, normally at:

```text
~/.config/remote-observer-mcp/config.toml
```

or the path selected by `REMOTE_OBSERVER_CONFIG`.

Start from `config.example.toml`. Do not store passwords, API keys, SSH private keys, or other credentials in this TOML file.

Example shape:

```toml
[hosts.gateway]
transport = "local"

[hosts.remote]
transport = "ssh"
ssh_alias = "example-host"
gpu = true

[hosts.remote.services.app]
unit = "app.service"
logs = false

[hosts.remote.repos.app]
path = "/srv/app"

[hosts.remote.containers.api]
name = "api"
logs = false

[hosts.remote.processes.worker]
name = "worker"
```

The repository/path/name grammars are deliberately narrow in v1. A valid but unsupported local naming convention should be added through a reviewed config-schema change rather than bypassing validation.

## Development

Requirements: Python 3.12.

```bash
python -m pip install -e '.[dev]'
python -m ruff check .
python -m pytest -q
python scripts/smoke_stdio.py
```

The deterministic suite does not require real SSH hosts, credentials, Docker, systemd, NVIDIA hardware, or Secure MCP Tunnel access. Observer tests use fake transports. `scripts/smoke_stdio.py` performs a real MCP stdio initialize/list-tools/call exchange against a temporary local config.

## Run as a stdio MCP server

```bash
export REMOTE_OBSERVER_CONFIG="$HOME/.config/remote-observer-mcp/config.toml"
remote-observer-mcp
```

This command expects an MCP client on stdin/stdout. Running it detached as a standalone daemon is not useful.

For production, `tunnel-client` should spawn this command as its stdio MCP child. See `deploy/README.md`.

## Gateway deployment

The production supervisor is **`tunnel-client`**, not a second standalone MCP systemd service. The tunnel client owns the stdio child lifecycle and systemd owns the tunnel-client process.

Deployment proceeds in three stages:

1. local/manual deterministic MCP smoke;
2. optional short gateway/tmux Tunnel smoke;
3. managed gateway/systemd service.

No real Tunnel traffic is required by the repository test suite. Because avoiding additional spend is a project constraint, `USER_ACTIONS.md` contains an explicit no-extra-cost gate before any account-backed `doctor`/`run` call.

## Supported target assumptions

v1 system/service/Docker/Git/GPU observation is designed primarily for Linux targets. The gateway itself may be a Mac during development, but the Linux system observer rejects unsupported local OS parsing rather than guessing.

The SSH remote command serialization assumes a POSIX-compatible remote login shell. Windows remote targets are not part of v1.

## Validation status semantics

Repository work reports use:

- `PASS`: the stated command/check was run and observed to pass;
- `FAIL`: it was run and failed;
- `NOT RUN`: it was intentionally not executed;
- `BLOCKED`: it could not be executed because a required dependency or user-controlled environment is unavailable.

Real SSH, Tunnel, ChatGPT-connector, and gateway-systemd acceptance remains `NOT RUN` until the steps in `USER_ACTIONS.md` are actually performed. Do not infer production connectivity from the deterministic CI suite.

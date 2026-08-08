# remote-observer-mcp

`remote-observer-mcp` gives ChatGPT a broad **read-only** view of explicitly registered local and SSH-accessible hosts and workspaces without exposing arbitrary shell execution, arbitrary hostnames, or arbitrary filesystem roots.

The repository also contains a separate **Execution Bridge** for exact commands and break-glass shell scripts. That bridge is intentionally not an MCP tool: every execution waits on an independent GitHub Environment approval before a self-hosted runner can invoke `remote-observer-exec`.

## Architecture

```text
ChatGPT
  |
  +-- read lane
  |    -> OpenAI Secure MCP Tunnel
  |    -> tunnel-client on gateway
  |    -> remote-observer-mcp (stdio child)
  |    -> logical host/workspace registry
  |    -> semantic observer
  |    -> preferred CLI / safe fallback
  |    -> local or strict SSH transport
  |    -> bounded + redacted result and metadata-only audit
  |
  +-- execution lane
       -> committed versioned execution request
       -> workflow_dispatch(request_id only)
       -> hosted validation + request digest
       -> GitHub Environment: remote-execution
       -> required reviewer approval
       -> [self-hosted, remote-observer] runner
       -> remote-observer-exec
       -> registered local or SSH target
```

Design and implementation records are under `docs/superpowers/specs/` and `docs/superpowers/plans/`. Gateway deployment is in `deploy/README.md`; execution-runner deployment is in `deploy/execution-runner.md`; all remaining real-account/credential/runner operations are batched in `USER_ACTIONS.md`.

## Safety boundary

The public MCP surface is semantic and read-only. It does **not** provide:

- arbitrary `execute`, `shell`, `ssh`, command, argv, script, hostname, URL, method, or absolute-path tools;
- arbitrary file reads outside registered workspace roots;
- `tmux send-keys` / `run-shell` or session mutation;
- `fd --exec`, AST rewrite/fix, yq in-place writes, Docker exec/inspect, package install/update, archive extraction, or service/process mutation;
- automatic software installation or privilege elevation.

Hosts, workspaces and resources are referenced by logical IDs in a gateway-local TOML registry. Unknown or unsafe identifiers fail closed. Local observer processes use `asyncio.create_subprocess_exec`; SSH uses registered aliases with `BatchMode=yes`, `StrictHostKeyChecking=yes`, and a bounded connection timeout.

Read-only does not mean secret-free. Logs, diffs and workspace files can contain sensitive material. Collection uses allowlists and secret-path denial as the primary boundary. Output is bounded and best-effort redacted before crossing MCP. Metadata-only audit records never store raw observed output or argv.

## Read-only MCP tools

Every tool is annotated read-only, non-destructive, idempotent and closed-world.

### Host discovery and baseline system state

- `list_hosts`
- `host_overview`
- `system_status`
- `disk_usage`
- `process_status`

### Workspaces and files

- `list_workspaces`
- `workspace_find`
- `workspace_search`
- `workspace_read`
- `workspace_tree`
- `file_info`
- `checksum`

Workspace inputs use a logical workspace ID plus validated relative paths. Concrete roots stay in local configuration and are not returned by discovery tools. Common secret paths such as `.env`, private keys and credential files are denied before content collection.

### Code and structured data

- `code_search`
- `json_query`
- `structured_query`

`code_search` uses search-only `ast-grep`; rewrite/fix options are not exposed. Structured selectors use a narrow dot-key / array-index grammar rather than arbitrary jq/yq programs.

### tmux

- `tmux_sessions`
- `tmux_windows`
- `tmux_panes`
- `tmux_capture`

Only list/capture operations exist. `tmux_capture` is bounded; no key injection or tmux mutation is exposed.

### Processes, network, filesystem and hardware

- `process_list`
- `process_tree`
- `network_listeners`
- `network_interfaces`
- `network_routes`
- `dns_lookup`
- `filesystem_status`
- `disk_hotspots`
- `user_sessions`
- `hardware_info`
- `sensor_status`

Process observation avoids environment variables and full command lines by default. Network observation is passive; there is no port scan or arbitrary socket/URL probe.

### systemd and journals

- `service_status`
- `service_logs`
- `service_failures`
- `systemd_timers`
- `journal_query`

Log access is opt-in per registered service. `journal_query` accepts only fixed, bounded time/priority/line filters for registered services.

### Docker and Compose

- `container_list`
- `container_logs`
- `container_stats`
- `compose_status`

Only configured containers are returned. Container logs are opt-in. Compose status is enabled only for workspaces with `compose = true`. No Docker exec/inspect/mutation exists in the MCP lane.

### Git

- `repo_status`
- `repo_diff`
- `repo_log`

Git diff collection excludes common sensitive paths plus validated configured patterns. Remote URLs are not part of the observer surface.

### NVIDIA GPU

- `gpu_status`
- `gpu_processes`

Both use fixed `nvidia-smi` query fields; no process control is exposed.

### Runtime, toolchain, package and archive inspection

- `tool_availability`
- `runtime_versions`
- `python_environment`
- `node_environment`
- `rust_environment`
- `cargo_metadata`
- `nix_environment`
- `mise_environment`
- `package_info`
- `archive_list`

`cargo_metadata` is offline. Nix observation uses offline/read-oriented metadata queries and does not build, update profiles or write lock files. `archive_list` lists members without extraction.

## Preferred CLI and fallback behavior

The MCP API is independent of which CLI is installed. Backends are package-owned constants; model input never chooses an executable or flags.

Current preference chains include:

| Capability | Preferred | Fallback |
|---|---|---|
| text search | `rg` | `grep` |
| file discovery | `fd` | `find` |
| tree view | `eza` | `find` |
| disk hotspots | `dust` | `du` |
| process list | `procs` | `ps` |
| checksum | `b3sum` | `sha256sum`, then `shasum -a 256` |
| JSON | native parser | - |
| structured data | fixed `yq` where required | native JSON/TOML parser where supported |

If no safe equivalent backend exists, the tool returns `unsupported_capability`. The MCP never installs missing software automatically. A fallback is rejected when it cannot preserve the requested security policy; for example, a grep fallback will not silently ignore a configured exclusion it cannot safely represent.

## Runtime configuration

Runtime configuration normally lives at:

```text
~/.config/remote-observer-mcp/config.toml
```

or at the path selected by `REMOTE_OBSERVER_CONFIG`. The gateway runbook uses `/etc/remote-observer-mcp/config.toml`.

Start from `config.example.toml`. Do not store passwords, API keys, SSH private keys or other credentials in this TOML file.

Example:

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

[hosts.remote.containers.api]
name = "api"
logs = false

[hosts.remote.repos.app]
path = "/srv/app"
secret_patterns = ["private/**"]

[workspaces.app]
host = "remote"
root = "/srv/app"
secret_patterns = ["private/**"]
compose = false
```

The v1 identifier/path grammars are deliberately narrow. Add legitimate unsupported naming conventions through a reviewed schema change instead of bypassing validation.

## Development and deterministic verification

Requirements: Python 3.12.

```bash
python -m pip install -e '.[dev]'
python -m ruff check .
python -m pytest -q
python scripts/smoke_stdio.py
```

The deterministic suite does not require real SSH hosts, credentials, Docker, systemd, NVIDIA hardware, modern CLI installations, Secure MCP Tunnel access, a GitHub Environment, or a self-hosted runner. Observer/backend tests use fake transports where appropriate. `scripts/smoke_stdio.py` performs a real MCP stdio initialize/list-tools/call exchange against a temporary local config.

## Run the read-only MCP server

```bash
export REMOTE_OBSERVER_CONFIG="$HOME/.config/remote-observer-mcp/config.toml"
remote-observer-mcp
```

This command expects an MCP client on stdin/stdout. In production, `tunnel-client` spawns it as its stdio child; it is not a separate standalone daemon. See `deploy/README.md`.

## Execution Bridge

Arbitrary or mutating work belongs to the separate approval-gated execution lane, not to MCP.

Execution requests are versioned JSON documents. `mode="argv"` preserves an exact argument vector and runs without a local shell. `mode="shell"` is break-glass, always risk `R4`, and uses a fixed `bash -lc` implementation after approval. Requests are bounded, reject secret-like literals, resolve only registered hosts/workspaces, and produce a SHA-256 request digest plus sanitized execution evidence.

The executor entry point is:

```text
remote-observer-exec <request_id>
```

The GitHub workflow accepts only `request_id`, validates the committed request on a hosted runner, then gates the **self-hosted execution job itself** on Environment `remote-execution`. No Issue body, comment, PR title, branch text, command, script, argv, host or path is accepted as executable workflow input.

All R1-R4 executions require the independent Environment approval in v1. See `execution_requests/README.md` and `deploy/execution-runner.md`.

## Gateway deployment and production acceptance

The read-lane production supervisor is `tunnel-client`; it owns the stdio child lifecycle and systemd owns the tunnel-client process. The deployment sequence is:

1. deterministic local MCP smoke;
2. optional short gateway/tmux Tunnel smoke;
3. managed gateway/systemd service;
4. ChatGPT read-tool acceptance.

The Execution Bridge has a separate acceptance sequence for the GitHub Environment and self-hosted runner.

No real Tunnel, SSH, Environment approval or self-hosted execution is required by repository CI. `USER_ACTIONS.md` contains those remaining steps, including the no-extra-cost gate before account-backed Secure MCP Tunnel traffic.

## Supported target assumptions

v1 system/service/Docker/Git/GPU observation is primarily designed for Linux targets. The gateway may be a Mac during development. SSH remote command serialization assumes a POSIX-compatible remote login shell; Windows remote targets are outside v1.

## Validation status semantics

- `PASS`: the stated command/check was actually run and passed.
- `FAIL`: it was run and failed.
- `NOT RUN`: intentionally not executed.
- `BLOCKED`: a required user-controlled dependency/environment is unavailable.

Real SSH, Secure MCP Tunnel, GitHub `remote-execution` approval, self-hosted runner, and ChatGPT production acceptance remain `NOT RUN` until the steps in `USER_ACTIONS.md` are performed. Deterministic CI success must not be presented as production connectivity.

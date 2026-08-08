# Generic Read Tools and Approval-Gated Execution Bridge Design

## Goal

Extend `remote-observer-mcp` from host/service observation into a general-purpose, read-oriented inspection layer for registered workspaces and hosts, while keeping arbitrary code execution and state mutation out of the MCP read surface.

The system has two deliberately separate lanes:

```text
ChatGPT
  |
  +-- Read lane ----------------------------------------------+
  |    remote-observer-mcp                                   |
  |      -> logical host/workspace registry                   |
  |      -> semantic tool                                    |
  |      -> preferred CLI / standard fallback                |
  |      -> local or SSH transport                           |
  |      -> output bounds/redaction/audit                    |
  |                                                          |
  +-- Execution lane -----------------------------------------+
       GitHub execution request                              |
         -> workflow_dispatch(request_id)                    |
         -> GitHub Environment: remote-execution             |
         -> independent human approval                       |
         -> self-hosted gateway runner                       |
         -> execution-request CLI                            |
         -> local or registered SSH target
```

## Non-goals

- Do not add `execute`, `shell`, `ssh`, arbitrary hostname, or arbitrary absolute-path tools to the MCP surface.
- Do not infer that a command is safe merely because its common usage is read-only.
- Do not expose mutating CLI options such as `fd --exec`, `tmux send-keys`, `ast-grep --rewrite`, `yq -i`, `git checkout`, or arbitrary `curl` methods through the read lane.
- Do not place literal credentials or secret-bearing execution requests in this public repository.
- Do not bypass ChatGPT plan restrictions by mislabeling a mutating MCP tool as read-only.

## Design principles

1. **Semantic API first.** MCP exposes tasks, not raw commands.
2. **Logical targets only.** Models select registered host/workspace/resource IDs, never arbitrary machine names or host filesystem roots.
3. **Prefer modern CLI, tolerate minimal hosts.** A backend resolver prefers modern tools when present and falls back to standard Unix equivalents when behavior can be kept equivalent and safe.
4. **Fail closed.** If no safe backend exists, return `unsupported_capability`; never install software automatically.
5. **Collection policy is primary defense.** Output redaction remains secondary defense.
6. **Execution approval is independent of ChatGPT.** Every execution-lane run waits on a GitHub Environment approval gate before any self-hosted runner executes the request.
7. **Exact request, immutable evidence.** The executor reports request ID, request digest, target, risk, duration, exit code, truncation and redaction metadata.

## Workspace registry

Add first-class workspace configuration independent of host resources.

```toml
[workspaces.paperapp]
host = "emma"
root = "/srv/paperapp"

[workspaces.research]
host = "gpu1"
root = "/home/research/project"
```

`WorkspaceConfig` contains:

- logical workspace ID;
- registered host ID;
- validated absolute root path;
- optional secret-path patterns;
- optional capability flags when a workspace must disable expensive/sensitive observers.

MCP inputs use `workspace="paperapp"` and relative paths. Relative paths are normalized lexically, reject absolute paths/control characters/traversal, and are checked against denied secret patterns before any command is built. Where local filesystem resolution is possible, symlink escape is rejected. For SSH targets, commands operate relative to the configured root and never accept a model-provided absolute path.

## Backend resolver

Backends are package-owned implementations, not configurable executable strings.

Initial preference chains:

| Capability | Preferred | Fallback |
|---|---|---|
| text search | `rg` | `grep` |
| find files | `fd` | `find` |
| tree/list | `eza` | `find` |
| disk hotspots | `dust` | `du` |
| process list | `procs` | `ps` |
| JSON query | `jq` | Python JSON parser |
| structured YAML/TOML query | `yq` | narrow Python parser where supported |
| checksum | `b3sum` | `sha256sum` / `shasum -a 256` |

The resolver performs only fixed capability checks such as `command -v` via a package-owned fixed command. A backend module owns its fixed argv templates and result parser.

No MCP argument becomes an executable name or shell fragment.

## Read-lane tool groups

### 1. Workspace inspection

- `list_workspaces`
- `workspace_find(workspace, pattern?, kind?, max_results?)`
- `workspace_search(workspace, query, glob?, max_results?)`
- `workspace_read(workspace, relative_path, start_line?, end_line?)`
- `workspace_tree(workspace, relative_path?, depth?)`
- `file_info(workspace, relative_path)`
- `checksum(workspace, relative_path)`

`workspace_read` is implemented as bounded byte/line reading rather than a raw `bat` passthrough. `bat` may be used only where its output is deterministic and decoration is disabled; native/Python reading is the baseline.

### 2. Code and structured data

- `code_search(workspace, language, pattern, max_results?)` using `ast-grep` when installed; no rewrite/fix options.
- `json_query(workspace, relative_path, expression)` with a deliberately narrow jq-like expression grammar; do not expose arbitrary jq programs in v1 if they can invoke unsupported features.
- `structured_query(workspace, relative_path, selector)` for YAML/TOML/JSON read selectors; no in-place/write operations.

### 3. tmux observation

- `tmux_sessions(host)`
- `tmux_windows(host, session)`
- `tmux_panes(host, session, window?)`
- `tmux_capture(host, pane, lines=100)`

Only list/capture operations are exposed. Pane/session IDs must come from narrow validated identifiers. No `send-keys`, `run-shell`, `new-session`, kill, resize, or option mutation exists in the read lane.

### 4. Host diagnostics

- `process_list(host, limit?)`
- `process_tree(host, process?)`
- `network_listeners(host)`
- `network_interfaces(host)`
- `network_routes(host)`
- `dns_lookup(host, name)` with validated DNS-name grammar only
- `filesystem_status(host)` (`df`, `findmnt`, `lsblk` as available)
- `disk_hotspots(host, root_id?, depth?)`; roots are configured logical IDs, not paths
- `user_sessions(host)`
- `hardware_info(host)`
- `sensor_status(host)` when `sensors` exists

No packet capture, arbitrary URL fetch, arbitrary socket connect, or port scan is part of this group.

### 5. Services and runtime diagnostics

Extend existing observers with:

- `service_failures(host)`
- `systemd_timers(host)`
- `journal_query(host, service, since_minutes?, priority?, lines?)`
- `container_stats(host)`
- `compose_status(host, workspace)` only when the workspace explicitly enables Compose observation
- `gpu_processes(host)`

`journal_query` remains bound to configured services and fixed filters. It cannot query arbitrary units or arbitrary journal fields.

### 6. Environment/toolchain/package/archive inspection

- `tool_availability(host)` returns supported capability/backend availability without installing anything.
- `runtime_versions(host)` for a fixed catalog such as Python, uv, Node, npm, pnpm, Rust, Cargo, rustup, Nix, Git, Docker, tmux, CUDA/NVIDIA tooling and mise.
- `python_environment(workspace)` using fixed interpreter/package metadata queries, no `python -c` supplied by the model.
- `node_environment(workspace)` using fixed version/manifest metadata.
- `rust_environment(workspace)` and `cargo_metadata(workspace)` with `cargo metadata --offline`.
- `nix_environment(workspace)` using fixed read-oriented `nix`/`nix-store` queries; no build/profile mutation.
- `mise_environment(workspace)` using fixed listing/version operations.
- `package_info(host, package)` using platform-specific read metadata commands and a validated package-name grammar.
- `archive_list(workspace, relative_path)` using type-specific list-only commands; never extract.

## Genericity without raw read execution

A single `readonly_exec(program, args)` tool is intentionally rejected for v1. Many nominally read-oriented tools have execution/write escape hatches, and validating every option grammar would recreate a shell policy engine.

Genericity instead comes from:

- a broad semantic catalog;
- backend fallback;
- logical host/workspace targets;
- reusable bounded result models;
- a `tool_availability` catalog so ChatGPT can discover what each target supports.

When a task falls outside this surface, it goes to the execution lane rather than weakening the MCP boundary.

## Execution request model

Execution requests are versioned JSON documents consumed by a separate CLI, not MCP tools.

Conceptual schema:

```json
{
  "schema_version": 1,
  "request_id": "exec-20260808-0001",
  "target": {"host": "emma", "workspace": "paperapp"},
  "mode": "argv",
  "argv": ["python", "-m", "pytest", "-q"],
  "timeout_seconds": 120,
  "risk": "R1",
  "reason": "Run repository tests after a proposed change"
}
```

Break-glass requests use `mode="shell"` and a script field. They are always risk `R4` regardless of claimed reason.

Risk metadata:

- `R1`: transient compute/read-heavy task;
- `R2`: reversible workspace mutation;
- `R3`: persistent/remote state change;
- `R4`: privileged, destructive, package-management, arbitrary shell, or otherwise high-impact operation.

Risk is informational in v1 because **all levels require Environment approval**. It may support stricter policies later; it never lowers the manual gate.

## Execution request storage and public-repository boundary

The code repository remains public, so committed request payloads must not contain secrets. The validator rejects common token/private-key/password patterns and documents that paths/commands are public metadata if committed.

The workflow accepts only a narrow `request_id` (for example `exec-[0-9]{8}-[0-9]{4}`), maps it to a fixed request directory, and validates the document before approval/execution. It never accepts a command/script as workflow input.

For deployments where command confidentiality matters, the same executor can later be hosted from a private companion repository without changing the executor schema. v1 documents this trade-off explicitly.

## GitHub approval workflow

A dedicated workflow uses:

- `workflow_dispatch` with only `request_id`;
- `environment: remote-execution` on the execution job;
- `runs-on: [self-hosted, remote-observer]`;
- read-only repository token permissions unless additional metadata publication is explicitly required;
- checkout of an immutable ref/digest before reading the request;
- a Python executor invocation, never `run: ${{ inputs.command }}`;
- concurrency keyed by request ID to prevent duplicate simultaneous execution.

The GitHub Environment must be configured with required reviewers. This configuration and self-hosted runner installation are user-controlled production acceptance steps and remain in `USER_ACTIONS.md`.

## Executor behavior

The executor:

1. validates request ID and schema;
2. rejects literal secret-like content;
3. resolves host/workspace through the same config library used by the observer;
4. verifies risk/mode invariants (`shell => R4`);
5. computes and prints a request SHA-256 digest;
6. executes argv without a local shell, or break-glass shell using a fixed `bash -lc` implementation only after the external approval gate;
7. supports local or registered SSH transport;
8. enforces timeout and output caps;
9. redacts returned output;
10. emits structured execution metadata and exits nonzero on failure.

The executor does not elevate privileges by itself. `sudo` or package management may appear only in an explicitly approved R4 request and remain subject to the runner/service account's actual OS permissions.

## Security boundaries

Read lane:

- every MCP tool keeps read-only/non-destructive/idempotent/closed-world annotations;
- no model-provided executable, hostname, absolute root, shell fragment, or mutating option;
- secret path deny rules apply to search/find/read/tree/structured tools;
- outputs and result counts are bounded;
- existing metadata-only audit remains the common wrapper.

Execution lane:

- separate entry point and workflow;
- Environment approval before self-hosted execution;
- narrow request ID workflow input;
- versioned validated request documents;
- no secrets in request payload;
- exact request digest in evidence;
- no automatic retry of commands with side effects;
- no execution from Issue body/comment text.

## Testing strategy

Each implementation outcome follows RED -> GREEN -> REFACTOR.

Deterministic tests cover:

- workspace ID/path validation and traversal/symlink/secret-pattern rejection;
- backend preference/fallback with fake executables;
- generated argv contains no unsafe execution options;
- bounded parsers for every new observer;
- MCP schema and annotations across all registered tools;
- tmux capture cannot invoke send/run operations;
- structured/AST tools cannot mutate files;
- execution request schema/risk/secret validation;
- shell request always R4;
- workflow static contract: Environment gate, self-hosted labels, narrow input, no command interpolation;
- output cap/redaction/audit integration;
- stdio smoke after the complete extension set.

Real command availability, real SSH, GitHub Environment approval and self-hosted runner execution remain separate acceptance checks.

## Implementation decomposition

Shared contracts are merged first. Independent feature groups branch from the same verified main SHA after their dependencies are stable:

1. workspace/config contract;
2. backend resolver/tool catalog;
3. workspace search/find/read/tree;
4. AST/structured query;
5. tmux observation;
6. process/network/filesystem diagnostics;
7. systemd/journal/Docker/GPU diagnostics;
8. runtime/toolchain/package/archive observation;
9. execution request schema/executor;
10. GitHub approval workflow/deployment docs;
11. adversarial hardening/full acceptance documentation.

Items 3-8 may run in parallel after 1-2. Items 9-10 are a separate lane and can run in parallel with read-tool groups once config/shared result contracts are stable.

## Completion criteria

Repository implementation is complete when:

- all listed read tools are registered and deterministic tests pass;
- fallback behavior is tested without requiring modern CLI installation;
- no read tool exposes arbitrary execution or mutation;
- execution request/executor/workflow static contracts pass;
- full ruff/pytest/stdio smoke is green at a fixed SHA;
- all implementation PRs are normal merge commits, never squash merges;
- remaining real Environment/self-hosted/Tunnel/SSH setup is consolidated in `USER_ACTIONS.md` and tracked as production acceptance rather than falsely reported as complete.

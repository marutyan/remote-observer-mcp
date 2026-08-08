# Generic Read Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the read-only MCP surface with workspace inspection, safe CLI fallback, tmux/host diagnostics, and runtime/toolchain/package/archive observation without introducing arbitrary execution.

**Architecture:** Add a first-class workspace registry and a package-owned backend resolver. Each observer remains semantic: it builds fixed argv templates, selects a safe preferred/fallback backend, runs through the existing local/SSH transport, then returns bounded/redacted/audited structured data. Independent observer groups branch only after workspace and backend contracts merge.

**Tech Stack:** Python 3.12, `mcp>=1.28.1,<2`, stdlib `asyncio`/`subprocess`/`tomllib`/`pathlib`/`json`, pytest, pytest-asyncio, ruff, GitHub Actions.

## Global Constraints

- The MCP surface remains read-only/non-destructive/idempotent/closed-world.
- No tool accepts arbitrary executable names, hostnames, absolute roots, shell fragments, or mutating CLI options.
- Host/workspace/resource identifiers are logical IDs resolved from TOML and fail closed.
- Prefer modern CLI only when safe behavior is equivalent; otherwise use the package baseline or return `unsupported_capability`.
- Never install missing tools.
- Secret-path deny rules apply before search/find/read/tree/structured/code collection.
- Existing output cap, redaction, metadata-only audit, local/SSH transport, and stdio smoke contracts remain mandatory.
- Every behavior follows RED -> GREEN -> REFACTOR with frequent commits carrying the owning Issue number.
- No squash merge.

---

## File structure additions

```text
src/remote_observer_mcp/
├── backends/
│   ├── __init__.py          # capability resolver and fixed executable discovery
│   └── catalog.py           # package-owned capability -> preferred/fallback definitions
├── workspace.py             # workspace path/secret policy helpers
└── observers/
    ├── workspace.py         # find/search/read/tree/file metadata/checksum
    ├── structured.py        # AST/JSON/YAML/TOML selectors
    ├── tmux.py              # list/capture only
    ├── diagnostics.py       # process/network/filesystem/user/hardware
    ├── service_ext.py       # failed units/timers/bounded journal query
    ├── container_ext.py     # stats/compose status
    ├── gpu_ext.py           # GPU process view
    └── environment.py       # availability/runtime/toolchain/package/archive

tests/
├── test_workspace_config.py
├── test_backend_resolver.py
├── test_workspace_observer.py
├── test_structured_observer.py
├── test_tmux_observer.py
├── test_diagnostics_observer.py
├── test_service_extensions.py
├── test_container_extensions.py
├── test_gpu_extensions.py
├── test_environment_observer.py
└── test_generic_read_security.py
```

---

### Task 1: Workspace registry and path policy

**Files:**
- Modify: `src/remote_observer_mcp/config.py`
- Create: `src/remote_observer_mcp/workspace.py`
- Modify: `config.example.toml`
- Test: `tests/test_workspace_config.py`

**Interfaces:**
- Produces `WorkspaceConfig(workspace_id: str, host_id: str, root: str, secret_patterns: tuple[str, ...], compose: bool)`.
- Produces `AppConfig.workspace(workspace_id: str) -> WorkspaceConfig`.
- Produces `normalize_relative_path(value: str) -> str` and `ensure_visible_relative_path(workspace, value) -> str`.

- [ ] Add failing config tests for valid workspace resolution, unknown host reference, traversal, absolute relative-path input, control characters, and invalid secret patterns.
- [ ] Verify RED in CI.
- [ ] Extend root config allowed keys to `hosts` and `workspaces`; parse immutable workspace mappings and verify each workspace references an existing host.
- [ ] Implement relative-path normalization: allow `.` and normal POSIX segments, reject absolute paths, `..`, NUL/control characters, backslash ambiguity, and empty path where a file is required.
- [ ] Apply default secret patterns (`.env`, `.env.*`, `*.pem`, `*.key`, credential/secret names) plus workspace-config patterns before returning a relative path for content access.
- [ ] Add example workspaces to `config.example.toml` without real paths or credentials.
- [ ] Run targeted/full tests and ruff.
- [ ] Commit: `feat(workspace): add fail-closed workspace registry (#<issue>)`.

**Parallelization gate:** Tasks 2+ must consume this merged contract.

---

### Task 2: Backend resolver and capability catalog

**Files:**
- Create: `src/remote_observer_mcp/backends/__init__.py`
- Create: `src/remote_observer_mcp/backends/catalog.py`
- Test: `tests/test_backend_resolver.py`

**Interfaces:**
- `BackendChoice(capability: str, executable: str, variant: str)`.
- `resolve_backend(transport, capability) -> Awaitable[BackendChoice]`.
- Package-owned catalog includes `search: rg|grep`, `find: fd|find`, `tree: eza|find`, `disk_hotspots: dust|du`, `process_list: procs|ps`, `checksum: b3sum|sha256sum|shasum`, `json: jq|python-native`, `structured: yq|python-native`.

- [ ] Write failing resolver tests using fake transports that emulate fixed `command -v` results.
- [ ] Verify RED.
- [ ] Implement fixed capability catalog; unknown capability raises `unsupported_capability` without echoing model text.
- [ ] Implement fixed executable discovery. Executable names come only from catalog constants.
- [ ] Cache choices per transport instance/capability only for one MCP process lifetime; failures remain deterministic.
- [ ] Add tests proving model input cannot select an executable or add flags.
- [ ] Run targeted/full tests and ruff.
- [ ] Commit: `feat(backends): add safe preferred and fallback resolver (#<issue>)`.

**Parallelization gate:** Tasks 3-8 may branch from the same main SHA after Tasks 1-2 merge.

---

### Task 3: Workspace find/search/read/tree and file metadata

**Files:**
- Create: `src/remote_observer_mcp/observers/workspace.py`
- Test: `tests/test_workspace_observer.py`

**Interfaces:**
- MCP tools: `list_workspaces`, `workspace_find`, `workspace_search`, `workspace_read`, `workspace_tree`, `file_info`, `checksum`.
- `max_results` clamps to `1..500`; tree depth to `1..8`; read line span to at most 1000 lines and existing byte cap.

- [ ] Write failing tests for exact tool schemas, registered workspace resolution, backend preference/fallback, traversal/secret denial, result caps, and no mutating options (`--exec`, `-x`, shell preprocessor flags).
- [ ] Verify RED.
- [ ] Implement `list_workspaces` summaries without exposing actual root paths.
- [ ] Implement `workspace_find` with `fd` or `find`, always rooted at configured workspace; return workspace-relative paths only.
- [ ] Implement `workspace_search` with `rg` or `grep`, fixed recursive/read-only flags, bounded results, optional validated glob grammar, no preprocessor/command options.
- [ ] Implement `workspace_read` with package-owned Python/native bounded reading rather than free-form CLI execution.
- [ ] Implement `workspace_tree` using `eza` or `find`; normalize all results to relative paths.
- [ ] Implement `file_info` using fixed `stat`/`file` calls and `checksum` using resolver variants.
- [ ] Register tools through extension registrar and audit wrapper.
- [ ] Run targeted/full tests and stdio smoke.
- [ ] Commit logical units: `feat(workspace): add search and discovery tools (#<issue>)`; `feat(workspace): add bounded read and metadata tools (#<issue>)`.

---

### Task 4: AST and structured-data query tools

**Files:**
- Create: `src/remote_observer_mcp/observers/structured.py`
- Test: `tests/test_structured_observer.py`

**Interfaces:**
- `code_search(workspace, language, pattern, max_results=100)`.
- `json_query(workspace, relative_path, selector)`.
- `structured_query(workspace, relative_path, selector)` supporting JSON/YAML/TOML by extension.

- [ ] Write failing tests showing `ast-grep` search uses fixed search-only options and never rewrite/update flags.
- [ ] Define narrow languages (`python`, `rust`, `javascript`, `typescript`, `json`, `yaml`) and a bounded pattern grammar with no shell interpolation.
- [ ] Implement `code_search`; if `ast-grep` is absent, return `unsupported_capability` rather than guessing a semantic fallback.
- [ ] Define selector grammar as dot-separated keys plus non-negative array indexes; reject arbitrary jq/yq programs, functions, pipes, assignment/update operators and shell-like text.
- [ ] Implement JSON parsing in Python baseline; YAML only when safe `yq` backend is present; TOML via `tomllib` baseline.
- [ ] Apply workspace secret/path policy before opening/querying.
- [ ] Run targeted/full tests and stdio smoke.
- [ ] Commit: `feat(structured): add safe AST and data queries (#<issue>)`.

---

### Task 5: tmux observation

**Files:**
- Create: `src/remote_observer_mcp/observers/tmux.py`
- Test: `tests/test_tmux_observer.py`

**Interfaces:**
- `tmux_sessions(host)`; `tmux_windows(host, session)`; `tmux_panes(host, session, window=None)`; `tmux_capture(host, pane, lines=100)`.

- [ ] Write failing tests for fixed list-format commands, missing tmux normalization, identifier validation and bounded capture.
- [ ] Add adversarial assertions that generated argv never contains `send-keys`, `run-shell`, `new-session`, `kill-*`, `set-option`, or command strings from input.
- [ ] Implement fixed `list-sessions`, `list-windows`, `list-panes` formats and parse structured tab-delimited output.
- [ ] Implement `capture-pane -p` with fixed start offset computed from clamped line count and validated pane ID grammar.
- [ ] Return `unsupported_capability` when tmux is unavailable or has no server.
- [ ] Run targeted/full tests and stdio smoke.
- [ ] Commit: `feat(tmux): add list and bounded capture observers (#<issue>)`.

---

### Task 6: Process, network, filesystem and host diagnostics

**Files:**
- Create: `src/remote_observer_mcp/observers/diagnostics.py`
- Test: `tests/test_diagnostics_observer.py`

**Interfaces:**
- `process_list`, `process_tree`, `network_listeners`, `network_interfaces`, `network_routes`, `dns_lookup`, `filesystem_status`, `disk_hotspots`, `user_sessions`, `hardware_info`, `sensor_status`.

- [ ] Write RED tests for preferred/fallback process/disk backends and fixed network/filesystem commands.
- [ ] Define DNS-name grammar and reject IP/URL/shell input outside the intended name lookup surface.
- [ ] Implement process listing without environment or full command-line secrets by default; return PID/user/state/name and bounded CPU/memory fields only.
- [ ] Implement `network_listeners` with `ss` then `lsof` fallback; no active connection attempts/port scan.
- [ ] Implement interface/route read commands with `ip` then platform fallback; normalize unavailable capability.
- [ ] Implement filesystem status with `df`/`findmnt`/`lsblk` fixed calls.
- [ ] Implement configured-root `disk_hotspots`; add optional host `roots` registry only if needed, never accept a raw path.
- [ ] Implement sessions/hardware/sensors with fixed read commands and bounded parsers.
- [ ] Run targeted/full tests and stdio smoke.
- [ ] Commit: `feat(diagnostics): add host process network and filesystem views (#<issue>)`.

---

### Task 7: Service, journal, Docker and GPU extensions

**Files:**
- Create: `src/remote_observer_mcp/observers/service_ext.py`
- Create: `src/remote_observer_mcp/observers/container_ext.py`
- Create: `src/remote_observer_mcp/observers/gpu_ext.py`
- Tests: `tests/test_service_extensions.py`, `tests/test_container_extensions.py`, `tests/test_gpu_extensions.py`

**Interfaces:**
- `service_failures(host)`, `systemd_timers(host)`, `journal_query(host, service, since_minutes=60, priority=None, lines=100)`.
- `container_stats(host)`, `compose_status(workspace)`.
- `gpu_processes(host)`.

- [ ] Add failing systemd tests for fixed failed/timer formats and journal filters bound to registered service IDs.
- [ ] Clamp journal time to 1..10080 minutes, priority to fixed enum, lines to 1..500; never accept arbitrary journal match expressions.
- [ ] Implement `container_stats` only for configured containers; no inspect/exec.
- [ ] Implement `compose_status` only when `WorkspaceConfig.compose=true`; fixed `docker compose ps --format json` rooted at configured workspace.
- [ ] Implement GPU process query with fixed `nvidia-smi` fields and no process control.
- [ ] Run targeted/full tests and stdio smoke.
- [ ] Commit per observer family with owning Issue number.

---

### Task 8: Tool availability, runtimes, package and archive inspection

**Files:**
- Create: `src/remote_observer_mcp/observers/environment.py`
- Test: `tests/test_environment_observer.py`

**Interfaces:**
- `tool_availability(host)`, `runtime_versions(host)`, `python_environment(workspace)`, `node_environment(workspace)`, `rust_environment(workspace)`, `cargo_metadata(workspace)`, `nix_environment(workspace)`, `mise_environment(workspace)`, `package_info(host, package)`, `archive_list(workspace, relative_path)`.

- [ ] Write failing tests for fixed runtime catalog and missing-tool behavior.
- [ ] Implement version probing with package-owned executable/subcommand pairs only; never model-provided executable names.
- [ ] Python environment: fixed `python --version`, `pip --version`/`uv --version`, and manifest presence; no model-provided `python -c`.
- [ ] Node environment: fixed `node/npm/pnpm --version` and parsed package manifest metadata.
- [ ] Rust/Cargo: fixed `rustc/cargo/rustup --version`, `cargo metadata --offline --format-version 1 --no-deps`.
- [ ] Nix/mise: fixed read-only version/list/show commands; no build/profile/install/update operations.
- [ ] Package info: validated package-name grammar and platform-specific read metadata (`dpkg-query`/`apt-cache`, `brew info --json`, equivalent safe fallback) without installation.
- [ ] Archive list: detect extension and use list-only fixed commands (`tar -tf`, `unzip -l`, `7z l`); secret/path policy first; never extract.
- [ ] Run targeted/full tests and stdio smoke.
- [ ] Commit: `feat(environment): add toolchain package and archive observers (#<issue>)`.

---

### Task 9: Cross-cutting generic-read hardening

**Files:**
- Create: `tests/test_generic_read_security.py`
- Modify focused observer/policy files only as failures require.
- Modify: `README.md`, `config.example.toml`.

**Interfaces:**
- No new public escape-hatch tool.

- [ ] Enumerate all MCP tools and assert read-only/non-destructive/idempotent/closed-world annotations.
- [ ] Assert forbidden input names remain absent: `command`, `argv`, `hostname`, `absolute_path`, `shell`, `script`, `url`, `method`.
- [ ] Add injection-shaped cases for workspace/path/glob/selector/language/tmux IDs/DNS/package names.
- [ ] Add secret-path regression across find/search/read/tree/structured/code/archive.
- [ ] Add backend-option regression ensuring no `--exec`, `--pre`, rewrite/in-place/send-keys/inspect/exec mutation options can be generated.
- [ ] Update README with complete read catalog, fallback semantics, unsupported-capability behavior and explicit boundary to Execution Bridge.
- [ ] Run `python -m ruff check .`, `python -m pytest -q`, and `python scripts/smoke_stdio.py` on a fixed SHA.
- [ ] Commit: `test(security): harden generic read tool surface (#<issue>)`; `docs(read): document generic observer catalog (#<issue>)`.

---

## Issue/PR sequencing

Create one numbered Issue + one normal-merge PR per reviewer-sized outcome:

1. workspace contract;
2. backend resolver;
3. workspace tools;
4. structured/code queries;
5. tmux;
6. host diagnostics;
7. service/container/GPU extensions;
8. environment/toolchain/package/archive;
9. cross-cutting security/docs.

Tasks 3-8 are independent after Tasks 1-2 merge and should be implemented on separate branches from the same verified main SHA. Do not modify the same registrar/core file unnecessarily; rely on package extension auto-registration.

## Completion verification

Required deterministic evidence:

```bash
python -m ruff check .
python -m pytest -q
python scripts/smoke_stdio.py
```

All must exit 0. Real SSH/Tunnel/tool installation remains outside deterministic completion and is tracked in production acceptance.

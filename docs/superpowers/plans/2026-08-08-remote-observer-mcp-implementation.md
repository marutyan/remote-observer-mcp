# Remote Observer MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-oriented, read-only MCP server that observes explicitly registered local and SSH-accessible hosts without exposing arbitrary shell execution or arbitrary filesystem access.

**Architecture:** A stdio MCP adapter exposes semantic observer tools. Requests resolve logical host/resource IDs through a local TOML registry, execute fixed commands through local or SSH transports, then apply output bounds, redaction, normalization, and metadata-only audit logging before returning data. The same package runs on a Mac during development and on a long-lived Linux gateway in production.

**Tech Stack:** Python 3.12, `mcp>=1.28.1,<2`, stdlib `asyncio`/`subprocess`/`tomllib`, pytest, pytest-asyncio, ruff, GitHub Actions.

## Global Constraints

- Target Python is 3.12.
- Pin MCP Python SDK to `mcp>=1.28.1,<2`; v2 migration is a separate future change.
- The public MCP surface is read-only and semantic; no `execute`, `shell`, arbitrary SSH command, arbitrary hostname, or arbitrary path parameter exists.
- Host/resource identifiers resolve through local configuration and fail closed.
- No credential, token, private key, `.env` value, or literal secret is committed.
- Local subprocesses are created without `shell=True`.
- SSH remote command components are fixed implementation tokens or strictly validated configuration values; model-provided free-form text never becomes remote command syntax.
- Output is bounded before crossing the MCP boundary and then redacted.
- Real SSH/Tunnel/systemd installation is deferred to the user-action batch; unit/integration tests use local fakes.
- Every production behavior follows RED -> GREEN -> REFACTOR.
- Commits use the owning Issue number; no squash merge is used.
- Independent observer work may branch in parallel only after Task 3 fixes the shared registry/transport/result interfaces.

---

## File Structure

```text
remote-observer-mcp/
├── pyproject.toml
├── README.md
├── config.example.toml
├── .github/workflows/ci.yml
├── src/remote_observer_mcp/
│   ├── __init__.py
│   ├── __main__.py
│   ├── server.py
│   ├── config.py
│   ├── errors.py
│   ├── models.py
│   ├── policy.py
│   ├── audit.py
│   ├── transports/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── local.py
│   │   └── ssh.py
│   └── observers/
│       ├── __init__.py
│       ├── system.py
│       ├── systemd.py
│       ├── docker.py
│       ├── git.py
│       └── gpu.py
├── tests/
│   ├── conftest.py
│   ├── test_package.py
│   ├── test_config.py
│   ├── test_policy.py
│   ├── test_transports.py
│   ├── test_server.py
│   ├── test_system_observer.py
│   ├── test_systemd_observer.py
│   ├── test_docker_observer.py
│   ├── test_git_observer.py
│   ├── test_gpu_observer.py
│   └── test_security.py
├── scripts/
│   └── smoke_stdio.py
└── deploy/
    ├── remote-observer-mcp.service
    ├── tunnel-client.service.example
    └── README.md
```

`server.py` owns only MCP registration/dispatch. `config.py` owns TOML parsing and registry resolution. `models.py` defines stable typed requests/results shared by observers and transports. `policy.py` owns validation, output limits, and redaction. Transports own process execution only. Each observer owns one command family.

---

### Task 1: Project scaffold, CI, and deterministic test harness

**Issue/PR outcome:** The repository is installable on Python 3.12 and CI can prove formatting/lint/test status without any SSH host.

**Files:**
- Create: `pyproject.toml`
- Create: `.github/workflows/ci.yml`
- Create: `src/remote_observer_mcp/__init__.py`
- Create: `tests/test_package.py`
- Create: `tests/conftest.py`
- Modify: `README.md`

**Interfaces:**
- Produces an importable `remote_observer_mcp` package with version `0.1.0`.
- Development commands: `python -m pytest -q` and `python -m ruff check .`.

- [ ] **Step 1: Add the first failing package import test**

```python
def test_package_has_version():
    import remote_observer_mcp

    assert remote_observer_mcp.__version__ == "0.1.0"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_package.py -q`

Expected: FAIL because `remote_observer_mcp` is not importable.

- [ ] **Step 3: Add minimal packaging and package version**

`pyproject.toml` must declare:

```toml
[project]
name = "remote-observer-mcp"
version = "0.1.0"
requires-python = ">=3.12,<3.14"
dependencies = ["mcp>=1.28.1,<2"]

[project.optional-dependencies]
dev = [
  "pytest>=8.3,<9",
  "pytest-asyncio>=0.25,<1",
  "ruff>=0.12,<1",
]
```

`src/remote_observer_mcp/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_package.py -q`

Expected: PASS.

- [ ] **Step 5: Add CI**

CI runs on `ubuntu-24.04`, Python 3.12, installs editable dev dependencies, then runs:

```bash
python -m ruff check .
python -m pytest -q
```

- [ ] **Step 6: Commit**

Commit: `chore(scaffold): add Python package and CI (#<issue>)`.

---

### Task 2: Configuration, registry, and strict identifier validation

**Issue/PR outcome:** Runtime TOML resolves only configured logical IDs and rejects unsafe configuration before any transport runs.

**Files:**
- Create: `src/remote_observer_mcp/config.py`
- Create: `src/remote_observer_mcp/errors.py`
- Create: `config.example.toml`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `load_config(path: Path) -> AppConfig`
  - `AppConfig.host(host_id: str) -> HostConfig`
  - `HostConfig.service(resource_id: str) -> ServiceConfig`
  - `HostConfig.repo(resource_id: str) -> RepoConfig`
  - `HostConfig.container(resource_id: str) -> ContainerConfig`
- Unknown logical IDs raise `ObserverError(code="unknown_host" | "unknown_resource", ...)`.

- [ ] **Step 1: Write failing tests for valid logical resolution and unknown host**

```python
def test_registry_resolves_only_known_host(config_file):
    config = load_config(config_file)
    assert config.host("gateway").transport == "local"

    with pytest.raises(ObserverError) as exc:
        config.host("unknown")
    assert exc.value.code == "unknown_host"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_config.py -q`

Expected: FAIL because config types do not exist.

- [ ] **Step 3: Implement immutable dataclasses and TOML parser**

Use frozen dataclasses for `AppConfig`, `HostConfig`, `ServiceConfig`, `RepoConfig`, and `ContainerConfig`. Parse with `tomllib`.

Validation rules:
- logical IDs: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`;
- SSH aliases: same narrow grammar;
- systemd units/container names: `^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$`;
- repository paths: absolute, no control chars, no quotes/backticks/dollar/semicolon/pipe/ampersand/redirection characters, no newline/tab;
- transport: exactly `local` or `ssh`.

- [ ] **Step 4: Add failing tests for unsafe alias/path/unit values**

Use cases such as `host;id`, `/srv/repo;rm`, `unit$(id).service`, relative repo path, newline injection.

- [ ] **Step 5: Verify RED then implement fail-closed validation**

Run the targeted tests before and after implementation.

- [ ] **Step 6: Commit**

Commit: `feat(config): add fail-closed resource registry (#<issue>)`.

---

### Task 3: Shared result model, output policy, local/SSH transport contract

**Issue/PR outcome:** All observers depend on one stable transport/result contract with timeout, output cap, redaction, and normalized failures.

**Files:**
- Create: `src/remote_observer_mcp/models.py`
- Create: `src/remote_observer_mcp/policy.py`
- Create: `src/remote_observer_mcp/transports/base.py`
- Create: `src/remote_observer_mcp/transports/local.py`
- Create: `src/remote_observer_mcp/transports/ssh.py`
- Create: `src/remote_observer_mcp/transports/__init__.py`
- Create: `tests/test_policy.py`
- Create: `tests/test_transports.py`

**Interfaces:**
- `CommandSpec(argv: tuple[str, ...], timeout_seconds: float, max_output_bytes: int)`
- `CommandResult(exit_code: int, stdout: str, stderr: str, duration_ms: int, truncated: bool, redacted: bool)`
- `Transport.run(command: CommandSpec) -> Awaitable[CommandResult]`
- `LocalTransport.run(...)`
- `SshTransport(alias: str, connect_timeout_seconds: int).run(...)`
- `sanitize_output(text: str, max_bytes: int) -> SanitizedOutput`

- [ ] **Step 1: Write failing redaction/truncation tests**

```python
def test_sanitize_output_redacts_token_and_truncates():
    raw = "Authorization: Bearer sk-test-secret\n" + ("x" * 1000)
    result = sanitize_output(raw, max_bytes=128)
    assert "sk-test-secret" not in result.text
    assert "[REDACTED]" in result.text
    assert result.truncated is True
```

- [ ] **Step 2: Verify RED and implement policy**

Hard caps:
- ordinary timeout <= 10 s;
- log timeout <= 15 s;
- SSH connect timeout <= 5 s;
- returned stdout+stderr <= 128 KiB;
- line/request limits are clamped by observer code.

Redaction covers common bearer/API-key/private-key header patterns and replaces the full suspected secret value with `[REDACTED]`.

- [ ] **Step 3: Write failing LocalTransport timeout/exit-code tests**

Use `sys.executable -c ...`, not shell commands.

- [ ] **Step 4: Verify RED and implement LocalTransport with `asyncio.create_subprocess_exec`**

Never use `create_subprocess_shell` or `shell=True`.

- [ ] **Step 5: Write failing SSH argv construction test with a fake executable**

Assert local argv begins with:

```text
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 <alias> <validated-remote-command>
```

and unsafe configured values are rejected before process creation.

- [ ] **Step 6: Implement SSH transport**

The remote command string is assembled from observer-provided argv tokens using `shlex.join`. Tokens are fixed implementation values or registry values validated by Task 2. Model-provided free-form command text is not an interface. Tests assert that quotes, `$()`, semicolons, control characters, and unsupported whitespace cannot enter through configuration.

- [ ] **Step 7: Run complete shared-contract suite**

Run:

```bash
python -m pytest tests/test_config.py tests/test_policy.py tests/test_transports.py -q
python -m ruff check .
```

Expected: PASS.

- [ ] **Step 8: Commit**

Commit logical units separately:
1. `feat(policy): bound and redact observer output (#<issue>)`
2. `feat(transport): add local and SSH runners (#<issue>)`

**Parallelization gate:** Only after this PR is merged may Tasks 5-8 be implemented on independent branches.

---

### Task 4: MCP adapter, discovery, and minimal system observer

**Issue/PR outcome:** A stdio MCP server exposes `list_hosts`, `host_overview`, `system_status`, and `disk_usage`, all marked read-only.

**Files:**
- Create: `src/remote_observer_mcp/server.py`
- Create: `src/remote_observer_mcp/observers/system.py`
- Create: `src/remote_observer_mcp/observers/__init__.py`
- Create: `tests/test_server.py`
- Create: `tests/test_system_observer.py`
- Create: `scripts/smoke_stdio.py`
- Create: `src/remote_observer_mcp/__main__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- `create_server(config: AppConfig) -> FastMCP`
- observers return structured dictionaries and never MCP-specific objects.
- all decorators use `ToolAnnotations(readOnlyHint=True, destructiveHint=False)`.

- [ ] **Step 1: Write failing command-builder tests for system status and disk usage**

Linux fixed command forms are explicit and bounded. Unsupported OS is normalized to `unsupported_capability`.

- [ ] **Step 2: Verify RED and implement system observer**

- [ ] **Step 3: Write failing MCP tool-list test**

Create server with a fixture config and assert exact tool names:

```text
list_hosts
host_overview
system_status
disk_usage
```

Assert each tool advertises `readOnlyHint=True`.

- [ ] **Step 4: Verify RED and register FastMCP tools**

`pyproject.toml` adds `remote-observer-mcp = "remote_observer_mcp.server:main"`. `main()` loads `REMOTE_OBSERVER_CONFIG` or `~/.config/remote-observer-mcp/config.toml` and runs stdio.

- [ ] **Step 5: Add stdio smoke script**

The smoke script starts the package as a subprocess, performs MCP initialize/list-tools/call sequence using the SDK client, calls only `list_hosts`, and exits nonzero if unexpected tools or mutation hints appear.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
python -m pytest tests/test_server.py tests/test_system_observer.py -q
python scripts/smoke_stdio.py
```

- [ ] **Step 7: Commit**

1. `feat(system): add bounded system observers (#<issue>)`
2. `feat(mcp): expose read-only stdio tools (#<issue>)`

---

### Task 5: systemd observer

**Dependency:** Task 3 and Task 4 merged.

**Files:**
- Create: `src/remote_observer_mcp/observers/systemd.py`
- Create: `tests/test_systemd_observer.py`
- Modify: `src/remote_observer_mcp/server.py`

**Interfaces:**
- `service_status(host, service) -> dict`
- `service_logs(host, service, lines: int = 100) -> dict`
- `lines` clamp: 1..500.
- only registered services with `logs = true` may use `service_logs`.

- [ ] Write failing tests for inactive service normalization, unknown service rejection, log opt-in, and line clamping.
- [ ] Verify RED.
- [ ] Implement fixed `systemctl show`/`journalctl` command builders without arbitrary journal filters.
- [ ] Verify targeted suite and full suite.
- [ ] Commit: `feat(systemd): observe registered services and logs (#<issue>)`.

---

### Task 6: Docker observer

**Dependency:** Task 3 and Task 4 merged. May run in parallel with Tasks 5, 7, and 8.

**Files:**
- Create: `src/remote_observer_mcp/observers/docker.py`
- Create: `tests/test_docker_observer.py`
- Modify: `src/remote_observer_mcp/server.py`

**Interfaces:**
- `container_list(host) -> dict` returns only configured containers.
- `container_logs(host, container, lines=100) -> dict` requires registered container and `logs = true`.
- no `docker inspect`.

- [ ] Write failing tests proving unregistered containers are omitted and `inspect` never appears in generated commands.
- [ ] Verify RED.
- [ ] Implement fixed `docker ps --filter name=... --format ...` and `docker logs --tail ...`.
- [ ] Verify targeted/full tests.
- [ ] Commit: `feat(docker): observe allowlisted containers (#<issue>)`.

---

### Task 7: Git observer with sensitive-path filtering

**Dependency:** Task 3 and Task 4 merged. May run in parallel with Tasks 5, 6, and 8.

**Files:**
- Create: `src/remote_observer_mcp/observers/git.py`
- Create: `tests/test_git_observer.py`
- Modify: `src/remote_observer_mcp/server.py`

**Interfaces:**
- `repo_status(host, repo) -> dict`
- `repo_diff(host, repo) -> dict`
- `repo_log(host, repo, count=20) -> dict`
- count clamp: 1..100.

- [ ] Write failing tests that secret-like paths (`.env`, `*.pem`, `*.key`, configured patterns) never appear in returned diff.
- [ ] Verify RED.
- [ ] Implement status/log fixed commands.
- [ ] Implement diff with fixed/config-validated Git pathspec exclusions in the same `git diff` invocation; never interpolate filenames discovered from repository output into a second remote command. The SSH transport serializes each validated argv token with POSIX-safe quoting before the remote login shell parses it; redaction remains secondary defense.
- [ ] Add tests showing Git remote URLs are not requested or returned.
- [ ] Verify targeted/full tests.
- [ ] Commit:
  1. `feat(git): observe registered repositories (#<issue>)`
  2. `feat(git): filter sensitive diff paths (#<issue>)`

---

### Task 8: GPU observer

**Dependency:** Task 3 and Task 4 merged. May run in parallel with Tasks 5-7.

**Files:**
- Create: `src/remote_observer_mcp/observers/gpu.py`
- Create: `tests/test_gpu_observer.py`
- Modify: `src/remote_observer_mcp/server.py`

**Interfaces:**
- `gpu_status(host) -> dict`
- enabled only when host config has `gpu = true`.
- initial implementation is NVIDIA only and uses fixed CSV fields.

- [ ] Write failing tests for capability-disabled host and parser behavior.
- [ ] Verify RED.
- [ ] Implement fixed `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`.
- [ ] Normalize binary absence as `unsupported_capability`; never install drivers/tools.
- [ ] Verify targeted/full tests.
- [ ] Commit: `feat(gpu): add NVIDIA status observer (#<issue>)`.

---

### Task 9: Adversarial hardening and metadata-only audit

**Dependency:** Tasks 5-8 merged.

**Files:**
- Create: `src/remote_observer_mcp/audit.py`
- Create: `tests/test_security.py`
- Modify: `src/remote_observer_mcp/server.py`
- Modify: `src/remote_observer_mcp/policy.py`

**Interfaces:**
- `AuditEvent(timestamp, tool, host_id, resource_id, duration_ms, outcome, truncated, redacted)`
- audit output is structured JSON lines to stderr or configured local log sink; never stdout because stdio MCP uses stdout.

- [ ] Add injection-shaped tests for `;`, `$()`, backticks, newlines, traversal, control chars, overlong IDs, and unsafe config values.
- [ ] Verify each test fails for the intended missing protection before changing production code.
- [ ] Add audit tests proving raw command output and secret values are absent.
- [ ] Implement remaining validation/redaction/audit gaps.
- [ ] Run `python -m pytest -q` and `python -m ruff check .`.
- [ ] Commit:
  1. `test(security): add adversarial observer cases (#<issue>)`
  2. `feat(audit): record metadata-only tool activity (#<issue>)`

---

### Task 10: Tunnel/gateway packaging and user-action batch

**Dependency:** Task 9 merged.

**Files:**
- Create: `deploy/remote-observer-mcp.service`
- Create: `deploy/tunnel-client.service.example`
- Create: `deploy/README.md`
- Modify: `README.md`
- Modify: `config.example.toml`

**Interfaces:**
- Deployment files contain environment-variable/credential references only.
- No literal tunnel ID/API key/SSH key is committed.

- [ ] Add static tests that deployment files do not contain token/key material patterns and stdio service logs do not redirect protocol stdout into ordinary application logs.
- [ ] Verify RED if files are absent.
- [ ] Add a gateway systemd unit with explicit user, working directory/config reference, restart policy, and hardening compatible with SSH access.
- [ ] Add a tunnel-client example referencing `CONTROL_PLANE_API_KEY` and a placeholder tunnel ID variable/reference, following upstream tunnel-client guidance.
- [ ] Document the transition sequence: Mac manual/tmux -> gateway tmux smoke -> systemd managed service.
- [ ] Add a single `USER_ACTIONS.md` section/document listing only remaining real-environment actions:
  1. confirm no unwanted extra tunnel charge;
  2. create/provide tunnel ID and restricted runtime key without pasting secrets into chat;
  3. provide approved SSH aliases/resource IDs;
  4. run one read-only SSH smoke command set;
  5. approve/install systemd units on the chosen gateway.
- [ ] Run full static/test suite.
- [ ] Commit:
  1. `docs(deploy): add gateway and tunnel runbook (#<issue>)`
  2. `chore(systemd): add managed gateway units (#<issue>)`

---

## Verification Matrix

Before declaring the software complete:

```bash
python -m ruff check .
python -m pytest -q
python scripts/smoke_stdio.py
```

Required observed outcomes:
- all commands exit 0;
- no test is skipped because a real SSH host is absent;
- `tools/list` contains only the approved semantic read-only tools;
- no tool accepts arbitrary command/hostname/path input;
- injection-shaped inputs fail before transport execution;
- output cap and redaction tests pass;
- Git diff sensitive-path tests pass;
- deployment secret-scan tests pass.

Real-environment smoke checks remain `NOT RUN` until the user-action batch is performed.

## Issue/PR Sequencing

Use one numbered Issue and one non-squashed PR per outcome:

1. plan/scaffold;
2. config + registry;
3. transport + policy contract;
4. MCP core + system observer;
5. systemd observer;
6. Docker observer;
7. Git observer;
8. GPU observer;
9. security/audit hardening;
10. tunnel/gateway packaging.

After contract merge, observer Issues 5-8 are independent and may be implemented concurrently. Each branch is cut from the same verified main SHA, and each PR is rebased/updated only through ordinary commits or merges; do not squash history.

## Completion Boundary

The codebase is considered implementation-complete when all local deterministic validation is green and the deployment/user-action batch is documented. Production connectivity is a separate acceptance step because it requires the user's account, SSH environment, credentials, and approval for installation. Do not claim production connectivity before those checks are actually run.

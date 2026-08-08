# Approval-Gated Execution Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate execution lane that can run exact argv or break-glass shell requests on the gateway or registered SSH targets only after an independent GitHub Environment approval.

**Architecture:** Execution requests are versioned JSON documents validated by a separate `remote-observer-exec` CLI. GitHub Actions accepts only a narrow request ID, gates the self-hosted execution job on Environment `remote-execution`, checks out an immutable ref, validates/digests the request, then invokes the executor. The MCP read server never imports or registers execution tools.

**Tech Stack:** Python 3.12, existing config/transport/policy modules, stdlib JSON/hashlib/subprocess, pytest, ruff, GitHub Actions, self-hosted Linux runner.

## Global Constraints

- No execution function is registered as an MCP tool.
- All execution requests require the external GitHub Environment gate in v1, regardless of risk level.
- Workflow input is only a narrow `request_id`; commands/scripts are never workflow inputs or Issue/comment text.
- `argv` mode executes without a local shell.
- `shell` mode is break-glass, uses fixed `bash -lc`, and must be risk `R4`.
- Requests containing secret-like literals are rejected before execution.
- Target hosts/workspaces are logical IDs resolved through existing config.
- Executor does not elevate privileges automatically and does not retry side-effectful commands automatically.
- Output remains capped/redacted and evidence includes request digest.
- No squash merge.

---

## File additions

```text
src/remote_observer_mcp/execution/
├── __init__.py
├── model.py        # schema/risk validation and digest
├── secrets.py      # literal-secret rejection
└── executor.py     # local/SSH argv + R4 shell execution
scripts/
└── execute_request.py
execution_requests/
└── README.md
.github/workflows/
└── approved-execution.yml

tests/
├── test_execution_model.py
├── test_execution_executor.py
├── test_execution_workflow.py
└── test_execution_security.py
```

---

### Task 1: Versioned execution request schema

**Files:**
- Create: `src/remote_observer_mcp/execution/__init__.py`
- Create: `src/remote_observer_mcp/execution/model.py`
- Test: `tests/test_execution_model.py`

**Interfaces:**
- `ExecutionRequest.from_json_bytes(data: bytes) -> ExecutionRequest`.
- Fields: `schema_version=1`, `request_id`, `target.host`, optional `target.workspace`, `mode`, `argv` or `script`, `timeout_seconds`, `risk`, `reason`.
- `request_digest(raw: bytes) -> str` SHA-256.

- [ ] Write failing tests for valid argv request and exact normalized fields.
- [ ] Add failing cases for wrong schema version, unsafe request ID, unknown keys, missing target, invalid risk, timeout outside `1..900`, argv empty/non-string/control-char elements, mode/field mismatch.
- [ ] Require `shell => R4`; reject shell at R1-R3.
- [ ] Validate request ID grammar `exec-[0-9]{8}-[0-9]{4}`.
- [ ] Preserve exact argv ordering; do not normalize into a command string.
- [ ] Add deterministic SHA-256 digest test over original bytes.
- [ ] Run targeted/full tests and ruff.
- [ ] Commit: `feat(execution): add versioned request schema (#<issue>)`.

---

### Task 2: Secret-literal rejection

**Files:**
- Create: `src/remote_observer_mcp/execution/secrets.py`
- Modify: `src/remote_observer_mcp/execution/model.py`
- Test: `tests/test_execution_security.py`

**Interfaces:**
- `reject_secret_literals(request: ExecutionRequest) -> None`.

- [ ] Write RED tests for bearer/API-key patterns, PEM/private key headers, password/token assignments, common OpenAI/GitHub token prefixes, multiline credential material.
- [ ] Test that ordinary flags/paths/reasons do not false-positive on generic words.
- [ ] Implement conservative detection over argv/script/reason only; error message never echoes matched secret.
- [ ] Document that the public repository makes committed request commands/paths public metadata even when secret-free.
- [ ] Run targeted/full tests.
- [ ] Commit: `feat(execution): reject secret-bearing requests (#<issue>)`.

---

### Task 3: Executor contract and argv mode

**Files:**
- Create: `src/remote_observer_mcp/execution/executor.py`
- Create: `scripts/execute_request.py`
- Modify: `pyproject.toml` to add `remote-observer-exec` entry point
- Test: `tests/test_execution_executor.py`

**Interfaces:**
- `execute_request(config: AppConfig, request: ExecutionRequest) -> Awaitable[ExecutionEvidence]`.
- `ExecutionEvidence(request_id, digest, target_host, target_workspace, risk, mode, exit_code, duration_ms, truncated, redacted)` plus bounded stdout/stderr.

- [ ] Write failing local argv tests with `sys.executable` and no `shell=True`.
- [ ] Resolve optional workspace ID, require its host to match target host, and set cwd to configured root.
- [ ] Reuse existing local transport/output policy where possible; add explicit execution timeout support up to 900s without weakening MCP observer defaults.
- [ ] For SSH target, reuse registered SSH alias and strict host-key/batch options; never accept hostname from request.
- [ ] Produce structured JSON evidence to stdout after sanitization; diagnostics go to stderr.
- [ ] No retry on nonzero exit or timeout.
- [ ] Run targeted/full tests.
- [ ] Commit: `feat(execution): run approved argv requests (#<issue>)`.

---

### Task 4: Break-glass shell mode

**Files:**
- Modify: `src/remote_observer_mcp/execution/executor.py`
- Test: `tests/test_execution_executor.py`, `tests/test_execution_security.py`

**Interfaces:**
- Fixed shell implementation only: local `bash -lc <script>`; SSH remote equivalent through strict registered transport.

- [ ] Write RED tests proving shell requests require R4 and use exactly fixed `bash -lc` implementation.
- [ ] Reject NUL/control bytes not valid in a script payload and enforce a script byte cap (32 KiB).
- [ ] Ensure no code path labels shell mode as read-only or exposes it to MCP registration.
- [ ] Add tests that approved shell content is passed as one script argument and not concatenated with target/working-directory input.
- [ ] Run targeted/full tests.
- [ ] Commit: `feat(execution): add R4 break-glass shell mode (#<issue>)`.

---

### Task 5: Request repository contract

**Files:**
- Create: `execution_requests/README.md`
- Create: `execution_requests/example.json`
- Test: `tests/test_execution_security.py`

**Interfaces:**
- Requests stored only as `execution_requests/<request_id>.json`; example contains no real target/path/secret.

- [ ] Add static test that example validates and contains no secret-like material.
- [ ] Document public-metadata warning and recommend private companion repo when command confidentiality matters.
- [ ] Document lifecycle: ChatGPT creates exact request -> user reviews GitHub diff -> workflow dispatch by request ID -> Environment approval -> execution.
- [ ] Do not commit real execution requests as part of implementation.
- [ ] Commit: `docs(execution): define request storage contract (#<issue>)`.

---

### Task 6: GitHub approval workflow

**Files:**
- Create: `.github/workflows/approved-execution.yml`
- Test: `tests/test_execution_workflow.py`

**Interfaces:**
- `workflow_dispatch.inputs.request_id` only.
- Execution job: `environment: remote-execution`; `runs-on: [self-hosted, remote-observer]`.

- [ ] Write static RED tests before workflow creation.
- [ ] Assert workflow `permissions: contents: read` and no broad write token permission.
- [ ] Assert dispatch defines no `command`, `script`, `argv`, `host`, or `path` inputs.
- [ ] Validate request ID in a non-executing hosted preflight job or fixed script before self-hosted execution.
- [ ] Gate the self-hosted job with `environment: remote-execution` and a concurrency key including request ID.
- [ ] Checkout an immutable ref supplied by workflow context and run `remote-observer-exec execution_requests/<id>.json` only after approval.
- [ ] Never use `eval`, `bash -c "${{ inputs... }}"`, Issue body, PR title, comment or branch-name content as executed script.
- [ ] Store sanitized evidence in the job log; no credential output.
- [ ] Run static/full tests.
- [ ] Commit: `ci(execution): add environment-gated execution workflow (#<issue>)`.

---

### Task 7: Deployment and user-action batch

**Files:**
- Modify: `USER_ACTIONS.md`
- Modify: `README.md`
- Create: `deploy/execution-runner.md`
- Test: `tests/test_execution_workflow.py`

**Interfaces:**
- Production setup remains user-controlled.

- [ ] Document creation of GitHub Environment `remote-execution` with required reviewer(s) and no self-approval shortcut where supported.
- [ ] Document self-hosted runner label `remote-observer` and least-privilege service account.
- [ ] State that runner should not expose Docker/root/sudo unless specifically intended for approved R4 operations.
- [ ] Document repository/public metadata boundary and optional private companion repo migration.
- [ ] Add acceptance steps for one benign R1 argv request; arbitrary shell R4 acceptance remains optional and must be visibly reviewed.
- [ ] Run full tests/ruff.
- [ ] Commit: `docs(execution): add approval and runner runbook (#<issue>)`.

---

### Task 8: Cross-cutting execution hardening and final verification

**Files:**
- Modify focused execution files only as regression tests require.
- Test: `tests/test_execution_security.py`, `tests/test_security.py`

**Interfaces:**
- No MCP execution tools.

- [ ] Enumerate MCP tools and assert `execute`, `shell`, `run_command`, `approved_execute` remain absent.
- [ ] Add request traversal test so `request_id` cannot select outside `execution_requests/`.
- [ ] Add malicious JSON/string/control-character tests and unknown-key rejection.
- [ ] Add workflow assertion that Environment gate is on the job that owns the self-hosted runner, not only a harmless preflight job.
- [ ] Add duplicate request concurrency/static replay evidence requirement; execution remains manually re-dispatchable but never automatic.
- [ ] Run `python -m ruff check .`, `python -m pytest -q`, `python scripts/smoke_stdio.py` at fixed SHA.
- [ ] Commit: `test(execution): harden approval-gated bridge (#<issue>)`.

---

## Issue/PR sequencing

Use one numbered Issue + one normal-merge PR for schema/secrets, executor modes, workflow/deployment, and final hardening as reviewer-sized outcomes. Schema must merge before executor; executor and request-doc work may proceed before workflow. The workflow must not be enabled against a real self-hosted runner until Environment reviewer configuration exists.

## Completion verification

Repository implementation is complete when ruff, full pytest, stdio smoke and execution workflow static tests pass. Real Environment approval and self-hosted execution remain `NOT RUN` until user-controlled setup is completed; never report them as PASS from static tests alone.

# Cross-user tmux read-only observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a local logical host to observe one fixed other OS user's tmux sessions/windows/panes/capture without exposing tmux mutation, arbitrary sudo, socket paths, or OS usernames to MCP input.

**Architecture:** Add optional `HostConfig.tmux_user` for local hosts only. The tmux observer selects a dedicated cross-user transport that accepts only the exact read-only `CommandSpec` shapes already produced by the observer and translates them into one fixed helper invocation under `sudo -n -u <configured-user>`. A root-owned helper performs its own validation and invokes only fixed `/usr/bin/tmux` read commands with a sanitized environment.

**Tech Stack:** Python 3.12, pytest/pytest-asyncio, MCP FastMCP, systemd/sudoers deployment docs.

## Global Constraints

- MCP tool schemas remain unchanged and read-only.
- `tmux_user` is accepted only for `transport = "local"` and must match the existing logical identifier grammar.
- No MCP-controlled OS username, sudo command, helper path, socket path, executable, or raw tmux subcommand.
- Helper supports only sessions/windows/panes/capture.
- No `send-keys`, `run-shell`, `new-session`, `kill-*`, option mutation, shell, or `eval` code path.
- Pane capture remains bounded to 1–500 lines.
- Existing direct local tmux and SSH tmux behavior remain unchanged when `tmux_user` is absent.
- Production privilege installation remains an explicit gateway action.
- The first implementation commit only is intentionally dated 2026-08-07 JST; later commits use normal timestamps.

---

### Task 1: Configuration contract

**Files:**
- Modify: `src/remote_observer_mcp/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `HostConfig.tmux_user: str | None`
- Consumes: existing `_ID_RE`, `_ensure_keys`, `_invalid`

- [ ] Add failing tests proving local `tmux_user` is accepted, SSH `tmux_user` is rejected, and unsafe identifiers are rejected.
- [ ] Run `python -m pytest tests/test_config.py -q` and confirm the new tests fail.
- [ ] Add `tmux_user: str | None` to `HostConfig`, allow the key in `_parse_host`, validate it with `_ID_RE`, and reject it unless transport is `local`.
- [ ] Add a commented `tmux_user` example to `config.example.toml` with a note that it requires the dedicated helper/sudoers deployment.
- [ ] Run focused tests and commit as `feat(config): add local tmux_user setting (#58)`. This is the one commit whose author/committer date is 2026-08-07 JST.

### Task 2: Read-only cross-user adapter and helper

**Files:**
- Modify: `src/remote_observer_mcp/observers/tmux.py`
- Create: `src/remote_observer_mcp/tmux_helper.py`
- Create: `deploy/remote-observer-tmux-read`
- Test: `tests/test_tmux_observer.py`
- Create: `tests/test_tmux_helper.py`

**Interfaces:**
- Produces: a tmux-only transport adapter chosen from host config.
- Helper CLI: `remote-observer-tmux-read sessions`; `windows <session>`; `panes <session-or-window>`; `capture <pane> <lines>`.

- [ ] Add failing tests for exact mapping of each existing tmux observer command to `sudo -n -u <tmux_user> -- /usr/local/libexec/remote-observer-tmux-read ...`.
- [ ] Add a test that unexpected/mutating tmux argv fails before any sudo subprocess runs.
- [ ] Add helper tests for valid sessions/windows/panes/capture and rejection of `send-keys`, `run-shell`, `new-session`, `kill-*`, malformed targets, extra args, and capture outside 1–500.
- [ ] Run focused tmux/helper tests and confirm failures.
- [ ] Implement helper parsing with no shell/eval and fixed `/usr/bin/tmux` argv only. Clear `TMUX`, `TMUX_PANE`, and `TMUX_TMPDIR` from the child environment.
- [ ] Implement the tmux-only cross-user adapter; leave generic transports unchanged.
- [ ] Keep direct local/SSH behavior unchanged when `tmux_user` is absent.
- [ ] Run focused tests and commit normally.

### Task 3: Deployment boundary and security regression tests

**Files:**
- Create: `deploy/remote-observer-tmux-read.sudoers.example`
- Modify: `deploy/README.md`
- Modify: `USER_ACTIONS.md`
- Modify: `tests/test_integrated_surface.py`

**Interfaces:**
- Production helper path: `/usr/local/libexec/remote-observer-tmux-read`.
- Production sudoers grants `remote-observer` passwordless execution of only that helper as the configured user.

- [ ] Add regression tests that MCP schemas expose no `tmux_user`, `user`, `sudo`, `socket`, `command`, or helper path fields.
- [ ] Add documentation assertions covering root ownership, non-writable helper, `visudo -cf`, and exact-user sudoers setup.
- [ ] Document a production example for `emma` using `tmux_user = "emma"` and the narrow helper rule; explicitly prohibit granting direct `/usr/bin/tmux`, shell, or general `sudo -u emma`.
- [ ] Run security/integration tests and commit normally.

### Task 4: Full verification and PR

**Files:**
- All changed files above.

- [ ] Run `python -m ruff check --no-cache .`.
- [ ] Run `python -m pytest -q`.
- [ ] Run `python scripts/smoke_stdio.py`.
- [ ] Inspect the final diff for any mutation path, generic privilege path, secrets, or unexpected MCP schema changes.
- [ ] Open a PR referencing #58; do not squash or merge.
- [ ] Wait for CI and report exact pass/fail counts.

## Production acceptance after merge

On `emma`, install the root-owned helper, validate/install the sudoers file, add `tmux_user = "emma"`, restart `tunnel-client.service`, then verify `sudo -u remote-observer sudo -n -u emma -- /usr/local/libexec/remote-observer-tmux-read sessions` and finally a real ChatGPT `tmux_sessions(host="emma")` call. Real production access is not PASS until that call succeeds.

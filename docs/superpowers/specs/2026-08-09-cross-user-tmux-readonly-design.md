# Cross-user tmux read-only observation design

Issue: #58
Date: 2026-08-09

## Context

The MCP daemon intentionally runs as the dedicated `remote-observer` OS user. Existing tmux observers execute fixed read-only commands (`list-sessions`, `list-windows`, `list-panes`, bounded `capture-pane`) through the normal host transport. On a local host this means tmux is executed as `remote-observer`, so it cannot reach another user's default tmux socket. In production the logical host `emma` is local, while the tmux server of interest belongs to OS user `emma`.

The existing MCP surface must remain closed-world: no arbitrary shell, sudo command, OS username, socket path, or tmux subcommand may become model-controlled.

## Goal

Allow a registered local host to opt into observing the tmux server of one fixed, gateway-local OS user while preserving the current read-only MCP contract.

Success means:

- `tmux_sessions`, `tmux_windows`, `tmux_panes`, and `tmux_capture` can observe the configured user's tmux server;
- the MCP input schemas remain unchanged;
- the target OS user comes only from root-managed host configuration;
- tmux mutation remains impossible through both the MCP observer and the privilege helper;
- existing local direct tmux behavior and SSH tmux behavior remain unchanged when no cross-user setting is configured;
- pane capture remains bounded to 1–500 lines and continues through normal output bounding/redaction/audit behavior.

## Approaches considered

### A. Share the tmux socket with `remote-observer`

Change socket/directory ownership or group permissions so `remote-observer` can connect directly.

Rejected. A tmux client socket is a control channel, not a read-only interface. Socket access would also permit `send-keys`, session/window mutation, and server control outside the MCP tool restrictions.

### B. Run `tunnel-client` / MCP as the interactive user

Run the observer stack as `emma` so the default socket is naturally accessible.

Rejected. This weakens process isolation for every observer capability, not just tmux, and gives the MCP daemon the interactive user's ambient filesystem and process permissions.

### C. Fixed read-only helper under constrained sudo

Recommended. Keep the MCP daemon as `remote-observer`. For a local host with a configured `tmux_user`, tmux observation invokes one root-owned helper as that exact registered user with `sudo -n -u <user>`. The helper accepts only semantic read operations and internally builds fixed `/usr/bin/tmux` argv.

This creates a narrow privilege boundary: sudo can launch only the helper, while the helper itself has no code path for mutation.

## Configuration

Add one optional host key:

```toml
[hosts.emma]
transport = "local"
tmux_user = "emma"
```

Rules:

- `tmux_user` is optional;
- it is accepted only for `transport = "local"` in v1;
- it must match the existing logical identifier grammar;
- it is gateway-local configuration and is never exposed as an MCP tool argument;
- no socket path, helper path, command, or sudo flags are configurable from MCP input.

When absent, tmux tools keep their current transport behavior.

## Helper contract

Install a root-owned executable at:

```text
/usr/local/libexec/remote-observer-tmux-read
```

Accepted invocations are semantic, not raw tmux argv:

```text
remote-observer-tmux-read sessions
remote-observer-tmux-read windows <session>
remote-observer-tmux-read panes <session-or-window>
remote-observer-tmux-read capture <pane> <lines>
```

The helper validates the same narrow target grammars as the MCP observer:

- session: `[A-Za-z0-9_.-]{1,64}`;
- window ID: `@[0-9]{1,10}`;
- pane ID: `%[0-9]{1,10}`;
- capture lines: integer 1–500.

It then executes only fixed `/usr/bin/tmux` commands:

- `list-sessions -F ...`;
- `list-windows -t ... -F ...`;
- `list-panes -t ... -F ...`;
- `capture-pane -p -t ... -S -N`.

The helper must not use a shell, `eval`, user-selected executable, or pass-through tmux subcommands. Any unexpected argument shape exits non-zero before tmux starts.

The helper runs with a minimal sanitized environment and does not inherit `TMUX`, `TMUX_PANE`, or `TMUX_TMPDIR` from the service process.

## Observer integration

The existing tmux parsing functions remain the source of response shaping. A small tmux-specific transport adapter selects between:

1. the existing host transport when `tmux_user` is absent; or
2. local execution of `sudo -n -u <registered-user> -- /usr/local/libexec/remote-observer-tmux-read ...` when `tmux_user` is configured.

The adapter only accepts the exact read-only `CommandSpec` shapes produced by the tmux observer and translates them to helper operations. If an unexpected tmux command reaches the adapter, it fails closed before `sudo` runs.

This preserves the existing observer API and keeps cross-user privilege handling isolated from generic transports.

## sudoers deployment

The deployment creates a dedicated sudoers rule for the exact helper and exact registered target user. For the current production host this is conceptually:

```text
remote-observer ALL=(emma) NOPASSWD: /usr/local/libexec/remote-observer-tmux-read *
```

The helper is root-owned and not writable by either `remote-observer` or `emma`. No `/bin/sh`, `/usr/bin/tmux`, general `sudo -u emma`, or passwordless sudo beyond this helper is granted.

A deploy example and validation commands will be documented. Production installation remains a user-controlled gateway action because it changes `/usr/local/libexec` and `/etc/sudoers.d`.

## Failure behavior

- helper missing or sudo denied: return a generic tmux observation failure without leaking sudoers details;
- target user's tmux server absent: preserve current empty-session / no-server semantics;
- invalid config: fail startup/config loading;
- unsupported SSH + `tmux_user`: fail config loading rather than silently ignoring it;
- invalid session/window/pane target: fail before helper/sudo execution.

## Tests

Add tests before implementation for:

1. config accepts `tmux_user` only on local hosts and rejects unsafe/SSH combinations;
2. tmux direct behavior is unchanged when `tmux_user` is absent;
3. cross-user adapter maps each existing read-only tmux command to the expected fixed helper invocation;
4. unexpected/mutating tmux argv is rejected before sudo transport execution;
5. helper accepts only sessions/windows/panes/capture and rejects `send-keys`, `run-shell`, `new-session`, `kill-*`, malformed targets, and out-of-range capture sizes;
6. MCP tool schemas remain read-only and expose no user/helper/sudo/socket/command field;
7. deployment docs contain the root-owned helper and narrow sudoers setup.

Deterministic CI does not require sudo or a live tmux server; helper and adapter behavior are tested through argv-level unit tests. Real `emma` cross-user tmux access is accepted only after the PR is merged/deployed and an actual ChatGPT `tmux_sessions` call succeeds.

## Scope

v1 supports exactly one optional cross-user tmux owner per local logical host. Multi-user tmux registries, arbitrary socket names, shared sockets, tmux mutation, and cross-user SSH elevation are out of scope.

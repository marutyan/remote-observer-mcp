# Remote Observer MCP Design

Date: 2026-08-08
Issue: #1
Status: proposed for user review

## 1. Purpose

`remote-observer-mcp` provides read-only observation of explicitly registered local and SSH-accessible hosts from a normal ChatGPT conversation. It is intended to reduce manual `ssh`, `journalctl`, `docker`, `git`, and GPU-status inspection without turning ChatGPT into an arbitrary remote shell.

The system separates observation from mutation. This repository owns only observation. Restart, deploy, backup, package installation, Git mutation, or other state-changing actions are explicitly outside this server and may later be handled by a separately reviewed action bridge.

## 2. Constraints

- No additional paid ChatGPT plan is assumed.
- The intended ChatGPT Pro path is custom MCP with read/fetch permissions; full write/modify MCP is not assumed available.
- Before any real Secure MCP Tunnel traffic is used, Phase 0 must confirm that the user's actual account can use the required tunnel path without unwanted additional charges. If unavoidable extra cost is discovered, tunnel integration stops until the architecture is reconsidered.
- The MCP server may run on a Mac during development and on a long-lived gateway in production.
- The first production gateway candidate is `emma`, but the implementation must not depend on that host name.
- Existing SSH configuration and host authentication are reused; the repository must not contain credentials, tokens, private keys, or literal secret values.
- Public MCP tools must not accept arbitrary shell commands, arbitrary remote hostnames, or arbitrary filesystem paths.
- A user-provided string must never become executable shell syntax through interpolation.

## 3. Non-goals

The initial system does not provide:

- arbitrary `execute`, `ssh`, `shell`, or script tools;
- service restart/stop/start;
- `git pull`, checkout, commit, push, or other Git mutation;
- Docker/container mutation;
- package installation or system configuration;
- arbitrary file reads;
- environment-variable inspection (`env`, `printenv`, `/proc/*/environ`);
- secret-management functions;
- continuous log streaming such as `tail -f`;
- direct management of Secure MCP Tunnel credentials.

## 4. Architecture

```text
ChatGPT Pro
    |
    | read-only MCP calls
    v
OpenAI Secure MCP Tunnel
    |
    | outbound tunnel connection
    v
Gateway
    |-- tunnel-client
    `-- remote-observer-mcp
           |
           | local observer or SSH
           v
      Registered hosts
```

The gateway is a deployment role, not a hard-coded machine. The same server must support:

1. development: Mac -> SSH hosts;
2. transition: long-lived gateway in tmux -> SSH hosts;
3. production: long-lived gateway under a service manager -> local host and SSH hosts.

The OpenAI `tunnel-client` supports a local stdio MCP command and requires a tunnel ID plus a runtime API key. The preferred initial integration is therefore to let `tunnel-client` spawn `remote-observer-mcp` as a stdio child instead of exposing an additional HTTP listener.

Relevant upstream references:

- OpenAI ChatGPT developer mode / MCP app documentation: `https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt`
- OpenAI Secure MCP Tunnel client: `https://github.com/openai/tunnel-client`

## 5. Trust boundaries

### 5.1 ChatGPT to MCP

ChatGPT supplies only typed semantic-tool arguments. Tool metadata should mark observation tools as read-only, but metadata is not treated as a security boundary.

### 5.2 MCP to resource registry

User-facing logical IDs are resolved through local configuration:

```text
host="gpu1" -> configured SSH alias
service="paperapp" -> configured service name
repo="rtdetr" -> configured repository path
```

Unknown IDs fail closed. The request may not supply an SSH hostname, IP address, username, service unit string, or filesystem path that bypasses the registry.

### 5.3 Gateway to SSH host

The gateway launches the system SSH client with an argv-based local process API so no local shell parses model-provided values. Baseline options should include:

- `BatchMode=yes`;
- `StrictHostKeyChecking=yes`;
- bounded `ConnectTimeout`;
- no password prompting;
- no implicit host-key acceptance.

OpenSSH commonly executes the remote command through the remote account's login shell. Therefore local argv construction alone is not a sufficient remote-command security boundary. Every value incorporated into a remote command must be either a fixed implementation constant or trusted configuration that passes strict observer-specific validation/encoding. Model-provided free-form text is never incorporated into remote command syntax.

Configured identifiers such as systemd units and container names use narrow character allowlists. Configured repository paths must be absolute and must satisfy a deliberately restricted safe-path grammar for v1; paths containing shell metacharacters, control characters, or unsupported whitespace are rejected rather than escaped permissively.

SSH credentials remain in the operator's existing SSH setup (`~/.ssh/config`, ssh-agent, known_hosts, or equivalent host-managed mechanisms). They are never copied into repository configuration.

### 5.4 Remote command execution

Every remote command is constructed by code from a fixed observer definition plus validated registry data. There is no API that accepts command text.

Example:

```text
service_status(host="emma", service="callbot")
    -> host registry lookup
    -> service registry lookup
    -> validate configured unit identifier
    -> fixed systemctl command form
    -> SSH runner
    -> bounded/sanitized result
```

## 6. Configuration model

Runtime configuration is stored outside the public repository, for example:

```text
~/.config/remote-observer-mcp/config.toml
```

The repository contains only `config.example.toml` with non-sensitive placeholders.

Conceptual schema:

```toml
[hosts.emma]
transport = "ssh"
ssh_alias = "emma"

[hosts.emma.services.callbot]
unit = "callbot.service"

[hosts.emma.repos.paperapp]
path = "/configured/path/to/paperapp"

[hosts.emma.containers.api]
name = "paperapp-api"

[hosts.gpu1]
transport = "ssh"
ssh_alias = "gpu1"
gpu = true
```

A local gateway target can use a separate transport such as `local`; observers must otherwise use the same typed interface.

Configuration validation must reject duplicate IDs, empty aliases, unsupported transports, relative repository paths where absolute paths are required, unsafe remote-command characters, and malformed observer-specific entries.

## 7. MCP tool surface

The v1 surface is intentionally small. Tool names are semantic and each tool has a bounded output.

### Discovery

- `list_hosts()`
  - returns logical host IDs and enabled observer capabilities;
  - does not expose credentials or hidden SSH configuration.
- `host_overview(host)`
  - aggregates a minimal system summary using enabled read-only observers.

### System

- `system_status(host)`
  - load/uptime and memory summary;
  - exact command set is OS-specific and fixed by implementation.
- `disk_usage(host)`
  - bounded filesystem usage summary;
  - no recursive filesystem traversal.
- `process_status(host, process)`
  - only for registered process IDs/names if process observation is enabled.

### systemd

- `service_status(host, service)`
  - service must be registered for the host.
- `service_logs(host, service, lines)`
  - service must be registered;
  - line count is clamped to a configured maximum;
  - no arbitrary journal query syntax.

### Docker

- `container_list(host)`
  - returns status only for containers registered for that host;
  - does not enumerate unregistered containers.
- `container_logs(host, container, lines)`
  - container must be registered;
  - bounded lines;
  - no `docker inspect` in v1 because it can expose environment values and secrets.

### Git

- `repo_status(host, repo)`
- `repo_diff(host, repo)`
- `repo_log(host, repo, count)`

`repo` is always a logical ID. Git commands run with the configured repository path. Secret-like paths are excluded from diff collection before output is returned. Git remote URLs are not exposed in v1.

### GPU

- `gpu_status(host)`
  - available only when enabled for the host;
  - initial NVIDIA implementation may use a fixed `nvidia-smi` query format;
  - absence of the binary/capability is reported as unsupported rather than triggering installation.

## 8. Observer layering

The implementation is divided into independently testable components:

```text
MCP adapter
    -> request validation
    -> host/resource registry
    -> observer
    -> command builder
    -> transport runner (local | SSH)
    -> output limiter
    -> redactor
    -> normalized MCP response
```

### MCP adapter

Owns protocol schemas and maps a tool call to one observer request. It does not construct shell command strings.

### Registry

Owns logical-ID resolution and capability checks. Unknown host/resource IDs fail before process execution.

### Observer

Owns semantic interpretation such as "service status" or "git status" and requests a fixed command from a command builder.

### Command builder

Returns a structured local command description and, for SSH transport, a remote command assembled only from fixed tokens plus strictly validated registry values. It never accepts a user-editable shell script.

### Transport runner

Executes locally or through SSH with timeout and output bounds. It captures exit status, stdout, stderr, and timeout state in a normalized result.

### Output policy

Applies truncation and best-effort redaction before any data crosses the MCP boundary.

## 9. Read-only enforcement

Read-only behavior is enforced in several layers:

1. only semantic observation tools are exposed;
2. no arbitrary-command tool exists;
3. hosts/resources come from allowlisted local configuration;
4. command builders contain fixed command forms;
5. local process execution does not invoke a local shell;
6. SSH remote arguments are fixed or strictly validated before they reach the remote login shell;
7. tests exercise injection-shaped model input and unsafe configuration values;
8. MCP read-only annotations are added as descriptive metadata, not relied on for enforcement.

Commands with possible side effects are excluded even when usually used diagnostically. If an observer cannot be implemented without a meaningful mutation risk, it is not included in this server.

## 10. Sensitive-data policy

Read-only access can still leak secrets. Prevention therefore precedes redaction.

The initial policy prohibits observers that read:

- `.env` and `.env.*`;
- private keys and common key/certificate secret files;
- credential/secret configuration paths;
- process environments;
- Docker inspect output;
- arbitrary files requested by the model;
- raw SSH configuration/keys;
- Git remote URLs.

`repo_diff` excludes configured secret-pattern paths and common sensitive names. The implementation also performs best-effort output redaction for common credential/token patterns. Redaction is a secondary defense and must not justify adding unsafe collection commands.

Service/container logs may themselves contain application secrets or personal data. Log observation is therefore opt-in per registered resource, is bounded, passes through redaction, and remains an acknowledged residual risk.

Any suspected secret detection should replace the value with a marker rather than returning a partial token.

## 11. Resource limits

Defaults are conservative and configurable within hard caps. Initial targets for implementation planning are:

- SSH connect timeout: approximately 5 s;
- ordinary command timeout: approximately 10 s;
- log command timeout: approximately 15 s;
- journal/container log lines: maximum 500;
- Git log entries: maximum 100;
- total returned command output: maximum 128 KiB per tool call.

These are design targets, not final constants. The implementation plan must specify hard caps and tests before code is accepted.

Long-running or streaming jobs are not part of v1.

## 12. Error model

Errors are normalized into categories that are useful to ChatGPT without disclosing secrets:

- `unknown_host`;
- `unknown_resource`;
- `unsupported_capability`;
- `connection_failed`;
- `host_key_failure`;
- `authentication_failed`;
- `permission_denied`;
- `timeout`;
- `command_failed`;
- `output_truncated` (success with truncation metadata where appropriate);
- `invalid_configuration`.

Responses should preserve enough sanitized stderr/diagnostic detail to explain operational failures but must not dump environment/configuration wholesale.

Observer-specific non-zero statuses that represent normal state (for example an inactive service) should be normalized as observation data rather than automatically treated as transport failure.

## 13. Audit logging

The gateway records a local metadata-only audit event for every tool call:

- timestamp;
- tool name;
- logical host/resource IDs;
- duration;
- success/error category;
- whether output was truncated or redacted.

Raw stdout/stderr, secrets, SSH arguments containing sensitive values, and full log/diff content are not written to the audit record. The audit mechanism must not become a second copy of observed sensitive data.

## 14. Deployment lifecycle

### Phase A: local development

Run on the Mac and observe one test SSH host. A local tmux session may be used to keep development processes alive across terminal disconnects; Mac sleep still suspends them.

### Phase B: gateway smoke deployment

Move the same configuration model and binary/package to a long-lived gateway. Use tmux only as a temporary operational wrapper while SSH reachability, tunnel connectivity, and behavior are validated.

Before the first real Secure MCP Tunnel tool call, confirm account availability and charging behavior. If the required path introduces unwanted additional cost, stop at this gate rather than silently consuming billable API usage.

### Phase C: managed service

Replace tmux with the host's service manager (expected systemd on a Linux gateway). The service must use automatic restart, explicit dependencies as needed, bounded permissions, and journal-based logs. Runtime API keys are provided through a host secret mechanism or environment reference and never committed.

The MCP implementation must not encode a dependency on tmux or systemd; these belong to deployment packaging.

## 15. Testing strategy

### Unit tests

- configuration parsing and validation;
- registry lookup and fail-closed behavior;
- command construction;
- input bounds;
- output truncation;
- redaction;
- audit metadata generation;
- normalized error mapping.

### Injection/security tests

Inputs resembling shell syntax must never execute:

```text
; rm -rf ...
$(...)
`...`
../../...
newline/control-character payloads
```

Unknown host, service, repository, process, or container IDs must fail before transport invocation. Unsafe values placed in local test configuration must fail validation before SSH invocation.

### Integration tests

Use a fake SSH executable/transport fixture for deterministic tests of:

- local SSH argv construction;
- remote-command encoding/validation;
- timeout;
- non-zero exits;
- connection/auth failures;
- stdout/stderr limits;
- malformed output.

No real SSH host is required for the standard test suite.

### MCP contract tests

Verify:

- `tools/list` surface;
- input schemas;
- tool dispatch;
- read-only metadata;
- stable normalized error responses.

### Smoke tests

After unit/integration tests pass:

1. local observer only;
2. one disposable/approved SSH target;
3. after the no-extra-cost gate passes, Secure MCP Tunnel discovery and one benign tool call;
4. gateway relocation smoke test.

Real-host smoke tests are never represented as completed unless their command/environment/output is actually observed.

## 16. Implementation phases

Implementation work should be split into numbered Issues/PRs so each PR has one reviewable outcome. After this design is approved, the implementation plan should decompose at least the following outcomes:

1. project scaffold, protocol choice, CI, and test harness;
2. configuration + registry + transport abstraction;
3. MCP discovery and minimal system observers;
4. systemd observer;
5. Docker observer;
6. Git observer with sensitive-path filtering;
7. GPU observer;
8. security hardening and adversarial tests;
9. tunnel/local smoke setup;
10. gateway packaging and systemd deployment.

Independent observer implementations may proceed in parallel only after their shared registry/transport interfaces are fixed. Parallel branches must not modify the same shared contract without coordination.

No squash merge is to be used. Commits should remain logical review units and carry the owning Issue number.

## 17. User-decision batching

Implementation should continue through tasks that do not need local credentials, infrastructure access, destructive actions, installation, or policy changes. Items requiring user judgment or local interaction are collected rather than interrupting unrelated work.

Expected decision/interaction checkpoints include:

- implementation language and MCP SDK if objective comparison does not yield a clear default;
- confirmation that Secure MCP Tunnel access is available without unwanted additional charges;
- creation/provisioning of tunnel ID and runtime API key;
- approved SSH aliases/hosts and resource allowlists;
- approval before any package installation;
- first real-host SSH smoke test;
- production gateway credentials and service installation;
- merge approval for each reviewed PR.

These checkpoints must state exactly what the user needs to decide or run, with copy-pastable commands when local action is required. Secrets must not be requested in chat.

## 18. Open decisions after design approval

The following are intentionally deferred to the implementation plan or Phase 0 rather than guessed:

- implementation language and MCP SDK;
- exact command variants for Linux/macOS observers;
- final timeout/output cap constants;
- final secret-pattern list;
- whether `process_status` uses explicit per-resource registries or another equally narrow safe policy;
- availability and charging behavior of Secure MCP Tunnel for the user's actual account;
- final production gateway selection and its SSH reachability.

## 19. Acceptance criteria for Issue #1

Issue #1 is satisfied when this document is reviewed and agreed to as the design baseline, and when it clearly defines:

- goals and non-goals;
- gateway-independent architecture;
- semantic read-only tool surface;
- host/resource allowlisting;
- command/transport boundaries including SSH remote-shell handling;
- sensitive-data controls;
- timeout/output/error behavior;
- metadata-only audit behavior;
- test strategy;
- phased deployment and implementation;
- explicitly deferred user decisions and the no-extra-cost gate.

Implementation does not begin until the written design has been reviewed by the user.
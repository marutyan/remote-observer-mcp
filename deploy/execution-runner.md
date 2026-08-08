# Approval-gated execution runner

The Execution Bridge is separate from the MCP read server. It is enabled only after repository-side validation is green and the GitHub Environment / self-hosted runner are configured by the repository owner.

## Security topology

```text
manual workflow_dispatch(request_id)
  -> hosted preflight: schema + secret + digest validation only
  -> GitHub Environment: remote-execution
       -> required reviewer approval
  -> [self-hosted, remote-observer] runner
       -> remote-observer-exec
       -> registered local or SSH target
```

The approval must be attached to the job that owns the self-hosted runner. Gating only a harmless preflight job does not protect execution.

## Environment configuration

Create a GitHub Environment named `remote-execution` and configure at least one **required reviewer**. Where the GitHub plan/settings support it, prevent the initiator from approving their own deployment. Do not place runtime SSH credentials in committed request files.

## Runner configuration

Use a dedicated self-hosted runner with label `remote-observer`. Prefer a dedicated OS account. The account should have only the local files, SSH config and remote permissions intentionally needed for approved operations.

Do **not** add the runner account to `docker`, passwordless `sudo`, privileged groups, or broad production credentials merely to make the bridge convenient. If a reviewed R4 action needs a privilege, grant the narrowest OS-level capability separately and document it.

Expected gateway files remain outside the repository:

- `/etc/remote-observer-mcp/config.toml`
- the runner service's SSH config/known_hosts/private credential files

## Acceptance

Start with one benign R1 request such as the documented `python3 --version` example adapted to an actually registered host ID. Confirm:

1. hosted preflight validates and prints a digest;
2. the execution job waits for Environment approval before a self-hosted runner receives it;
3. rejecting approval produces no execution;
4. approving it executes exactly once and reports sanitized evidence;
5. no execution tool appears in MCP `tools/list`.

R4 shell acceptance is optional. If tested, inspect the exact committed script and request digest before approval.

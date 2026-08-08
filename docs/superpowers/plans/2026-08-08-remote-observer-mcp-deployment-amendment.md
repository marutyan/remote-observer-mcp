# Remote Observer MCP Deployment Amendment

Date: 2026-08-08
Issue: #25
Applies to: `2026-08-08-remote-observer-mcp-implementation.md`, Task 10

## Reason for amendment

The original implementation plan listed both `deploy/remote-observer-mcp.service` and `deploy/tunnel-client.service.example`. Current OpenAI `tunnel-client` documentation makes the stdio MCP lifecycle explicit: for a local stdio MCP profile, `tunnel-client` launches the configured MCP command as a child process and owns its stdin/stdout.

A separately managed `remote-observer-mcp.service` would therefore be the wrong production topology. A detached stdio server has no Tunnel client attached to its protocol streams and would introduce a second, conflicting supervisor.

This amendment **supersedes the standalone MCP service portions of Task 10**. Earlier Tasks 1-9 are unchanged.

## Corrected Task 10

### Files

- Create: `deploy/tunnel-client.service.example`
- Create: `deploy/README.md`
- Create: `USER_ACTIONS.md`
- Create: `tests/test_deployment.py`
- Modify: `README.md`
- Modify: `config.example.toml`

Do **not** create `deploy/remote-observer-mcp.service`.

### Runtime ownership

```text
systemd
  -> tunnel-client
       -> remote-observer-mcp (stdio child)
            -> local/SSH observer transports
```

`systemd` supervises `tunnel-client`; `tunnel-client` supervises the stdio MCP child. The MCP implementation remains unaware of tmux/systemd.

### Credential contract

- runtime daemon uses a Restricted key with `Tunnels Read + Use`;
- no Admin key is stored on the long-lived daemon;
- runtime key is referenced through `file:/etc/remote-observer-mcp/runtime-api-key`;
- no tunnel ID, API key, SSH private key, or other literal credential is committed;
- SSH credentials remain gateway-local host state.

### Deployment sequence

1. deterministic package/stdio MCP tests;
2. no-extra-cost/account availability gate;
3. create Tunnel and restricted runtime key;
4. create a `sample_mcp_stdio_local` profile whose MCP command is `remote-observer-mcp`;
5. validate approved SSH aliases with `BatchMode=yes` and `StrictHostKeyChecking=yes`;
6. run `tunnel-client doctor --profile remote-observer --explain`;
7. optionally run a short foreground/tmux smoke;
8. install/enable the single `tunnel-client.service` unit;
9. verify health/readiness and ChatGPT `list_hosts`/one read-only observer call.

### Completion boundary

Repository implementation is complete when deterministic CI, stdio MCP smoke, deployment static tests, and this runbook are green. Real Tunnel/SSH/systemd/ChatGPT connectivity remains explicitly `NOT RUN` until the user executes `USER_ACTIONS.md` and supplies sanitized results.

## Upstream basis

- <https://github.com/openai/tunnel-client/blob/master/docs/configuration.md>
- <https://github.com/openai/tunnel-client/blob/master/docs/connectors.md>
- <https://github.com/openai/tunnel-client/blob/master/docs/permissions.md>

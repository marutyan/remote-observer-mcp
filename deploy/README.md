# Gateway deployment

This directory documents the production shape for `remote-observer-mcp`. It does not contain credentials or host-specific runtime configuration.

## Runtime ownership

`remote-observer-mcp` is a stdio MCP server. In production, OpenAI `tunnel-client` owns the MCP child process and its stdin/stdout lifecycle:

```text
ChatGPT
  -> Secure MCP Tunnel
  -> tunnel-client (systemd service)
  -> remote-observer-mcp (stdio child)
  -> local or SSH transport
  -> registered hosts/resources
```

Do **not** run a second standalone `remote-observer-mcp.service`. A detached stdio server has no MCP client on its stdin/stdout and creates an unnecessary second supervisor.

The checked-in `tunnel-client.service.example` therefore supervises only `tunnel-client`; the selected tunnel profile contains the MCP command that `tunnel-client` spawns.

## Upstream contracts

The deployment follows the current OpenAI `tunnel-client` documentation:

- configuration and `init`/`doctor`/`run`: <https://github.com/openai/tunnel-client/blob/master/docs/configuration.md>
- ChatGPT connector flow: <https://github.com/openai/tunnel-client/blob/master/docs/connectors.md>
- tunnel/runtime-key permissions: <https://github.com/openai/tunnel-client/blob/master/docs/permissions.md>

Re-check these documents before a real deployment because `tunnel-client` is independently versioned and its CLI may change.

## Files on the gateway

The examples assume this layout:

```text
/opt/remote-observer-mcp/
  .venv/bin/remote-observer-mcp

/etc/remote-observer-mcp/
  config.toml
  runtime-api-key

/var/lib/remote-observer-mcp/
  .config/tunnel-client/remote-observer.yaml
  .ssh/
    config
    known_hosts
    # dedicated read-only-observer SSH credentials when remote hosts are enabled

/run/remote-observer-mcp/
  health.url
```

`/var/lib/remote-observer-mcp` is the service user's HOME. SSH credentials are host-managed state, not repository files. Prefer dedicated credentials whose remote authorization is limited to the observation account and hosts needed by this service.

## Tunnel profile

Create the profile on the gateway as the `remote-observer` service user. The real tunnel ID is supplied only at deployment time:

```bash
sudo -u remote-observer env HOME=/var/lib/remote-observer-mcp \
  /usr/local/bin/tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile remote-observer \
  --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --mcp-command /opt/remote-observer-mcp/.venv/bin/remote-observer-mcp
```

Inspect the generated profile before continuing. It should identify the intended tunnel and stdio MCP command; it must not contain a long-lived admin credential.

The MCP child inherits `REMOTE_OBSERVER_CONFIG=/etc/remote-observer-mcp/config.toml` from the systemd service environment.

## Runtime credential

Use a **Restricted** OpenAI API key with **Tunnels Read + Use** for the runtime process. The daemon does not need tunnel create/update/delete permission and should not use an Admin key.

The example systemd unit references the runtime key through the tunnel client's supported file-backed secret reference:

```text
--control-plane.api-key=file:/etc/remote-observer-mcp/runtime-api-key
```

Create that file locally on the gateway with owner/group permissions that let only root and the `remote-observer` service account read it. Do not commit the file, paste the value into GitHub, or paste it into chat.

## Preflight

Before starting the managed service, validate the local MCP package without real infrastructure:

```bash
cd /opt/remote-observer-mcp
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_stdio.py
```

Then validate SSH host-key/authentication behavior for each approved alias using only a benign read-only command:

```bash
sudo -u remote-observer env HOME=/var/lib/remote-observer-mcp \
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes APPROVED_ALIAS true
```

Finally run the tunnel client's diagnostic command. This step reaches the Tunnel control plane, so execute it only after the no-extra-cost gate in `USER_ACTIONS.md` has been cleared:

```bash
sudo -u remote-observer env \
  HOME=/var/lib/remote-observer-mcp \
  REMOTE_OBSERVER_CONFIG=/etc/remote-observer-mcp/config.toml \
  /usr/local/bin/tunnel-client doctor \
  --profile remote-observer \
  --control-plane.api-key=file:/etc/remote-observer-mcp/runtime-api-key \
  --explain
```

`doctor` checks profile syntax, MCP connectivity, authentication, DNS/network reachability, tunnel access, and WebSocket readiness according to the upstream client documentation.

## Transition sequence

### 1. Mac/manual development

Run the MCP server locally with the deterministic stdio smoke. For a manual Tunnel experiment, use a temporary profile and runtime key only after the no-extra-cost gate is cleared.

### 2. Gateway/tmux smoke

Before installing systemd, a short foreground `tunnel-client run --profile remote-observer ...` may be kept inside tmux to validate that the gateway remains available across the operator's SSH disconnect. tmux is only an operational wrapper; it is not part of the MCP implementation.

A tmux session does not solve host shutdown or OS reboot. Do not treat it as the production supervisor.

### 3. Gateway/systemd

Copy `deploy/tunnel-client.service.example` to `/etc/systemd/system/tunnel-client.service`, review paths/user/hardening against the actual gateway, then enable it only after the user-action gates have passed.

The service exposes a local health endpoint on `127.0.0.1:18080` and writes the resolved health URL under `/run/remote-observer-mcp/health.url`. Use the tunnel client's documented `/healthz`, `/readyz`, and `/stats` endpoints for local diagnostics.

## Failure policy

- Tunnel/account/cost uncertainty: stop before network traffic.
- SSH unknown host key: fail; do not auto-accept a new key.
- SSH authentication failure: fail; do not fall back to password prompting.
- Missing Docker/Git/GPU/systemd permissions: report unsupported/permission failure; do not install or elevate automatically.
- MCP child crash: `tunnel-client` owns the runtime child lifecycle; systemd restarts the tunnel supervisor on failure.
- Real-host tests remain `NOT RUN` until their exact command/environment/result is observed.

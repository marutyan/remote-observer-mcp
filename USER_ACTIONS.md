# User action batch

このファイルは、deterministicな実装・テストだけでは完了できない実環境操作を一箇所に集約する。上から順に実施し、各gateを通過するまでは次へ進まない。

**秘密情報をChatGPT、GitHub Issue/PR、execution request、shell historyへ貼らない。** Runtime API keyはroot管理かつservice groupだけが読めるfileへ、SSH private keyはservice user/runnerだけが読めるgateway-local fileへ直接保存する。

## 0. No-extra-cost gate

目的: Secure MCP Tunnelを使うことで、現在のChatGPT Pro以外に許容していない**追加費用**が発生しないことを確認する。

1. OpenAIの現在のChatGPT/MCP/Tunnel UI・公式ドキュメント・課金表示を確認する。
2. 実アカウントで必要なTunnel機能が利用可能であることを確認する。
3. 追加API従量課金その他の追加費用が必要、または費用有無を確認できない場合は **STOP**。Tunnel traffic、`doctor`、`run`は実行しない。

このgateはコードから安全に代行できない。確認結果だけを後で共有すればよく、credential自体は共有しない。

## 1. Tunnel と runtime key を用意する

OpenAIのTunnel管理画面・公式手順で対象Tunnelを作成し、そのTunnelだけをruntime利用するkeyを用意する。

- key type: **Restricted**
- permission: **Tunnels Read + Use**
- long-lived runtime daemonではAdmin keyを使用しない

取得したTunnel IDはprofile生成時だけ使う。Runtime API keyはchatへ貼らずgateway上のfileへ保存する。

## 2. Gateway service account と package を用意する

以下はUbuntu系gatewayを想定した例。実行前に対象hostが正しいことを確認する。

```bash
if ! id remote-observer >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /var/lib/remote-observer-mcp --shell /usr/sbin/nologin remote-observer
fi
sudo install -d -o root -g root -m 0755 /opt/remote-observer-mcp
sudo install -d -o root -g remote-observer -m 0750 /etc/remote-observer-mcp
```

OpenAIのTunnels管理画面または公式`tunnel-client`配布手順からgatewayに対応する`tunnel-client`を取得し、実体/version/sample contractを確認する。

```bash
/usr/local/bin/tunnel-client --version
/usr/local/bin/tunnel-client help quickstart
/usr/local/bin/tunnel-client profiles samples show sample_mcp_stdio_local
```

repositoryを`/opt/remote-observer-mcp`へ配置し、Python 3.12 virtual environmentへpackageをinstallする。実際のclone/update方法はgatewayの既存運用に合わせる。

```bash
cd /opt/remote-observer-mcp
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_stdio.py
```

## 3. Runtime config と runtime key を配置する

`config.example.toml`を参考にgateway上だけに実configを作る。使いたいhost/resource/workspaceをlogical IDで登録する。

```bash
sudo install -o root -g remote-observer -m 0640 config.example.toml /etc/remote-observer-mcp/config.toml
sudo install -o root -g remote-observer -m 0640 /dev/null /etc/remote-observer-mcp/runtime-api-key
sudoedit /etc/remote-observer-mcp/config.toml
sudoedit /etc/remote-observer-mcp/runtime-api-key
```

`runtime-api-key`にはStep 1のRestricted runtime keyだけを保存する。ownerはroot、groupは`remote-observer`、modeは`0640`を維持し、値をcommand-line argumentへ直接書かない。

Workspace observerを使う場合は、実pathをChatGPTへ渡す代わりに例えば次のようなlogical IDを登録する。

```toml
[workspaces.example]
host = "remote"
root = "/srv/example"
secret_patterns = ["private/**"]
compose = false
```

## 4. SSH aliases と known_hosts を準備する

remote hostを使う場合だけ実施する。MCPとExecution Bridgeはregistered SSH aliasを使うため、専用HOMEに必要なaliasとknown_hostsを用意する。

```bash
sudo install -d -o remote-observer -g remote-observer -m 0700 /var/lib/remote-observer-mcp/.ssh
sudo -u remote-observer env HOME=/var/lib/remote-observer-mcp \
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes APPROVED_ALIAS true
```

初回接続で未知のhost keyが出た場合は自動受理しない。fingerprintを別経路で照合してからknown_hostsへ登録する。

可能なら専用SSH credentialを使い、remote側の権限をobserverに必要なread-only範囲へ限定する。private keyの値はrepositoryやchatへ貼らない。

## 5. Tunnel profile を生成する

Tunnel IDを一時的なshell variableへ入れ、service userのHOMEへprofileを生成する。

上流CLI contractのcanonical形は `tunnel-client init --sample sample_mcp_stdio_local --profile remote-observer --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" --mcp-command /opt/remote-observer-mcp/.venv/bin/remote-observer-mcp`。

実際にはservice userとして実行する。

```bash
sudo -u remote-observer env HOME=/var/lib/remote-observer-mcp CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" \
  /usr/local/bin/tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile remote-observer \
  --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --mcp-command /opt/remote-observer-mcp/.venv/bin/remote-observer-mcp
```

生成されたprofileを確認し、対象TunnelとMCP commandが正しいこと、Admin credentialや不要なheaderが入っていないことを確認する。

## 6. Tunnel preflight

**Step 0が未確認なら実行しない。**

```bash
sudo -u remote-observer env \
  HOME=/var/lib/remote-observer-mcp \
  REMOTE_OBSERVER_CONFIG=/etc/remote-observer-mcp/config.toml \
  /usr/local/bin/tunnel-client doctor \
  --profile remote-observer \
  --control-plane.api-key=file:/etc/remote-observer-mcp/runtime-api-key \
  --explain
```

PASS判定ではprofile/MCP/authentication/DNS/network/tunnel access/WebSocket readinessの各結果を保存する。失敗を無視してsystemdへ進まない。

## 7. tmux で短時間read-lane smokeする

systemd install前に必要な場合だけ、foreground `run`をtmux内で短時間試す。tmuxはSSH disconnectへの耐性確認にのみ使いproduction supervisorにはしない。

```bash
sudo -u remote-observer env \
  HOME=/var/lib/remote-observer-mcp \
  REMOTE_OBSERVER_CONFIG=/etc/remote-observer-mcp/config.toml \
  /usr/local/bin/tunnel-client run \
  --profile remote-observer \
  --control-plane.api-key=file:/etc/remote-observer-mcp/runtime-api-key \
  --health.listen-addr=127.0.0.1:18080 \
  --log.level=info \
  --log.format=json
```

ChatGPT側から最初に`list_hosts`、次に`list_workspaces`を呼ぶ。その後、`system_status`、`workspace_search`、`tmux_sessions`等の副作用なしtoolを1つずつ確認する。`execute`やrestart/deployはMCPには存在しない。

## 8. read laneをsystemdへ移行する

`deploy/tunnel-client.service.example`を実gatewayのpath/userに合わせてレビューしてから配置する。

```bash
sudo install -o root -g root -m 0644 deploy/tunnel-client.service.example /etc/systemd/system/tunnel-client.service
sudo systemctl daemon-reload
sudo systemctl enable --now tunnel-client.service
sudo systemctl status tunnel-client.service --no-pager
sudo journalctl -u tunnel-client.service -n 100 --no-pager
```

health endpointもlocalhostから確認する。

```bash
curl --fail --silent --show-error http://127.0.0.1:18080/healthz
curl --fail --silent --show-error http://127.0.0.1:18080/readyz
```

## 9. read lane受入結果を記録する

secret値を除き、以下だけ共有すればproduction acceptanceを続行できる。

- Step 0: 追加費用なしを確認できたか: PASS / BLOCKED
- `tunnel-client --version`: 実行結果
- `tunnel-client doctor`: PASS / FAIL（secretを除いた出力）
- approved SSH aliasごとのread-only smoke: PASS / FAIL
- `systemctl status`: active / failed
- `/healthz`: PASS / FAIL
- `/readyz`: PASS / FAIL
- ChatGPTから`list_hosts`: PASS / FAIL
- ChatGPTから`list_workspaces`: PASS / FAIL
- ChatGPTから任意のread-only observer 1件: PASS / FAIL

---

# Execution Bridge user actions

以下はread MCPとは独立した操作。**GitHub Environmentによる外部承認が設定されるまで任意コード実行を有効化しない。** 詳細は`deploy/execution-runner.md`を参照する。

## 10. GitHub Environment `remote-execution` を作る

Repository SettingsでEnvironment **`remote-execution`** を作成し、少なくとも1名の **required reviewer** を設定する。

可能なGitHub plan/settingsでは、workflowを開始した本人が自分でapprovalできない設定を有効にする。これが使えない場合でも、任意shell/R4は別人または明示的な追加レビューを運用ルールにする。

Environmentにcommandやscriptをsecretとして保存しない。必要なcredentialはrunner/gateway側の既存least-privilege credentialを使用する。

## 11. 専用self-hosted runnerを準備する

GitHub Actionsの専用 **self-hosted** runnerをgatewayへ登録し、label **`remote-observer`** を付ける。

runner用OS accountはleast privilegeにする。便利さのためだけに以下を付与しない。

- passwordless sudo
- root login
- Docker socket / `docker` group
- production全体へ届くSSH credential
- broad cloud/API credential

承認済みR4で本当に必要な権限だけ、OS側でも別途狭く付与する。Execution Bridge自体はprivilege elevationを自動で追加しない。

runner accountから必要なregistered SSH aliasを使う場合も`BatchMode=yes` / `StrictHostKeyChecking=yes`でbenign smokeを先に行う。

## 12. benign R1 requestでapproval gateを受入確認する

最初から任意shellを試さない。`execution_requests/example.json`を参考に、実configに存在するlogical hostへ向けたsecret-freeなR1 `argv` requestを用意する。

requestにはcredentialを入れない。public repositoryではcommand/args/target/reason自体がpublic metadataになる点も確認する。機密性が必要ならprivate companion repositoryへ移す。

受入手順:

1. request JSONのexact diffをレビューする。
2. **Approved remote execution** workflowを`request_id`だけでmanual dispatchする。
3. hosted preflightがschema/secret checkとSHA-256 digestを出すことを確認する。
4. execution jobが`remote-execution` Environmentで**待機し、self-hosted runnerではまだ実行されない**ことを確認する。
5. まずapprovalをreject/cancelし、実行されないことを確認する。
6. 同等のbenign requestを再dispatchし、今度はrequired reviewerがapproveする。
7. `[self-hosted, remote-observer]` runnerで`remote-observer-exec`が1回だけ動き、sanitized evidenceを返すことを確認する。
8. MCP `tools/list`に`execute` / `shell` / `approved_execute`等が存在しないことを再確認する。

R4 shell受入は必須ではない。試す場合はcommitted requestのexact scriptとdigestをEnvironment approval直前に再確認する。

## 13. 最終production acceptance結果

以下をIssue #27へsecretを除いて記録する。

### Read lane

- No-extra-cost gate: PASS / BLOCKED
- Tunnel doctor: PASS / FAIL
- strict SSH smoke: PASS / FAIL
- tunnel-client systemd: active / failed
- health/readiness: PASS / FAIL
- ChatGPT `list_hosts`: PASS / FAIL
- ChatGPT generic read tool: PASS / FAIL

### Execution Bridge

- `remote-execution` Environment exists: PASS / FAIL
- required reviewer configured: PASS / FAIL
- `[self-hosted, remote-observer]` runner online: PASS / FAIL
- reject path caused no execution: PASS / FAIL
- benign approved R1 executed exactly once: PASS / FAIL
- request digest/evidence recorded without secrets: PASS / FAIL
- MCP surface still contains no execution tool: PASS / FAIL

ここまで実施するまでは、repositoryのdeterministic CIがGREENでもreal Tunnel/SSH/Environment/self-hosted executionをPASSとは扱わない。

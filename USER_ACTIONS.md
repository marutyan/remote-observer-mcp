# User action batch

このファイルは、deterministicな実装・テストだけでは完了できない実環境操作を一箇所に集約する。上から順に実施し、各gateを通過するまでは次へ進まない。

**秘密情報をChatGPT、GitHub Issue/PR、shell historyへ貼らない。** Runtime API keyはroot管理かつservice groupだけが読めるfileへ、SSH private keyはservice userだけが読めるgateway-local fileへ直接保存する。

## 0. No-extra-cost gate

目的: Secure MCP Tunnelを使うことで、現在のChatGPT Pro以外に許容していない追加費用が発生しないことを確認する。

1. OpenAIの現在のChatGPT/MCP/Tunnel UI・公式ドキュメント・課金表示を確認する。
2. 実アカウントで必要なTunnel機能が利用可能であることを確認する。
3. 追加API従量課金その他の追加費用が必要、または費用有無を確認できない場合は **STOP**。Tunnel traffic、`doctor`、`run`は実行しない。

このgateはコードから安全に代行できないため、確認結果だけを後で共有すればよい。credential自体は共有しない。

## 1. Tunnel と runtime key を用意する

OpenAIのTunnel管理画面・公式手順で対象Tunnelを作成し、そのTunnelだけをruntime利用するkeyを用意する。

- key type: **Restricted**
- permission: **Tunnels Read + Use**
- long-lived runtime daemonではAdmin keyを使用しない

取得したTunnel IDは後続profile生成時だけ使う。Runtime API keyはchatへ貼らず、gateway上のfileへ保存する。

## 2. Gateway service account と package を用意する

以下はUbuntu系gatewayを想定した例。実行前に対象hostが正しいことを確認する。

```bash
if ! id remote-observer >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /var/lib/remote-observer-mcp --shell /usr/sbin/nologin remote-observer
fi
sudo install -d -o root -g root -m 0755 /opt/remote-observer-mcp
sudo install -d -o root -g remote-observer -m 0750 /etc/remote-observer-mcp
```

OpenAIのTunnels管理画面または公式`tunnel-client`配布手順から、gatewayに対応する`tunnel-client`を取得する。repositoryのunit例は`/usr/local/bin/tunnel-client`を前提にするため、実体とversionを先に確認する。

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

`config.example.toml`を参考に、gateway上だけに実configを作る。

```bash
sudo install -o root -g remote-observer -m 0640 config.example.toml /etc/remote-observer-mcp/config.toml
sudo install -o root -g remote-observer -m 0640 /dev/null /etc/remote-observer-mcp/runtime-api-key
sudoedit /etc/remote-observer-mcp/config.toml
sudoedit /etc/remote-observer-mcp/runtime-api-key
```

`runtime-api-key`にはStep 1のRestricted runtime keyだけを保存する。ownerはroot、groupは`remote-observer`、modeは`0640`を維持し、値をcommand-line argumentへ直接書かない。

## 4. SSH aliases と known_hosts を準備する

remote hostを使う場合だけ実施する。MCPは`~/.ssh/config`のaliasを使うため、`remote-observer`専用HOMEに必要なaliasとknown_hostsを用意する。

```bash
sudo install -d -o remote-observer -g remote-observer -m 0700 /var/lib/remote-observer-mcp/.ssh
sudo -u remote-observer env HOME=/var/lib/remote-observer-mcp \
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes APPROVED_ALIAS true
```

初回接続で未知のhost keyが出た場合、自動受理しない。fingerprintを別経路で照合してからknown_hostsへ登録する。

可能なら専用SSH credentialを使い、remote側の権限をobserverに必要なread-only範囲へ限定する。private keyの値はこのrepositoryやchatへ貼らない。

## 5. Tunnel profile を生成する

Tunnel IDを一時的なshell variableへ入れ、service userのHOMEへprofileを生成する。以下の一行はupstream CLIのprofile生成contractを明示するための基準形である。

```bash
tunnel-client init --sample sample_mcp_stdio_local --profile remote-observer --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" --mcp-command /opt/remote-observer-mcp/.venv/bin/remote-observer-mcp
```

実際にはservice userとして実行する。

```bash
sudo -u remote-observer env HOME=/var/lib/remote-observer-mcp CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" \
  /usr/local/bin/tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile remote-observer \
  --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --mcp-command /opt/remote-observer-mcp/.venv/bin/remote-observer-mcp
```

生成された`/var/lib/remote-observer-mcp/.config/tunnel-client/remote-observer.yaml`を確認し、対象TunnelとMCP commandが正しいこと、Admin credentialや不要なheaderが入っていないことを確認する。

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

PASS判定では、profile/MCP/authentication/DNS/network/tunnel access/WebSocket readinessの各結果を保存する。失敗を無視してsystemdへ進まない。

## 7. tmux で短時間smokeする

systemd install前に必要な場合だけ、foreground `run`をtmux内で短時間試す。tmuxはSSH disconnectへの耐性を確認するためだけに使い、production supervisorにはしない。

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

ChatGPT側から最初に呼ぶtoolは`list_hosts`に限定する。その後、`system_status`等の副作用なしtoolを1つずつ確認する。restart/deploy等はこのMCPには存在しない。

## 8. systemd へ移行する

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

## 9. 最終受入結果を返す

後で以下だけ共有すれば、こちらでproduction acceptanceを続行できる。secret値は不要。

- Step 0: 追加費用なしを確認できたか: PASS / BLOCKED
- `tunnel-client --version`: 実行結果
- `tunnel-client doctor`: PASS / FAIL（secretを除いた出力）
- approved SSH aliasごとのread-only smoke: PASS / FAIL
- `systemctl status`: active / failed
- `/healthz`: PASS / FAIL
- `/readyz`: PASS / FAIL
- ChatGPTから`list_hosts`: PASS / FAIL
- ChatGPTから任意のread-only observer 1件: PASS / FAIL

# 运维、升级与恢复

受管实例默认根目录为 `/var/lib/v-ui`。实际服务命令取决于安装时检测到的 service manager。

## systemd

```sh
sudo systemctl status v-ui.service v-ui-http01.socket v-ui-http01.service
sudo journalctl -u v-ui.service -n 100 --no-pager
sudo systemctl restart v-ui.service
```

## OpenRC

```sh
sudo rc-service v-ui status
sudo rc-service v-ui-http01 status
sudo rc-service v-ui restart
```

OpenRC 的 HTTP-01 responder 只获得绑定 80 所需 capability；systemd 使用 socket activation。面板本身保持非 root。

## 活动版本运行时

```sh
ROOT=/var/lib/v-ui
ID="$(sudo python3 -c 'import json;print(json.load(open("/var/lib/v-ui/CURRENT.json"))["release_id"])')"
PY="$ROOT/releases/$ID/runtime/python/bin/python3"
APP="$ROOT/releases/$ID/payload"
```

不要再使用旧的 `venv/bin/python` 路径。

## 受管升级

必须使用与当前机器 target 一致、同一发布版本验收过的包：

```sh
sudo bash install.sh --bundle ./vui-linux-x86_64-gnu.zip --sha256 '<可信 SHA256>' \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --port 8443 --node-port 10443 --accept-terms --upgrade
```

流程：验证候选 → 停止自己的受管服务 → 私有停机备份 → 激活 → HTTPS 健康检查 → 恢复服务。失败会尝试恢复原配置与代码选择，不删除数据。

不要把 CPU/libc/service-manager 迁移当成普通 `--upgrade`。

HY2 的 UDP 声明不持久保存在安装配置中；如需升级时检查并确认相应规则，须再次显式追加例如 `--node-udp-port 10443 --node-udp-port 20443`。既有安装会跳过 UDP bind 探测并提示人工核对所有权，避免运行节点造成假冲突；升级不证明端口空闲，也不自动推断/创建节点。TCP/UDP 同号规则仍分别核对，详见[安装指南](INSTALLATION.md)。

## 防火墙变化

安装器只在列出精确端口/协议并获明确确认后修改 UFW/firewalld，不自动启用防火墙。HY2 的 QUIC 需要节点 UDP 通行，现有 TCP 规则不能代替；这不意味着应用 UDP 转发已验收。以后修改端口时，还要同步检查：

- 本机监听；
- UFW/firewalld 或自定义 nftables/iptables；
- 云安全组；
- 客户端/订阅地址。

V-UI 不自动删除旧规则，因为端口可能被别的服务复用。

## 管理员恢复

停止面板后，以 `v-ui` 用户和活动版本 `$PY` 执行：

```sh
sudo -u v-ui env VUI_DATA_DIR="$ROOT/data" PYTHONPATH="$APP" \
  "$PY" -B -m app.admin set-password my_admin
```

密码交互输入，不放 shell 历史。

## 备份 / 恢复

备份包含数据库、节点秘密、证书私钥和 CA 账号，是未加密敏感文件。执行前停止所有可能写数据的受管进程。

systemd 示例：

```sh
sudo systemctl stop v-ui.service v-ui-http01.service v-ui-http01.socket
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" backup "$ROOT/manual-backup.zip"
```

恢复会撤销旧管理会话和订阅令牌。出现 `RESTORE_PENDING.json` 时保持停机并执行 `recover-restore`，不要手工删除日志绕过检查。

证书故障不要通过关闭 TLS 验证绕过。更多见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

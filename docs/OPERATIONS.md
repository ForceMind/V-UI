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
ID='<填写本次安装输出的 release_id>'
# 从安装器 Installed 输出或 root 控制的 /etc/v-ui/service.json 选择已知已安装版本。
case "$ID" in ''|*[!A-Za-z0-9._-]*) printf '%s\n' '请填写有效 release_id'; exit 1;; esac
case "$ID" in [A-Za-z0-9]*) ;; *) exit 1;; esac
test "${#ID}" -le 101 || exit 1
PY="$ROOT/releases/$ID/runtime/python/bin/python3"
APP="$ROOT/releases/$ID/payload"
```

不要再使用旧的 `venv/bin/python` 路径。不要把配置文件作为 shell 脚本执行，也不要以 root 执行服务用户拥有的 `$PY`；下文均显式选择已有 `v-ui` 身份。此路径准备无需假定主机安装了 Python 或 jq。

安装输出和 service.json 是安装器记录；手动代码回滚后可能不是当前活动版本。先用这个已知安装版本打开下节菜单，选择 `2` 查看 CURRENT 的活动版本，再按相同格式更新 `ID`、`PY`、`APP`。后续管理员、备份/恢复等命令须使用确认后的活动版本；不要把旧安装记录当作实时选择。

## 只读终端菜单

包含该功能的安装包提供真实 payload 入口，不安装全局 `v-ui` 命令或新的 root 包装器。按上节填写已安装版本后，在交互终端执行并确认活动选择：

```sh
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" menu
```

自定义目录的非 root 手动安装由目录所有者执行相同 `$PY -B "$APP/scripts/deploy.py" --root "$ROOT" menu`，无需 `sudo`。目录必须已存在、无符号链接且权限为 `0700`。受管 `/var/lib/v-ui` 必须属于已有 `v-ui` 用户；菜单拒绝 root 或错误所有者，不会代替用户提权。

- `1` 查看服务状态：只查询固定的 V-UI systemd/OpenRC 服务。`active`、`inactive`、`failed`、`transitional`、`unknown`、`inaccessible`、`unavailable` 分别表示运行、未运行、失败、切换中、查询未确认、无查询权限、无对应信息/程序。缺少命令、总线错误、超时不当成停机。
- systemd 同时展示面板、HTTP-01 socket 和 responder。socket 为 `active/listening`、responder 为 `inactive` 时标记“按需响应已就绪”，这是正常 socket activation；服务快照不证明 HTTPS、证书或节点转发健康。
- `2` 显示活动/前一 release ID、运行时 target；仅关联到受管目录与专用用户后才读取面板 HTTPS 地址、service manager 和安装器就绪记录。就绪记录是历史安装结果，非实时健康。自定义实例不读取全局受管配置，也不猜测地址或查询无关主机服务。
- `3` 才调用既有完整校验器，读取整个活动 payload/runtime 的哈希并核对本地清单。首次画面、服务查询、元数据、帮助和退出都不做全量哈希、不创建目录、数据、锁或字节码。
- `h` 查看帮助；`0`、EOF 或 Ctrl-C 退出。非 TTY 输入/输出直接拒绝，不消费管道中的选项。超长选项安全退出，不把后缀当作下一次选择。

菜单只读，不启动/停止/重启服务、不升级/卸载、不修改防火墙或 SSH，也不备份/恢复。状态查询使用固定可信系统程序、固定服务名和最小环境，每个命令最多 3 秒、16 KiB 输出，无 shell/pager/自动 sudo；Ctrl-C 会清理正在查询的子进程。不会执行配置中的 `bootstrap_python`，也不会显示账号、邮箱、凭据、证书材料路径、原始 JSON 或命令错误输出。

CURRENT/READY 和受管 service.json 均限制 16 KiB，使用 nofollow、文件类型和所有权/权限检查；选择完整校验时才额外预检最多 4,000,000 字节的 MANIFEST 及 release/payload/runtime 目录边界。损坏、超限、链接或不可读元数据会明确显示不可用/拒绝校验。校验后再次核对活动选择；并发切换不报通过。本地清单比对不等于独立来源签名，同用户并发改动仍不能获得原子全树保证。

安装包验收由现有 deployment 的 x86_64/glibc 和 portable 的 ARM64/glibc、两种 musl 消费者运行真实 payload 菜单 PTY；真实 systemd 关联另由仅限空白临时 CI 主机的 oneclick 验收。四目标、真实服务与本地假元数据/查询测试分别记录，接入测试不预先表示准确提交已通过。

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

# 运维、升级与恢复

受管实例默认根目录为 `/var/lib/v-ui`。实际服务命令取决于安装时检测到的 service manager。

全局 `v-ui` 受信控制入口是单独、显式安装的候选组件，见[受管终端控制](MANAGED_OPERATIONS.md)。默认一键安装不会新增该入口；下文包内非 root 菜单继续可用。仓库内软件测试不等于已在你的主机安装或验证了新的 root 控制层。

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

## 终端运维菜单

包含该功能的安装包提供真实 payload 入口，不安装全局 `v-ui` 命令或新的 root 包装器。按上节填写已安装版本后，在交互终端执行并确认活动选择：

```sh
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" menu
```

自定义目录的非 root 手动安装由目录所有者执行相同 `$PY -B "$APP/scripts/deploy.py" --root "$ROOT" menu`，无需 `sudo`。目录必须已存在、无符号链接且权限为 `0700`。受管 `/var/lib/v-ui` 必须属于已有 `v-ui` 用户；菜单拒绝 root 或错误所有者，不会代替用户提权。

- `1` 查看服务状态：只查询固定的 V-UI systemd/OpenRC 服务。`active`、`inactive`、`failed`、`transitional`、`unknown`、`inaccessible`、`unavailable` 分别表示运行、未运行、失败、切换中、查询未确认、无查询权限、无对应信息/程序。缺少命令、总线错误、超时不当成停机。
- systemd 同时展示面板、HTTP-01 socket 和 responder。socket 为 `active/listening`、responder 为 `inactive` 时标记“按需响应已就绪”，这是正常 socket activation；服务快照不证明 HTTPS、证书或节点转发健康。
- `2` 显示活动/前一 release ID、运行时 target；仅关联到受管目录与专用用户后才读取面板 HTTPS 地址、service manager 和安装器就绪记录。就绪记录是历史安装结果，非实时健康。自定义实例不读取全局受管配置，也不猜测地址或查询无关主机服务。
- `3` 调用既有活动版本完整校验器，读取整个活动 payload/runtime 的哈希并核对本地清单。首次画面、服务查询、元数据、帮助和退出都不做全量哈希、不创建目录、数据、锁或字节码。
- `4` 校验并准备本地升级包：输入本地包的字面路径及来自独立可信官方来源、已核验并接受的 SHA-256，再输入 `yes` 确认。路径与摘要不回显；不展开 shell、变量、通配符或 `~`，菜单本身不下载。每个输入都有长度上限。确认前任一步空输入、拒绝、EOF 或 Ctrl-C 均不开始准备。
- `h` 查看帮助；未进行候选准备时 `0`、EOF 或 Ctrl-C 退出。非 TTY 输入/输出直接拒绝，不消费管道中的选项。超长选项安全退出，不把后缀当作下一次选择。

选项 `1/2/3` 保持只读；`4` 只准备候选。菜单不启动/停止/重启服务、不激活升级/卸载、不修改防火墙或 SSH，也不备份/恢复。状态查询使用固定可信系统程序、固定服务名和最小环境，每个命令最多 3 秒、16 KiB 输出，无 shell/pager/自动 sudo；Ctrl-C 会清理正在查询的子进程。不会执行配置中的 `bootstrap_python`，也不会显示账号、邮箱、凭据、证书材料路径、原始 JSON 或命令错误输出。

选项 `4` 在确认后重新检查已有私有目录、活动元数据和本地普通文件边界，由正在运行菜单的安装包 Python 通过固定 argv 调用包内 `scripts/deploy.py stage`。它会创建 `.operation.lock`、临时目录与独立候选，不调用 activate，不取得 `.panel.lease`，不改写 CURRENT、旧 release 或数据；已有面板可以继续运行，但准备会额外占用 CPU、内存和磁盘；不发停服命令不等于任意资源条件下旧服务不受影响，也不构成 512 MiB 等低内存资格。另一个操作持有 `.operation.lock` 时拒绝，不排队或绕过锁。摘要错误也可能已创建操作锁，不能把失败描述为整个安装树完全未改动。

准备会执行候选包里的 Python、pip 与健康检查，权限与当前用户相同。SHA-256 只证明字节一致，不认证来源；`--no-index` 不是网络隔离或沙箱，候选代码仍可能联网、改变当前用户能访问的文件或逃离进程组。因此仅用于上述可信包，不用于检查未知包是否安全，也不保证隔离同用户恶意代码或并发改动。

处理期限为 600 秒，之后/正常 worker 退出后均执行有界进程组 TERM、5 秒宽限、KILL，并最多等待 5 秒回收直属 worker。信号处理器只记录 SIGINT/SIGHUP/SIGTERM；从启动到结果报告都延后退出，重复 Ctrl-C 不绕过清理。启动时父进程暂时屏蔽 INT/HUP，Popen 返回或失败后恢复原信号 mask；候选有意继承 INT/HUP 屏蔽，避免 fork 到 setsid 间的终端中断击中它，不额外屏蔽 TERM。此处理不提供沙箱。确认后的 EOF 不打断正在进行的工作，结果报告后退出。输出全部丢弃到 DEVNULL，避免原始候选错误、路径和子孙继承管道造成泄露或无期限 EOF 等待。用 WNOWAIT 保留已退出 leader 的 PID/PGID 身份，完成最后一次组信号后才回收；SIGCHLD 必须保持默认处置；发现 leader 已被外部回收（ECHILD）就不再向可能复用的 PGID 发信号，并报告未确认。不以僵尸组消失作为等待条件，也不宣称回收并非自己子进程的孙进程。不可中断内核状态和恶意 setsid 逃逸不在保证内。

只有期限内观察到 worker 正常 exit 0 且 CURRENT 原始字节和安装目录身份保持，才报告“候选已准备，尚未升级”。正常 worker 退出后的残留组清理不倒推为 stage 被强制终止。CURRENT 变化/不可读则明确“候选已准备，但活动选择保留未确认”，不自动回退。非零退出、超时、启动/清理错误都为“失败或结果未确认”，可能留下不完整候选；READY 在健康检查前写入，单独出现不代表成功。菜单不自动删除未知候选，也不继续激活；确认后退出不会再声称“未修改安装”。

CURRENT/READY 和受管 service.json 均限制 16 KiB，使用 nofollow、文件类型和所有权/权限检查；选择完整校验时才额外预检最多 4,000,000 字节的 MANIFEST 及 release/payload/runtime 目录边界。损坏、超限、链接或不可读元数据会明确显示不可用/拒绝校验。校验后再次核对活动选择；并发切换不报通过。本地清单比对不等于独立来源签名，同用户并发改动仍不能获得原子全树保证。

安装包验收由现有 deployment 的 x86_64/glibc 和 portable 的 ARM64/glibc、两种 musl 消费者运行真实 payload 菜单 PTY；stage 消费者使用当前构建包仅改 MANIFEST release ID 派生的隔离候选（其余源码、runtime/wheels 不变），持续请求旧 HTTPS 并核对旧 PID/starttime、CURRENT 原始字节、旧 release 和假数据；此派生测试包不是官方发布附件。真实 systemd 关联另由仅限空白临时 CI 主机的 oneclick 验收。四目标、真实服务与本地假元数据/查询测试分别记录，接入测试不预先表示准确提交已通过。

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
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" admin set-password my_admin
```

密码交互输入，不放 shell 历史。此入口取得操作锁和面板锁；不要同时运行绕过该入口的 `python -m app.admin`、直接 `main.py` 或其他写库脚本。

## 备份 / 恢复

备份包含数据库、节点秘密、证书私钥和 CA 账号，是未加密敏感文件。执行前停止所有可能写数据的受管进程。

systemd 示例：

```sh
sudo systemctl stop v-ui-http01.socket
sudo systemctl stop v-ui-http01.service v-ui.service
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" backup "$ROOT/manual-backup.zip"
```

性能候选的[私有备份缓存方案](LOW_RESOURCE_BACKUP_CACHE.md)要求目标目录所在文件系统支持硬链接，以便完整归档无覆盖发布；不支持时安全失败。若归档发布后的目录同步失败，命令仍报错，但会保留已完成文件，不自动删除或覆盖该路径。此行为只属于性能候选，未晋升为冻结 0.4.7 附件。

恢复会撤销旧管理会话和订阅令牌。出现 `RESTORE_PENDING.json` 时保持停机并执行 `recover-restore`，不要手工删除日志绕过检查。

证书故障不要通过关闭 TLS 验证绕过。更多见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

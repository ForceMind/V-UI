# V-UI

**面向个人自托管的代理管理面板：管理节点与证书，生成完整 Mihomo 分流订阅，用受管流程维护服务和数据。**

**v0.4.7** · FastAPI + SQLite · 中文网页后台 · Linux x86_64 / ARM64

[下载与发布](https://github.com/ForceMind/V-UI/releases) · [快速安装](#快速安装) · [协议范围](#协议与客户端支持) · [终端菜单](#终端运维菜单) · [完整文档](docs/README.md) · [报告问题](https://github.com/ForceMind/V-UI/issues)

V-UI 不依赖 Redis、常驻 Node.js 或在线订阅转换服务。运行包包含固定版本的便携 Python、离线 wheels、官方核心和本地前端资源；安装按明确版本选择，不追随 `latest`。

适合需要自行管理少量节点、证书和订阅的管理员。它不是多租户销售面板，也不替代主机防火墙、SSH 管理或网站托管。请仅在有权管理的系统和网络上使用。

## 功能概览

| 能力 | 本版提供 |
| --- | --- |
| 网页后台 | 管理员登录、退出、改密、会话失效与接口鉴权；没有默认密码 |
| 节点管理 | Xray / sing-box 独立状态与控制、候选配置校验、停止待应用及失败保护；公开导出严格受协议矩阵限制 |
| 图形化证书 | 单域名 HTTP-01 申请、测试/正式环境隔离、自动续期、面板热更新、TLS 节点绑定 |
| 分流与订阅 | ToClash 两种模式、40 项服务预设、自定义内网 DNS、CGNAT、规则预览、完整 Mihomo YAML |
| 订阅授权 | 按节点和格式授权的独立令牌，支持到期、轮换和撤销；令牌仅创建时显示 |
| 安装与运行 | 四个 Linux 目标、固定离线运行时、专用非 root 服务账号、systemd / OpenRC 安装检测 |
| 终端运维 | 包内服务用户菜单；另可显式安装全局受管菜单，执行 systemd 启停、候选切换、停机备份与恢复 |
| 数据保护 | 完整包校验、操作锁、恢复日志、代码回退与数据恢复分离；失败不会被写成“升级成功” |

### 协议与客户端支持

下面是 **已验证的公开组合**，不是“核心支持什么，面板就支持什么”。详细字段和排除项以[兼容矩阵](docs/COMPATIBILITY.md)为准。固定客户端验证使用 Mihomo 1.19.32 与 sing-box 1.14.2；服务端公开基线为 sing-box 1.14.2。

| 协议 | 传输 / 安全 | 已验证应用负载 | 主要约束 |
| --- | --- | --- | --- |
| VLESS | TCP + TLS | HTTP/TCP | 单 UUID、空 flow、明确 SNI、正常证书验证 |
| VLESS | WebSocket + TLS | HTTP/TCP | 严格 path，可选 Host；Host 不是服务端访问白名单 |
| VLESS | gRPC + TLS | HTTP/TCP | 固定 gRPC Lite、字面 service name、HTTP/2；错误 CA/SNI 可能表现为调用超时 |
| VLESS | direct TCP + REALITY / Vision | HTTP/TCP | 精确 `xtls-rprx-vision`、单 UUID / short ID、匹配 X25519、显式 SNI、Chrome |
| Trojan | TCP + TLS | HTTP/TCP | 单密码、明确 SNI、正常证书验证 |
| VMess | TCP + TLS | HTTP/TCP | 单 UUID、明确 SNI、正常证书验证 |
| Shadowsocks | AEAD | TCP / UDP | 仅 `aes-128-gcm`、`aes-256-gcm`、`chacha20-ietf-poly1305` |
| Hysteria2 | 原生 QUIC + TLS | HTTP/TCP | 单密码、默认传输参数；节点需要 UDP 通行 |
| TUIC v5 | 原生 QUIC + TLS | HTTP/TCP | 单 UUID / 密码、ALPN `h3`、零 RTT 关闭；节点需要 UDP 通行 |

- 提供对应的 URI/Base64、Mihomo YAML 和 sing-box JSON。**只有 Mihomo YAML 包含完整 ToClash 分流**；URI/Base64 仅承载节点，sing-box JSON 不是完整 ToClash 规则迁移。
- HY2 / TUIC 的 QUIC 使用 UDP，不代表应用 UDP 已验收。Mihomo TUIC adapter 自身保留 UDP 能力，不能声称已由一个无效配置项关闭。
- Xray 核心可以独立管理，但不能因此推导表中协议的 Xray 组合已具备同等公开导出资格。
- **XHTTP / HTTPUpgrade 公开导出仍阻断**。已有真实能力刻画和测试，不等于完整格式兼容或产品支持。
- 不支持的字段或组合会拒绝保存/公开导出；不会为生成“看似可用”的配置而静默丢字段或失败回退 DIRECT。

## 安装前检查

| 项目 | 要求与边界 |
| --- | --- |
| 架构与 libc | x86_64 或 ARM64；glibc 或 musl，必须选匹配的包 |
| 内核与ABI | 版本操作需要Linux pidfd等能力，运行时还需目标libc ABI兼容；四目标不承诺所有旧内核/libc版本 |
| 服务管理 | systemd 或 OpenRC；全局菜单的停启/数据事务仅支持满足受管校验的 systemd + cgroup v2 |
| 权限 | 系统安装由管理员明确执行；面板、核心和证书任务以专用低权限账号运行 |
| 域名与证书 | 准备自己控制的域名；HTTP-01 需要正确的 A/AAAA 和公网 TCP 80；也可提供已有证书 |
| 网络 | 安装阶段需要官方发布源；自动签发/续期需要 CA 网络；离线 wheels 不意味着整个服务离线 |
| 端口 | 默认 TCP 80（HTTP-01）、8443（HTTPS 面板）、10443（节点预检）；HY2/TUIC 的节点 UDP 需独立声明和放行 |
| 工具 | 在线下载示例需要 Bash、curl、sha256sum；系统 Python 3.9+ 用于安装引导，应用使用包内 Python 3.12 |
| 磁盘与内存 | 预留新旧版本、解包/校验快照、备份及数据恢复副本空间；不要按压缩包大小估算，详见下方资源说明 |

安装器按真实 CPU/libc/init 能力检测，不以发行版品牌承诺“所有 Linux 都支持”。不支持的CPU/init（例如runit/s6）会被拒绝。NixOS和声明式/只读系统不在支持范围，请勿运行此安装器；它没有保证提前识别所有此类环境。OpenRC HTTP-01 使用受限绑定低端口能力；systemd 使用 socket activation。详见[安装指南](docs/INSTALLATION.md)。

若 TCP 80 已被 Nginx、Apache、Caddy 或其他服务占用，请先自行规划；安装器不会停止原站点、接管反代或修改 SSH。它会逐项提示防火墙和云安全组条件，不自动启用防火墙。不要为省略人工核对而使用 `--assume-external-ports-open`。

## 快速安装

### 1. 固定版本，先核来源

先打开[官方 Releases](https://github.com/ForceMind/V-UI/releases)，确认 **v0.4.7 已公开且包含完整 19 项资产**。如果版本尚未公开、下载返回 404、文件不齐或摘要不匹配，停止安装；不要换成 `latest`、混用分支文件或改摘要跳过检查。

以下命令从固定版本下载安装入口并校验它；**请先审阅官方仓库、发行说明及安装脚本，确认这是你信任的软件来源，再执行管理员安装**。同站点下载的 SHA-256 是完整性检查，不是独立签名，也不能让未知脚本变得可信。

```sh
# 在一个新的空目录中执行；已存在同名目录时停止，避免混入旧文件。
set -eu
umask 077
mkdir vui-v0.4.7-install
cd vui-v0.4.7-install
BASE='https://github.com/ForceMind/V-UI/releases/download/v0.4.7'
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL \
  "$BASE/SHA256SUMS" -o SHA256SUMS
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL \
  "$BASE/install.sh" -o install.sh
awk '$2 == "install.sh" { print }' SHA256SUMS > install.sh.sha256
test "$(wc -l < install.sh.sha256)" -eq 1
sha256sum -c install.sh.sha256
INSTALL_SHA="$(awk '$2 == "install.sh" { print $1 }' install.sh.sha256)"
# 只把已接受的入口复制到root私有目录；执行前再次核对副本。
ROOT_BOOTSTRAP="$(sudo mktemp -d /root/vui-install.XXXXXXXX)"
sudo install -o root -g root -m 0600 ./install.sh "$ROOT_BOOTSTRAP/install.sh"
```

### 2. 交互式安装

```sh
# 沿用上一步同一终端中的ROOT_BOOTSTRAP和INSTALL_SHA。
run_vui_installer() {
  sudo /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root \
    /bin/bash -c '
      set -euo pipefail
      cd -- "$1"
      printf "%s  install.sh\n" "$2" | sha256sum -c -
      shift 2
      exec /bin/bash ./install.sh "$@"
    ' -- "$ROOT_BOOTSTRAP" "$INSTALL_SHA" "$@"
}
run_vui_installer --version v0.4.7
```

入口自动选择对应平台包，并校验固定安装 helper 与 ZIP 的摘要。按提示填写面板域名、联系邮箱，确认你控制域名、阅读并同意 CA 条款，再在终端设置管理员密码。默认管理员名称为 `admin`，可用 `--admin` 更改；密码为15–128字符。**没有预置密码，也不要把密码放进命令行或公开日志**。

默认面板地址为 `https://你的域名:8443/login`，以安装器最终 `Panel:` 输出为准。首次安装还会输出 `Installed:` 的 release ID，请留存用于排错和包内菜单定位。

需要 HY2 / TUIC 时，应按实际计划显式声明节点 UDP 端口，例如：

```sh
# 首次执行上方安装命令时，根据计划追加；无需先装一次再重复安装。
run_vui_installer --version v0.4.7 --node-udp-port 10443
```

UDP 声明可以重复，不会自动创建节点，也不持久保存为后续升级参数。TCP 与 UDP 同号仍是不同规则。新增规则会有独立确认；不要把端口预检当作公网连通保证。

### 本地完整套件与已有证书

完整本地安装需要同一版本的19项官方资产和已接受的 `SHA256SUMS` 整文件摘要。按[完整套件下载与root私有副本](docs/INSTALLATION.md#3-本地完整套件)下载、复核并复制后执行，不直接以root运行普通下载目录中的用户可写脚本。摘要必须来自已接受的来源，不能靠给未知包自己算一个值来建立信任。

四个目标为 `x86_64-gnu`、`aarch64-gnu`、`x86_64-musl`、`aarch64-musl`。不要把单独的应用 ZIP 当作包含全局控制组件的完整 kit。

已有证书可使用 `--cert /绝对路径/fullchain.pem --key /绝对路径/privkey.pem`；私有 CA 可明确提供 `--health-ca` 供安装健康检查。它不会自动创建托管续期关系。详细参数、数据目录、离线限制和平台检测见[安装指南](docs/INSTALLATION.md)。

## 首次登录与日常使用

1. 使用安装器输出的 HTTPS 域名登录。浏览器若提示证书不可信、过期或域名不匹配，先修复证书/域名/时间，不点击绕过警告继续使用。
2. 在“证书管理”检查正式证书；测试 CA 证书不能用于上线。新 TLS 节点可选择托管证书，或使用自己管理的有效材料。REALITY 不使用托管证书绑定。
3. 在“节点管理”创建兼容矩阵内的节点，检查核心实际运行/停止待应用状态。保存草稿不等于节点已生效。
4. 在“分流与订阅”配置规则，选择节点和格式，生成独立订阅令牌。复制一次显示的 URL，导入客户端并刷新。
5. 修改规则后，客户端刷新原订阅 URL 即可取得新配置。不要把管理员会话、订阅令牌、私钥或备份发到公共 Issue。

续期失败会保留旧材料，但不会延长旧证书的有效期；手动停止的核心不会因续期被擅自启动。安全中心中未支持的封禁入口显示说明，不会实施主机防火墙或 SSH 控制。详见[证书](docs/CERTIFICATES.md)、[配置与订阅](docs/CONFIGURATION.md)、[安全说明](SECURITY.md)。

## 终端运维菜单

**有两种入口，权限和用途不同。默认安装只提供包内服务用户菜单，不会自动生成全局 `v-ui` 命令。**

| 入口 | 调用身份 | 能做什么 |
| --- | --- | --- |
| 包内菜单 | 安装目录所属服务用户 | 服务状态、安装/活动版本信息、显式完整校验、准备可信本地候选；不启停或切换服务 |
| 全局受管菜单 | 管理员通过显式安装的 root-owned 入口 | systemd 启停/重启、停机备份、恢复、激活/代码回退、中断恢复；OpenRC 仅查询与纯候选准备 |

### 包内服务用户菜单

从安装器 `Installed:` 输出取得已知 release ID，按下面方式调用；不得把 `/etc/v-ui/service.json` 当 shell 脚本执行。若之后切换过版本，先在菜单选择 `2` 核实实际 CURRENT，再按[运维指南](docs/OPERATIONS.md#活动版本运行时)更新路径。

```sh
ROOT=/var/lib/v-ui
ID='<填写已安装的 release_id>'
case "$ID" in ''|*[!A-Za-z0-9._-]*) echo '请填写有效 release_id'; exit 1;; esac
case "$ID" in [A-Za-z0-9]*) ;; *) exit 1;; esac
test "${#ID}" -le 101 || exit 1
PY="$ROOT/releases/$ID/runtime/python/bin/python3"
APP="$ROOT/releases/$ID/payload"
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" menu
```

菜单需交互式终端；EOF/Ctrl-C 在未启动操作时安全退出，非 TTY 输入会拒绝。准备候选前要求可信本地包、可信 SHA-256 和明确确认；**准备成功不等于已升级**，不会切换 CURRENT。候选代码以同一服务用户执行，校验不是沙箱；超时/清理未确认可能留下不完整候选，不能仅凭 READY 文件认定成功。

**不要以 root 执行 `$PY` 或 payload 中的脚本。** 自定义非 root 安装由目录所有者直接运行，不使用 `sudo`。更多状态含义和600秒准备期限见[包内菜单合同](docs/OPERATIONS.md#终端运维菜单)。

### 可选的全局 `v-ui`

管理员须先从完整、独立可信的同版本 kit 显式安装控制组件，核对整个 `SHA256SUMS` 文件的可信摘要及 OS Python/受管安装边界。请按[全局菜单安装指南](docs/MANAGED_OPERATIONS.md#安装消费点与信任)操作，不能从服务用户可写的 payload 复制一个脚本就以 root 执行。

安装完成后：

```sh
sudo /usr/local/sbin/v-ui
```

菜单内依次提供 `1 状态 / 2 启动 / 3 停止 / 4 重启 / 5 备份 / 6 恢复 / 7 激活或代码回退 / 8 准备候选 / 9 中断恢复`；写操作逐次确认。这些是交互选项，不是 `v-ui backup` 这样的命令行子命令。它不新增 sudoers 权限、账号或常驻控制代理。systemd 的 HTTP-01 socket 正在监听而 responder 未运行时，可以是正常的按需激活；状态快照不能证明 HTTPS 或代理链路健康。

全局菜单的候选包/恢复归档必须由已有 `v-ui` 用户拥有、权限仅限属主、为普通单链接文件且祖先路径无符号链接；root 下载目录中的文件不能直接交给 worker。真实新控制层 root/systemd 资格来自记录中的 f509 临时 Ubuntu 测试；普通四平台包消费不等于四平台真实 root/init 全覆盖。相关限制见[受管控制合同](docs/MANAGED_OPERATIONS.md)。

## 升级、备份与恢复

- **升级前先备份并核来源。** 系统升级使用匹配 target 的新版本可信安装器和 `--upgrade`；不要把 CPU/libc/init 迁移当作普通升级。完整步骤见[受管升级](docs/OPERATIONS.md#受管升级)。
- **候选准备与切换分开。** 菜单 `stage` 只准备；`activate` 选择已准备版本并先备份。只切 CURRENT 不会更新 root-owned 控制组件、launcher、服务模板或安装记录；必要时按同版本完整 kit 显式刷新控制层。
- **备份需要所有 writers 静止。** 优先使用已安装全局受管菜单的停机事务；手动命令须遵循[备份/恢复指南](docs/OPERATIONS.md#备份--恢复)，先停 HTTP-01 socket，再停相关服务并确认无残留写进程。
- **备份未加密。** 它包含数据库、节点秘密、证书私钥和 CA 账号。妥善加密保管离机副本，限制访问，定期在隔离环境验证恢复。
- **代码回退不等于数据回滚。** 选择旧 release ID 不恢复旧数据库，也不恢复旧会话/订阅授权。保留原启动锚点 runtime，不要因为 CURRENT 已切换就删除旧目录。
- **恢复会撤销旧会话和订阅令牌。** 出现 `RESTORE_PENDING.json`、失败或未知状态时保持停机，按 `recover-restore` 流程处理；不要删除 journal、创建空库或强行重启掩盖故障。

默认数据位于 `/var/lib/v-ui/data`，版本位于 `/var/lib/v-ui/releases/<release_id>`；系统安装记录为 `/etc/v-ui/service.json`。请勿直接修改数据库、绕过操作锁或并发运行私自写数据的脚本。

## 资源要求与验证边界

项目持续降低安装、归档校验和运行开销，但**没有给出“512 MiB 整机长期稳定”的保证**。已有一核、零 swap 的短 cgroup 实际包测试，以及专门的安装/恢复资格；它们不包含全部宿主开销，也不等同连续24小时负载。

历史大数据场景（256 MiB 日志 / 1000 节点）的完整校验与恢复达到 **512 MiB 峰值、零余量**，出现限额事件但没有 OOM。160 MiB 空载 / 320 MiB 安装升级是尚未证成的工程预算，不是可依赖的最低配置。应为系统、代理流量、证书任务、候选准备和备份并发留出余量；不要仅凭面板空载 RSS 选择机器。

新旧运行时、私有校验快照、归档和恢复副本会同时占用磁盘。低内存环境避免把大快照放在 tmpfs；按自己的数据规模预留磁盘并监控空间。完整指标、原始失败与未验收项目见[低资源目标](docs/LOW_RESOURCE_TARGET.md)和[软件收口](docs/LOW_RESOURCE_CLOSURE_20261011.md)。

## 常见问题

| 问题 | 首先检查 |
| --- | --- |
| 找不到 `v-ui` 命令 | 默认安装不安装全局入口；使用包内菜单，或按可信 kit 指南显式安装 |
| 面板打不开 / 证书警告 | 安装输出的域名/端口、A/AAAA、证书链与时间、服务状态和云安全组；不关闭 TLS 校验 |
| TCP 80 被占用 | 确认原服务用途；不会自动停止网站或接管已有反代 |
| HY2/TUIC TCP 已放行仍不通 | 节点使用 UDP/QUIC；核对主机和云网络的精确 UDP 端口 |
| 包摘要不符 / receipt 漂移 | 停止操作，核同一版本和可信来源；不要改摘要、删 receipt 或绕过门禁 |
| 导出返回409 | 组合不受支持、节点停用或未准备好；不要清除未知字段伪造支持 |
| 服务状态 unknown / inaccessible | 查询失败或权限不足，不代表已停止；不要据此开始手工恢复 |
| 恢复后订阅失效 | 恢复会撤销旧授权，重新登录并创建/轮换订阅令牌 |

systemd 管理员可先读取：

```sh
sudo systemctl status v-ui.service v-ui-http01.socket v-ui-http01.service
sudo journalctl -u v-ui.service -n 100 --no-pager
```

OpenRC 使用 `sudo rc-service v-ui status` 与 `sudo rc-service v-ui-http01 status`。完整排查见[故障指南](docs/TROUBLESHOOTING.md)。提 Issue 时附精确版本/release ID、平台和脱敏错误；不上传完整订阅 URL、私钥、密码、数据库或原始备份。

## 文档导航

| 目标 | 入口 |
| --- | --- |
| 安装、端口、首次账号与证书 | [安装指南](docs/INSTALLATION.md) |
| 申请/续期/绑定证书 | [证书指南](docs/CERTIFICATES.md) |
| 节点参数、规则和订阅 | [配置指南](docs/CONFIGURATION.md) · [兼容矩阵](docs/COMPATIBILITY.md) |
| 菜单、升级、备份、恢复与回退 | [运维指南](docs/OPERATIONS.md) · [全局受管控制](docs/MANAGED_OPERATIONS.md) |
| 故障、安全和 API | [排错](docs/TROUBLESHOOTING.md) · [安全](SECURITY.md) · [API](docs/API.md) |
| 版本内容与正式资产 | [发布说明](docs/RELEASE_NOTES.md) · [变更日志](CHANGELOG.md) · [发布流程](docs/RELEASING.md) |
| 开发、完整路线与验收历史 | [贡献指南](CONTRIBUTING.md) · [路线图](ROADMAP.md) · [文档索引](docs/README.md) |

0.4.7 的网页后台、两类菜单、低资源软件修复和质量修复已进入主线，准确制品验收见[产品代码收口](docs/MAINLINE_OPERATIONS_CLOSURE_20261012.md)与 [PR #36](https://github.com/ForceMind/V-UI/pull/36)。后续提交必须有自己的准确验收；版本号、旧 CI 或文档描述不能替代对应 Release 的来源核对。[原 README 审计快照](docs/README_HISTORY_20261012.md)保留旧记录。

尚未支持的后续方向包括通用 UDP/DNS 扩围、DNS-01/通配符、核心自动升级/回滚、任意 YAML 导入合并、容器和 NixOS 等声明式部署。网站托管、SSH 管理和主机封禁不是本版后台能力。

## 开发与许可

开发环境使用 Python 3.12；Node.js 只用于开发验证。参见[贡献指南](CONTRIBUTING.md)运行默认单测、真实核心、浏览器及专门安装测试；环境 skip 不算相关场景通过。不要在生产机运行修改系统的测试开关。

V-UI 源码使用 [MIT License](LICENSE)，Copyright © 2026 ForceMind。Xray、sing-box、Certbot、ToClash 及其他依赖保留各自许可；项目 MIT 不替代第三方条款。来源、许可证与分发义务见 [third_party/NOTICE.md](third_party/NOTICE.md) 和发布包内的 provenance/license 文件。

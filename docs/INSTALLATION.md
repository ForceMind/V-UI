# 安装与首次运行

v0.4.9 纳入双开关、每入口 TCP 和 YAML 只读预览；历史版本与候选身份见[版本选择与验收交接](FROZEN_VERSION_TEST_HANDOFF.md)，本批准确状态见[一行安装计划](ONE_LINE_INSTALL_049.md)。

本批固定安装目标为已公开的 [v0.4.9](https://github.com/ForceMind/V-UI/releases/tag/v0.4.9)。仓库的短入口自动下载并校验该套件，在控制终端配置并安装全局 `v-ui`。固定摘要长命令仍保留在该 Release 中；旧标签与资产保持。引导入口与冻结应用套件分别记录源码身份。

**v0.4.7 安装器注意事项：** 在线路径的清理可能将成功或原失败状态改写为 1；便携 Python 引导还有临时目录归属与失败门禁缺口。若退出非零，保留输出并先核对控制器日志、活动版本和服务健康，不能一律视为成功，也不要盲目重跑。0.4.8 的修复和 shell 回归边界见[安装器修复说明](INSTALLER_EXIT_STATUS_048.md)，已发布资产不会原地替换。

## 一行安装与全局菜单

推荐在具备 Bash、curl 的 Linux 终端执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)
```

默认版本为 v0.4.9，也可追加 `--version v0.4.9`。目前只内置这一版本的可信摘要，其他版本会在安装前拒绝，不自动选用开发分支。入口可通过原有 sudo 策略提权，不依赖 sudo 保留过程替换的文件描述符。`setup.sh --help` 列出可转发的已有安装参数。

短入口先在控制终端读取并校验域名：空行、仅空白、URL、路径、端口、IP、通配符或非法标签会立即提示重输，不继续到邮箱配置，也不写入 V-UI 服务或安装记录。合法域名按既有规则去除首尾空白和末尾点、转换为 IDNA ASCII 并小写；显式 `--domain` 合法时免提示，无效时仍可交互纠正。每个物理输入行最多 4096 UTF-8 字节（含换行），超长整行会丢弃后重新提示；EOF 或 Ctrl-C 会取消。

校验使用固定系统 `/usr/bin/python3 -B -I -S`；缺失时通过已有包管理器准备系统 Python 3.9+，不替换解释器符号链接。该修复位于仓库短入口，不修改已冻结 v0.4.9 的安装器资产；直接调用该旧 Release 的长命令仍保留旧交互行为。

`main/setup.sh` 是可变仓库文件，执行前需要接受本项目仓库为软件来源；这与从独立渠道固定引导脚本摘要的信任方式不同。脚本内保留已发布安装器和整个 SHA256SUMS 的固定摘要，在 root 私有目录核验文件，再调用在线 `--with-global-cli --kit-sha256 ... --interactive` 流程。需要固定入口摘要时继续使用 Release 中原有命令。

过程替换语法中，入口下载失败可能只显示 curl 错误，而外层空 Bash 仍返回 0；不要仅凭该命令的退出码认定已安装。以实际安装完成输出、服务状态及 `v-ui` 可用性核对结果。引导脚本一旦开始执行，其下载、校验和安装失败仍会返回非零。
它会询问缺失的域名、邮箱、管理员名、面板和默认节点端口，密码仍通过控制终端读取两次；CA 条款和网络/防火墙确认由操作者完成。
应用与核心以专用非 root 用户运行；菜单使用固定 OS `/usr/bin/python3 -B -I -S`，预检现有能力，不替换系统解释器。
不支持的 OS 能力或未知 `v-ui` 占用会拒绝；受管 `--dry-run` 组合不执行。应用成功而菜单失败时会返回非零并保留可信 kit 供修复。
安装成功后在终端输入 `v-ui`。普通管理员使用已有 sudo 认证，不增加 sudoers 或 SUID；Root 控制层与服务用户 payload 保持既有边界。


## 1. 环境检测

本版一键安装器沿用已有能力检测模型，不按 Ubuntu 名称做白名单。启动后先读取系统信息并检测：

- 发行版 / `/etc/os-release`；
- CPU：x86_64 或 ARM64；
- libc：glibc 或 musl；
- init：systemd 或 OpenRC；
- 包管理器：apt、dnf/yum、zypper、pacman、apk、xbps、emerge；
- TCP 80、面板端口、默认节点 TCP 端口；显式声明时另检查节点 UDP/QUIC 端口；
- UFW、firewalld、自定义 nftables / iptables。

运行服务使用目标包自带的固定 CPython 3.12 和 hash-locked wheels，不要求系统自带 Python 3.12。系统 Python 只负责启动安装器；低于 3.9 时入口会先尝试包管理器，仍不足时使用固定 SHA-256 的便携 Python 引导。

当前目标包：

- `vui-linux-x86_64-gnu.zip`
- `vui-linux-aarch64-gnu.zip`
- `vui-linux-x86_64-musl.zip`
- `vui-linux-aarch64-musl.zip`

不支持的CPU/init会明确拒绝。NixOS和声明式/只读系统不在支持范围，请勿运行安装器；现有检测不保证提前识别这些环境。版本操作还要求Linux pidfd等内核能力及实际运行时libc ABI兼容，不承诺所有旧内核/libc版本均可运行。

## 2. 端口和防火墙

默认预检：

| TCP 端口 | 用途 |
| --- | --- |
| 80 | ACME HTTP-01 |
| 8443 | HTTPS 管理面板 |
| 10443 | 默认节点端口，可用 `--node-port` 修改 |

新安装在创建账号、写服务文件或修改防火墙前检查端口。TCP 80 与默认节点端口分别检查 IPv4 和可用的 IPv6；面板按 `--bind` 地址检查。systemd 与 OpenRC 使用相同的双栈预检。端口已有监听时安装停止，不结束原进程。预检不是端口预留，检查后新出现的监听仍可能导致服务启动失败。

UFW / firewalld 缺规则时按精确端口/协议列出（例如 `10443/tcp` 与 `10443/udp`），交互模式会询问是否现在开放；只有输入 `yes` 或操作者明确设置 `--open-firewall yes` 才修改。安装器不会自动启用尚未启用的主机防火墙。firewalld 通过 `--get-active-zones` 查询实际接口绑定，仅在一个明确的接口 zone、没有 source 绑定时自动处理，并同时写该 zone 的运行时和永久规则；不会把默认 zone 当作实际入口。多个 zone、source 绑定、空结果或查询失败时不猜测、不自动开放任何 zone，需人工核对实际入口并配置。确认期间 zone 发生变化也会停止修改。自定义 nftables / iptables 不自动覆盖；安装器列出端口并等待人工确认。云安全组无法由本机可靠修改，也会提示并等待确认。

自动化场景只有在操作者确实已经处理外部防火墙时才应使用 `--assume-external-ports-open`。这不是“检测到已开放”的意思。此标志也确认自定义/无法确定的本机防火墙已由操作者人工核对；`--open-firewall yes` 本身不会授权安装器猜测 firewalld zone。

80 被 Nginx / Apache / Caddy / 其他站点占用时，本版不会自动接管已有网站。

### Hysteria2 的显式 UDP/QUIC 声明

已验收 v0.4.5/HY2 与 v0.4.6/TUIC 沿用的安装器可接受可重复参数：

```sh
# 先完成下节可信kit/root私有副本准备；在首次安装调用中追加这些参数。
run_vui_local_installer --node-port 10443 --node-udp-port 10443 --node-udp-port 20443
```

- 每个 `--node-udp-port` 必须为 1024–65535；不传时 UDP 声明为空，不会根据默认 TCP 节点端口自动增加 UDP 规则。
- TCP 和 UDP 可以使用同一数字，但各有独立占用探测、规则和确认。面板/HTTP-01 保持 TCP，不能用它们的规则推导 HY2 已可达。
- 新安装在创建账号、写服务或改防火墙前，对声明 UDP 端口做 IPv4 及可用 IPv6 bind 探测；被占用则停止，不结束原进程。探测不是端口预留或外部连通证明。
- 既有安装/升级跳过 UDP bind 探测，避免与自己的运行节点冲突；会明确提示人工核对端口所有权。不能把升级路径写成“UDP 端口检查为空闲”。
- 主机规则、云安全组和自定义防火墙的确认均包括精确 `/tcp`、`/udp`。`--open-firewall yes` 不允许猜测 firewalld zone，也不授权自动启用防火墙；`--assume-external-ports-open` 只是操作者已处理外部/未知规则的声明。
- UDP 声明不写入持久安装配置；以后升级或重新运行如需检查这些端口，必须再次传入每个参数。安装器不会自动推断已有 HY2 节点。
- 该标志不会创建 HY2 节点、分配密码或启用应用 UDP。节点仍由面板另建；QUIC 使用 UDP 承载，但候选只验证 HTTP/TCP 应用负载，Mihomo `udp: false`、sing-box `network: tcp`。

UDP 安装行为和 HY2 集成仍需最终准确候选/主线门槛；固定二进制前置通过不表示真实安装或部署完成，见[HY2 契约](HYSTERIA2_045.md)。

## 3. 本地完整套件

以下为固定 v0.4.9 的手工完整套件入口，使用前先在[官方 Releases](https://github.com/ForceMind/V-UI/releases)确认版本公开、19项固定资产齐全，并接受该软件来源。以下使用Bash、curl、sha256sum和已有管理员sudo权限；不会让服务用户可写的下载目录成为root执行源码目录。`KIT_SHA` 是**整个SHA256SUMS文件的可信SHA-256**，应从已接受的发布记录/独立核验结果取得；摘要不是签名，不能以未知下载的自算值认证来源。请把示例target换为当前机器实际目标。

```sh
set -euo pipefail
umask 077
VERSION=v0.4.9
TARGET=x86_64-gnu
KIT_SHA='<填写已接受的 SHA256SUMS 整文件 SHA-256>'
case "$TARGET" in x86_64-gnu|aarch64-gnu|x86_64-musl|aarch64-musl) ;; *) exit 1;; esac
case "$KIT_SHA" in ''|*[!0-9a-f]*) echo '请先填写可信摘要'; exit 1;; esac
test "${#KIT_SHA}" -eq 64
KIT="$(mktemp -d)"
cd "$KIT"
ASSETS=(
  install.sh install_system.py platform_support.py firewall_support.py service_support.py
  install_ops_control.py system_launcher.py system_ops.py ops_worker.py release_tools.py v-ui
  vui-source.zip RELEASE.json RELEASE_NOTES.md SHA256SUMS
  vui-linux-x86_64-gnu.zip vui-linux-aarch64-gnu.zip
  vui-linux-x86_64-musl.zip vui-linux-aarch64-musl.zip
)
for file in "${ASSETS[@]}"; do
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location \
    --retry 2 --connect-timeout 15 --max-time 300 \
    "https://github.com/ForceMind/V-UI/releases/download/$VERSION/$file" -o "$file"
done
printf '%s  SHA256SUMS\n' "$KIT_SHA" | sha256sum -c -
sha256sum -c SHA256SUMS
ROOT_KIT="$(sudo mktemp -d /root/vui-kit.XXXXXXXX)"
for file in "${ASSETS[@]}"; do
  sudo install -o root -g root -m 0600 "$file" "$ROOT_KIT/$file"
done
# 复制后立即从root私有副本复核；此步只运行系统sha工具，不执行kit代码。
sudo /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root \
  /bin/bash -c '
    set -euo pipefail
    cd -- "$1"
    printf "%s  SHA256SUMS\n" "$2" | sha256sum -c -
    sha256sum -c SHA256SUMS
  ' -- "$ROOT_KIT" "$KIT_SHA"
run_vui_local_installer() {
  sudo /usr/bin/env -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root PYTHONDONTWRITEBYTECODE=1 \
    /bin/bash -c '
      set -euo pipefail
      cd -- "$1"
      printf "%s  SHA256SUMS\n" "$2" | sha256sum -c -
      sha256sum -c SHA256SUMS
      BUNDLE="vui-linux-$3.zip"
      SHA="$(awk -v file="$BUNDLE" '\''$2 == file {print $1}'\'' SHA256SUMS)"
      shift 3
      exec /bin/bash ./install.sh --bundle "./$BUNDLE" --sha256 "$SHA" "$@"
    ' -- "$ROOT_KIT" "$KIT_SHA" "$TARGET" "$@"
}
```

到此只完成套件下载、root私有副本和调用函数定义，尚未运行系统安装。准备安装全局控制层或升级已有实例时，不要再执行下面的首次安装调用。

首次系统安装时，再在同一终端明确运行：

```sh
run_vui_local_installer
```

19项资产包含项目自己的`vui-source.zip`；GitHub自动生成的“Source code” ZIP/TAR不替代它。单目标Actions kit不是最终四目标完整资产集合。临时下载目录与root私有副本都保留供核查，本示例不自动删除目录。

也可以在同一终端使用上方函数显式提供非秘密参数；**只有已阅读并同意CA条款时才传`--accept-terms`**：

```sh
run_vui_local_installer \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --port 8443 --node-port 10443 --accept-terms
```

这是替代上方交互安装的调用，不是要求首次成功后再执行第二次。管理员密码只从控制终端读取两次，要求15–128字符，不接受密码命令参数，也没有默认密码。默认管理员名为`admin`。成功后用安装器输出的`Panel: https://<域名>:<端口>/login`登录；证书警告应先排错，不绕过。

同时提供`--domain`和`--email`时，代码不会再弹出域名/邮箱/CA条款询问；自动签发必须事先同意后显式传`--accept-terms`。初次操作建议使用完整交互流程。

Python安装控制器的`--dry-run`会验证包并输出平台、防火墙/端口结果，不创建账号、服务或规则；仍读取完整包并可能提示域名/邮箱。外层`install.sh`进入控制器前可能安装引导工具/取得Python，在线`--version`还会下载文件，所以整个shell入口不是零写入或完全离线的dry-run。

## 4. 服务后端

### systemd

- `v-ui.service`：非 root HTTPS 面板、核心与证书任务。
- `v-ui-http01.socket`：systemd 持有公网 TCP 80。
- `v-ui-http01.service`：非 root challenge responder。

### OpenRC

- `v-ui`：通过 `supervise-daemon` 运行面板。
- `v-ui-http01`：仍以 `v-ui` 用户运行，仅获得绑定低端口所需 capability。

OpenRC 的 HTTP-01 分别绑定 IPv4 和 IPv6-only socket，IPv6-only 选项在绑定前设置；systemd 保持 socket activation。主面板不会因为 OpenRC 支持而变成 root 进程。

## 5. 数据与运行时

数据：`/var/lib/v-ui/data`

版本：`/var/lib/v-ui/releases/<release_id>`

活动版本自己的 Python：

`/var/lib/v-ui/releases/<release_id>/runtime/python/bin/python3`

root 只负责系统初始化和受管服务文件；应用、核心与 Certbot 由低权限服务用户运行。

## 6. 已有证书

可用 `--cert` + `--key` 跳过首次 ACME 申请。两者必须同时给出并是普通文件。私有 CA 可额外使用 `--health-ca` 做安装后的本机 TLS 健康检查。

已有证书不会自动变成 V-UI 托管续期关系；后续可在证书页面申请正式托管证书并绑定。

## 7. 官方 Release 一键安装

**本节为传统固定版本调用；推荐的一行命令还会显式安装全局 `v-ui`。** 只有在[官方 Releases](https://github.com/ForceMind/V-UI/releases)确认该版本正式公开且完整 19 项资产可用并核验后才可安装；未公开、缺文件、404或摘要不符时停止。不要混用 `latest`、分支文件与其他版本。安装入口的固定版本下载/校验步骤见[README快速安装](../README.md#快速安装)，入口本身也必须来自独立接受的可信来源：

```sh
# 按README完成固定入口校验/root私有副本及函数定义后：
run_vui_installer --version v0.4.9
```

脚本先检测 `x86_64/aarch64 + gnu/musl`，再从该明确版本下载对应目标包、安装控制器和 `SHA256SUMS`，逐个验证摘要后执行。不会下载 `latest`，也不会在发布时重新构建包。

HTTP-01 条件见 [CERTIFICATES.md](CERTIFICATES.md)，升级/备份见 [OPERATIONS.md](OPERATIONS.md)。

## 8. 菜单与首次维护

推荐一行受管安装会创建全局 `v-ui`；传统本地或未选受管模式的调用仍保留独立组件安装方式。包内服务用户菜单和全局受管菜单是不同入口，见[运维指南](OPERATIONS.md#终端运维菜单)与[显式全局组件安装](MANAGED_OPERATIONS.md#安装消费点与信任)。不要用 root 运行服务用户拥有的 portable Python/payload。

升级前保留离机备份和原启动锚点 runtime。正式 Release、源码验收、受管 root/init 资格与用户主机部署分别判断；安装或发布成功不产生整机/24小时稳定性保证。

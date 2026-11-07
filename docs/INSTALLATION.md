## v0.4.22 正式应用与短入口

v0.4.22已从准确main `558687a76cd29f4e4735284926eb05bf6664472e` 公开（PR137→dev、PR138→main）。候选/dev/main各自九组12 jobs、同源四目标、新装及公开14/15/16/17/19/20/21保数据升级全部通过；publish run42398527616晋升已验制品，Release437741132的tag/latest、19资产digest和真实小文件已匿名核验，旧21资产ID/大小/digest保持。浏览器未验证。手工固定版本使用`install.sh --version v0.4.22`；本提交将短入口绑定公开22的真实摘要，入口自身准确候选/dev/main及实际main URL另验。应用与入口source SHA不能混用，已有安装保留原配置和数据。[本批进度](MAINTENANCE_SHARE_0422.md)。下方21为历史。

## v0.4.21正式应用与短入口

v0.4.21已[公开](https://github.com/ForceMind/V-UI/releases/tag/v0.4.21)，正式应用源main2aa5e11、旧版资产保持；本次短入口正在独立切换并验收。下面的一行命令指向稳定main入口，目标以`setup.sh --help`为准；入口和发布应用的source SHA不同，不能混用。已有安装保留原配置/数据，选择升级才下载。构建与安装验收仅Actions，浏览器未验证；[进度](SIMPLE_OPERATIONS_PROGRESS.md)。下方“候选未公开”为历史。

## v0.4.21 候选说明

本分支的手工安装参数示例对应21候选，并非已公开下载地址。当前短入口`setup.sh`仍只信任公开20及其真实摘要；新构建和实际安装在Actions验收。用户未授权本批合并、公开或升级生产实例，原配置与数据保留。以下旧历史状态按各自记录解释。

## v0.4.20 发布准备：sing-box 启动兼容

当前分支准备20，尚未公开；手工--version示例随候选更新，不代表下载已存在。稳定版和短入口仍19。修复仅为受管panel服务增加网络接口监控必需AF_NETLINK，保留非root和其他安全限制，HTTP01服务不增加此地址族。安装及升级保留原账户、节点、证书、数据、代理/中转开关；不自动修改SSH或防火墙。准确验收及上线状态见[记录](SING_BOX_SYSTEMD_0420.md)。构建与实际安装只Actions，浏览器未验证。

## v0.4.19 安装与升级

[v0.4.19已公开](https://github.com/ForceMind/V-UI/releases/tag/v0.4.19)，应用源固定a2b9c188658f082b189e77d237787a96eb9cf921。本分支短入口使用公开19安装器和完整清单的真实可信摘要；实际main入口上线与验收状态见[本批记录](LONG_MAINTENANCE_0419.md)和承载PR。构建安装只Actions，浏览器未验证。

网页“远程维护”可选择180天，或运行sudo v-ui选择13维护授权，有效期输入180天/180d；默认仍30分钟、只读权限，按需选择其他权限。旧授权不自动延长，兑换后不重新计时，可随时撤销。已有安装可从菜单12在线升级，沿用原配置、账户和数据。

## v0.4.18 安装与升级（历史）

v0.4.18已公开，应用源固定为27862759d0d73a36fa1a7bdccf165d35a3b851b5。此分支短入口使用已匿名核验的18安装器和完整清单摘要，实际main入口的上线及安装资格见[统一记录](SIMPLE_NODE_WIZARD_PROGRESS.md)和承载PR。新界面按用途创建节点、选择已有证书后分享或供另一服务器使用；旧节点配置和数据保留。构建和安装验收仅GitHub Actions，浏览器未验证。

# 安装与首次运行

## 从 v0.4.17 起的远程维护与升级

临时远程维护与全局 `v-ui` 在线升级从17开始支持，以 `setup.sh --help` 核对当前短入口目标。[历史记录](REMOTE_MAINTENANCE_PROGRESS.md)和承载PR分别记录冻结发布源与后续引导入口资格。

安装17后运行 `sudo v-ui`：12在线升级（选择并明确确认后才下载），13生成/列举/撤销维护授权，默认只读。面板“远程维护与升级”也可生成限时链接，按需允许节点、分享、证书、中转、备份恢复或升级。已有安装沿用配置、账户和数据；维护任务由独立systemd组件执行。OpenRC保留原支持，新增远程系统任务暂不可用；详见[终端指南](MANAGED_OPERATIONS.md)及[权限契约](REMOTE_MAINTENANCE_CONTRACT.md)。

v0.4.9 纳入双开关、每入口 TCP 和 YAML 只读预览；历史版本与候选身份见[版本选择与验收交接](FROZEN_VERSION_TEST_HANDOFF.md)，本批准确状态见[一行安装计划](ONE_LINE_INSTALL_049.md)。

本页对应 [v0.4.19](https://github.com/ForceMind/V-UI/releases/tag/v0.4.19)；已有TLS证书自动绑定功能继续保留。短入口使用公开套件的真实可信摘要，旧标签与资产保持，引导入口与冻结应用套件分别记录源码身份。

**v0.4.7 安装器注意事项：** 在线路径的清理可能将成功或原失败状态改写为 1；便携 Python 引导还有临时目录归属与失败门禁缺口。若退出非零，保留输出并先核对控制器日志、活动版本和服务健康，不能一律视为成功，也不要盲目重跑。0.4.8 的修复和 shell 回归边界见[安装器修复说明](INSTALLER_EXIT_STATUS_048.md)，已发布资产不会原地替换。

> 启动阶段可能仍在校验程序和运行时；安装器会从请求启动起最多等待120秒，并以真实HTTPS/TLS/HTTP401确认就绪。失败时先提供本次启动脱敏诊断再恢复原配置；下载、备份和恢复耗时另计。请以最终安装完成信息为准。

## 一行安装与全局菜单

推荐在具备 Bash、curl 的 Linux 终端执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)
```

短入口固定已公开并核验摘要的v0.4.19，可追加 `--version=v0.4.19`。用 `setup.sh --help` 核对当前默认版本。入口仅信任已公开且显式内置摘要的版本，其他版本会在安装前拒绝，不自动选用开发分支。入口可通过原有 sudo 策略提权，不依赖 sudo 保留过程替换的文件描述符。`setup.sh --help` 列出可转发的已有安装参数。

v0.4.17完整套件已公开且19项资产齐全，使用前仍须核验。短入口检测到已有安装时，先显示本地管理 / 在线升级 / 继续或修复 / 退出菜单。选择 `1` 打开原全局管理菜单，`0` 或回车退出；这两项不下载发布套件，也不安装依赖。只有明确选择 `2` 或 `3` 才下载；`2` 沿用原配置升级，`3` 进入已验证安装器的原配置菜单（其内 `1` 沿用原值或续装、`2` 修改、`0` 或回车取消）。本地入口先核验 root 路径与 receipt 摘要，安装中断可续装，控制组件损坏不自动覆盖。修改时回车保留默认值，再确认变更；不需要先重新填写整套配置。已有管理员及密码、实际节点和订阅保持；证书模式不在此菜单切换。节点 TCP 端口仅作预检及防火墙声明，实际节点在面板管理。

短入口先在控制终端读取并校验域名：空行、仅空白、URL、路径、端口、IP、通配符或非法标签会立即提示重输，不继续到邮箱配置，也不写入 V-UI 服务或安装记录。合法域名按既有规则去除首尾空白和末尾点、转换为 IDNA ASCII 并小写；显式 `--domain` 合法时免提示，无效时仍可交互纠正。每个物理输入行最多 4096 UTF-8 字节（含换行），超长整行会丢弃后重新提示；EOF 或 Ctrl-C 会取消。

校验使用固定系统 `/usr/bin/python3 -B -I -S`；缺失时通过已有包管理器准备系统 Python 3.9+，不替换解释器符号链接。该修复位于仓库短入口，不修改已冻结 v0.4.9 的安装器资产；直接调用该旧 Release 的长命令仍保留旧交互行为。

`main/setup.sh` 是可变仓库文件，执行前需要接受本项目仓库为软件来源；这与从独立渠道固定引导脚本摘要的信任方式不同。脚本内保留已发布安装器和整个 SHA256SUMS 的固定摘要，在 root 私有目录核验文件，再调用在线 `--with-global-cli --kit-sha256 ... --interactive` 流程。需要固定入口摘要时继续使用 Release 中原有命令。

过程替换语法中，入口下载失败可能只显示 curl 错误，而外层空 Bash 仍返回 0；不要仅凭该命令的退出码认定已安装。以实际安装完成输出、服务状态及 `v-ui` 可用性核对结果。引导脚本一旦开始执行，其下载、校验和安装失败仍会返回非零。
新安装询问缺失的域名、邮箱、面板和默认节点端口；不再手填缺省用户名或密码。使用系统安全随机数生成用户名和强密码，只在当前控制终端交付：创建前显示待创建凭据，最终完成后汇总。请保存后登录面板，可在账号页面验证当前密码后修改用户名和密码。已有账号不会因升级、续装或修复被重置。首次 CA 条款由操作者明确同意，同一域名、production 环境已保存的有效授权可复用；更换域名仍须同意。
应用与核心以专用非 root 用户运行；菜单使用固定 OS `/usr/bin/python3 -B -I -S`，预检现有能力，不替换系统解释器。
不支持的 OS 能力或未知 `v-ui` 占用会拒绝；受管 `--dry-run` 组合不执行。应用成功而菜单失败时会返回非零并保留可信 kit 供修复。
需要保留的 CA 或配置变更确认接受大小写 `y/n/yes/no`，空回车默认否，其他回答原地重问；EOF/Ctrl-C 取消。网络/防火墙不再重复询问 y/n；自动检测结果不能证明云安全组已放行。
安装成功后在终端输入 `v-ui`。普通管理员使用已有 sudo 认证，不增加 sudoers 或 SUID；Root 控制层与服务用户 payload 保持既有边界。


## 1. 环境检测

本版一键安装器沿用已有能力检测模型，不按 Ubuntu 名称做白名单。启动后先读取系统信息并检测：

- 发行版 / `/etc/os-release`；
- CPU：x86_64 或 ARM64；
- libc：glibc 或 musl；
- init：systemd 或 OpenRC；
- 包管理器：apt、dnf/yum、zypper、pacman、apk、xbps、emerge；
- TCP80与面板实际监听；默认节点TCP端口只作声明；显式声明时检查UDP/QUIC端口；
- 主动网络诊断时识别UFW、firewalld及自定义规则，普通安装不全面扫描。

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

新安装在创建账号、写服务文件前检查实际需要监听的端口：TCP80做双栈探测，面板按 `--bind` 检查；已有监听则停止，不结束原进程。默认节点TCP端口仅为声明，不创建节点，不因该声明端口被占用阻断普通安装；dry-run会提供参考结果。预检不是端口预留，检查后新出现的监听仍可能导致服务启动失败。

普通安装默认不全面检测防火墙，不因未知规则或云入口重复提示、确认。真实ACME、TLS和HTTP401就绪检查仍保留，失败不能当作安装成功。单机自curl或回环HTTP成功都不是公网可达证明。

需排查时主动打开 `v-ui` 菜单10：只读查看本机规则、实际监听和未知云入口。菜单11列出精确端口/协议，明确确认后才对安全识别的已启用UFW/firewalld放行并回读；不自动启用防火墙，不猜zone，不修改SSH、云安全组或自定义规则。安装器显式 `--open-firewall yes` 也保留同等安全识别限制。自定义规则或云安全组需在实际入口处理。

`--assume-external-ports-open` 仅为旧脚本兼容参数，不提供公网可达证明，也不放宽证书或真实HTTPS检查。

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
- 主机规则检测与云安全组/自定义防火墙提示均包括精确 `/tcp`、`/udp`。`--open-firewall yes` 不允许猜测 firewalld zone，也不授权自动启用防火墙；`--assume-external-ports-open` 仅为兼容参数，不提供外部可达证明。
- UDP 声明不写入持久安装配置；以后升级或重新运行如需检查这些端口，必须再次传入每个参数。安装器不会自动推断已有 HY2 节点。
- 该标志不会创建 HY2 节点、分配密码或启用应用 UDP。节点仍由面板另建；QUIC 使用 UDP 承载，但候选只验证 HTTP/TCP 应用负载，Mihomo `udp: false`、sing-box `network: tcp`。

UDP 安装行为和 HY2 集成仍需最终准确候选/主线门槛；固定二进制前置通过不表示真实安装或部署完成，见[HY2 契约](HYSTERIA2_045.md)。

## 3. 本地完整套件

以下为 v0.4.16 的手工完整套件入口，使用前先在[官方 Releases](https://github.com/ForceMind/V-UI/releases)确认版本公开、19项固定资产齐全，并接受该软件来源。以下使用Bash、curl、sha256sum和已有管理员sudo权限；不会让服务用户可写的下载目录成为root执行源码目录。`KIT_SHA` 是**整个SHA256SUMS文件的可信SHA-256**，应从已接受的发布记录/独立核验结果取得；摘要不是签名，不能以未知下载的自算值认证来源。请把示例target换为当前机器实际目标。

```sh
set -euo pipefail
umask 077
VERSION=v0.4.16
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

这是替代上方交互安装的调用，不是要求首次成功后再执行第二次。新账号密码自动生成，不接受密码命令参数，不写入明文配置或环境变量，也没有固定默认密码。未传`--admin`时用户名随机生成，显式`--admin`只指定新账号名，不会覆盖已有账号。成功后用终端输出的`https://<域名>:<端口>/login`及已交付凭据登录；证书警告应先排错，不绕过。

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
run_vui_installer --version v0.4.22
```

脚本先检测 `x86_64/aarch64 + gnu/musl`，再从该明确版本下载对应目标包、安装控制器和 `SHA256SUMS`，逐个验证摘要后执行。不会下载 `latest`，也不会在发布时重新构建包。

HTTP-01 条件见 [CERTIFICATES.md](CERTIFICATES.md)，升级/备份见 [OPERATIONS.md](OPERATIONS.md)。

## 8. 菜单与首次维护

推荐一行受管安装会创建全局 `v-ui`；传统本地或未选受管模式的调用仍保留独立组件安装方式。包内服务用户菜单和全局受管菜单是不同入口，见[运维指南](OPERATIONS.md#终端运维菜单)与[显式全局组件安装](MANAGED_OPERATIONS.md#安装消费点与信任)。不要用 root 运行服务用户拥有的 portable Python/payload。

升级前保留离机备份和原启动锚点 runtime。正式 Release、源码验收、受管 root/init 资格与用户主机部署分别判断；安装或发布成功不产生整机/24小时稳定性保证。

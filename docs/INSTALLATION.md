# 安装与首次运行

当前公开稳定版为 **v0.4.23**。普通新用户使用下方无参数短入口；它固定核验已公开安装器和完整清单，已有安装先进入管理菜单。

已有实例先备份，升级保留原配置、管理员、节点、订阅与证书。完整包从[官方 v0.4.23 Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.23)取得，不混用分支文件、其他版本或不明下载站。

## 1. 一行安装

准备 Bash、curl 和 root 或已有 sudo 权限后，在服务器终端执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)
```

需要手动固定版本时，先从正式 Release 取得并核验 `install.sh`，再使用下列命令；这里的摘要对应 v0.4.23 的完整 `SHA256SUMS` 文件：

```bash
bash install.sh --version v0.4.23 --with-global-cli \
  --kit-sha256 61d6fba8f3d1c5fb6ae39a890db06e8b44e2afa165d1e13451d450a27e583a30 --interactive
```

完整安装器的校验参数不用于覆盖短入口的固定信任锚。v0.4.22 的公开套件及历史安装说明见[固定22说明](https://github.com/ForceMind/V-UI/blob/59fc1cce15df8da850dd0a13d58b4efe6879a6be/docs/INSTALLATION.md)。

入口只接受内置且已公开的受信版本摘要，不自动安装开发分支。`main/setup.sh` 本身是可变仓库文件，需要信任此仓库；需要独立固定入口摘要时，请使用 Release 内的说明。过程替换下载失败可能只打印 curl 错误，外层空 Bash 仍返回 0，不能只凭该短命令的退出码认定安装成功。

### 新服务器

1. 使用自己控制并解析到本服务器的域名。只填 DNS 域名，不带 `https://`、路径或端口，不填 IP、通配符或 GitHub 下载地址。无效输入会原地要求重输；国际化域名按 IDNA 规范化。
2. 按提示输入邮箱、面板端口和默认节点端口。默认面板 TCP 8443，节点 TCP 10443；声明节点端口不会创建节点。
3. 本人阅读并同意首次自动证书的 CA 条款。已有同域名、production 环境有效授权可复用，更换域名需重新确认。确认接受 `y/n/yes/no`，回车默认否。
4. 保存终端交付的随机管理员用户名和强密码。没有固定默认密码，密码不接受命令行参数；登录后可验证当前密码再修改账号。
5. 等待最终安装完成输出，按输出的 HTTPS 地址登录，再按[配置指南](CONFIGURATION.md)创建节点。

安装器从请求启动起最多等待 120 秒，验证真实 HTTPS、TLS 与 HTTP 401 就绪；下载和备份耗时另计。证书或就绪检查失败不会当作安装成功。保留完整退出状态和已脱敏输出，再按[故障排查](TROUBLESHOOTING.md)处理。

### 已有安装

| 选项 | 行为 |
| --- | --- |
| `1` 本地管理 | 打开已有全局菜单，不下载发布套件或安装依赖 |
| `2` 在线升级 | 明确选择后下载校验套件，沿用原配置和数据 |
| `3` 继续或修复 | 下载校验套件，显示原配置，选择续装或修改；修改时回车保留原值，最后确认 |
| `0` 或回车 | 退出，不下载套件、不更改安装 |

控制文件损坏或来源不可信时会拒绝，不覆盖未知文件。已有管理员及密码不会因升级、续装或修复被重置。证书模式不在此配置修改菜单中切换。

### 支持的环境

安装器检测 Linux、x86_64 / ARM64、glibc / musl、systemd / OpenRC、包管理器及实际内核能力，不按发行版名称承诺通用兼容。四目标包为 `vui-linux-{x86_64,aarch64}-{gnu,musl}.zip`。NixOS、声明式或只读系统不支持；旧内核或 libc ABI 可能无法运行，不要修改检测条件强行安装。

应用使用包内固定 CPython 3.12 和离线依赖。系统 Python 仅用于安装与受管控制；引导优先使用系统 Python 3.9+，必要时通过既有包管理器或固定摘要的便携引导准备，不替换系统解释器链接。完整受管系统维护限 systemd；OpenRC 的全局菜单目前仅支持状态查询和本地候选准备。

项目不承诺 512 MiB 整机长期运行。安装、代理负载、证书签发、升级与备份都需资源余量，详见[资源边界](LOW_RESOURCE_TARGET.md)。

## 2. 端口和防火墙

| 默认端口 | 用途 |
| --- | --- |
| TCP 80 | ACME HTTP-01 验证 |
| TCP 8443 | HTTPS 面板，可用 `--port` 修改 |
| TCP 10443 | 默认节点声明，可用 `--node-port` 修改；实际节点在面板创建 |

新安装在写账号及服务前探测 TCP 80 和面板实际监听；遇到占用会停止，不结束 Nginx、Apache、Caddy、容器或其他进程。默认节点 TCP 端口只作声明，不因占用阻断普通安装。所有预检都不是端口预留或公网可达证明。

普通安装不全面扫描防火墙。安装后需要排错时，用 `v-ui` 菜单 **10** 查看本机规则和监听；菜单 **11** 列出精确协议/端口，明确确认后仅对安全识别的已启用 UFW/firewalld 放行并回读。不会自动启用防火墙、猜 firewalld zone、修改 SSH、云安全组或未知自定义规则。`--open-firewall yes` 保留同等边界；`--assume-external-ports-open` 是兼容参数，不证明公网可达，也不绕过 TLS 检查。

HTTP-01 需要外部 CA 可以访问 TCP 80；本机 curl 成功不证明公网入口可达。域名 A/AAAA、实际 IPv6、CAA 和云规则也需正确，见[证书指南](CERTIFICATES.md)。

### QUIC 节点的 UDP 声明

Hysteria2 / TUIC 使用节点 UDP 端口承载 QUIC，TCP 同号规则不能代替。可向短入口或下节本地安装函数追加可重复参数，例如 `--node-udp-port 10443 --node-udp-port 20443`。

每个 UDP 声明必须为 1024–65535。新安装分别探测 IPv4 和可用 IPv6 的 UDP bind，占用时停止；升级跳过 bind 探测，避免与自己的运行节点冲突，需人工核对端口所有权。声明不创建节点、不持久保存，下次需要检查时须重新传入，也不授予应用 UDP 或 UDP 中转支持。

## 3. 本地完整套件

适合需要审核下载步骤或离线转移套件的管理员。先接受官方 Release 作为软件来源，并从受信发布记录或独立核验渠道取得**整个 `SHA256SUMS` 文件的 SHA-256**。摘要证明字节一致，不是签名，不能用未知下载文件的自算值认证来源。

以下是v0.4.23完整19项资产下载与核验模板。逐项校验并复制到root私有目录；目标架构须按实际机器修改。GitHub自动生成的Source code ZIP/TAR不替代`vui-source.zip`。

```sh
set -euo pipefail
umask 077
VERSION=v0.4.23
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

到此只完成套件下载、root私有副本和调用函数定义，尚未运行系统安装。如需升级已有实例，不要再执行下面的首次安装调用；优先使用全局菜单12或遵循[运维指南](OPERATIONS.md)。本节本地调用未请求安装全局控制组件，组件安装要求见[受管运维](MANAGED_OPERATIONS.md)。

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

## 4. 数据、服务与已有证书

| 路径或服务 | 作用 |
| --- | --- |
| `/var/lib/v-ui/data` | 数据库、节点配置和证书等私有数据 |
| `/var/lib/v-ui/releases/<release_id>` | 每个安装版本及自己的运行时 |
| `/etc/v-ui/service.json` | root 控制的受管安装记录 |
| `v-ui.service` | systemd 非 root 面板、核心和证书任务 |
| `v-ui-http01.socket` / `v-ui-http01.service` | systemd 持有 TCP 80，按需唤起非 root responder |
| OpenRC 的 `v-ui` / `v-ui-http01` | 非 root 服务，HTTP-01 responder 仅有绑定低端口所需 capability |

已有证书可用 `--cert` 与 `--key` 同时提供普通文件，跳过首次 ACME；私有 CA 可加 `--health-ca` 用于安装后的本机 TLS 健康检查。已有手工证书不会自动获得 V-UI 托管续期，后续需在证书页面申请并绑定正式证书。

root 负责初始化和受管控制；应用、核心与 Certbot 使用专用低权限用户。不要以 root 执行服务用户可写的 portable Python 或 payload，不把配置文件当 shell 执行。

## DNS-01 的安装与升级边界

DNS-01在已安装并可登录的面板内由管理员配置，不替代首次安装的HTTP-01流程；初装默认仍需公网TCP 80。操作见[Cloudflare DNS-01指南](DNS01_CLOUDFLARE.md)。

受支持升级遇到有效的已应用DNS面板绑定，会复用已应用旧revision，不重新签发或修改联系人。同一证书ID存在待应用版本时，保留期望值、错误和当前已应用材料，不把待应用状态提升为成功；期望/已应用ID冲突或材料损坏时明确拒绝混用。已有节点、凭据、传输和未知高级字段继续保留，停止的核心不会被证书操作擅自启动。

含DNS数据的备份使用`backup-dns01-v1`，旧22 reader会在替换数据前拒绝；受支持版本切换也会拒绝不声明DNS读取能力的旧运行时。恢复后DNS凭据须管理员重新验证启用，auto_renew偏好保持；详见[备份与恢复说明](DNS01_CLOUDFLARE.md#备份恢复与版本回退)。不要用旧安装器或短入口强行降级来绕过这些保护。

## 5. 安装后维护

推荐短入口注册全局 `v-ui`。`sudo v-ui` 的 **12 在线升级**沿用原配置，**13 维护授权**默认只读、30 分钟，最长 180 天；详见[运维指南](OPERATIONS.md)。旧式安装缺少全局入口时，使用短入口升级，或按[受管控制说明](MANAGED_OPERATIONS.md)准备受信组件。

保留离机备份和原版本运行时。v0.4.23 的四目标构建、新装、八旧版保数据升级及匿名公共安装已由 Actions 验收；短入口及实际 main URL 的证据另见[记录](DNS01_CLOUDFLARE_CONTRACT.md#公开发布与短入口)。用户主机与客户端需自行核对；**浏览器未验证**，历史问题与原始失败见[历史索引](history/README.md)。
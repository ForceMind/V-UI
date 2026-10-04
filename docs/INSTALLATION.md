# 安装与首次运行

## 1. 环境检测

0.3.1 一键安装器不再按 Ubuntu 名称做白名单。启动后先读取系统信息并检测：

- 发行版 / `/etc/os-release`；
- CPU：x86_64 或 ARM64；
- libc：glibc 或 musl；
- init：systemd 或 OpenRC；
- 包管理器：apt、dnf/yum、zypper、pacman、apk、xbps、emerge；
- TCP 80、面板端口、默认节点端口；
- UFW、firewalld、自定义 nftables / iptables。

运行服务使用目标包自带的固定 CPython 3.12 和 hash-locked wheels，不要求系统自带 Python 3.12。系统 Python 只负责启动安装器；低于 3.9 时入口会先尝试包管理器，仍不足时使用固定 SHA-256 的便携 Python 引导。

当前目标包：

- `vui-linux-x86_64-gnu.zip`
- `vui-linux-aarch64-gnu.zip`
- `vui-linux-x86_64-musl.zip`
- `vui-linux-aarch64-musl.zip`

未经适配的 CPU、init、声明式/只读系统会明确停止，不猜命令修改系统。

## 2. 端口和防火墙

默认预检：

| TCP 端口 | 用途 |
| --- | --- |
| 80 | ACME HTTP-01 |
| 8443 | HTTPS 管理面板 |
| 10443 | 默认节点端口，可用 `--node-port` 修改 |

端口已有监听时安装停止，不结束原进程。

UFW / firewalld 缺规则时，交互模式会询问是否现在开放；只有输入 `yes` 才修改。firewalld 同时写运行时和永久规则。自定义 nftables / iptables 不自动覆盖；安装器列出端口并等待人工确认。云安全组无法由本机可靠修改，也会提示并等待确认。

自动化场景只有在操作者确实已经处理外部防火墙时才应使用 `--assume-external-ports-open`。这不是“检测到已开放”的意思。

80 被 Nginx / Apache / Caddy / 其他站点占用时，本版不会自动接管已有网站。

## 3. 本地验收包

选择与机器 target 匹配的包并核对摘要：

```sh
sha256sum -c SHA256SUMS
SHA="$(awk '$2=="vui-linux-x86_64-gnu.zip" {print $1}' SHA256SUMS)"
sudo bash install.sh --bundle ./vui-linux-x86_64-gnu.zip --sha256 "$SHA"
```

也可以显式提供非秘密参数：

```sh
sudo bash install.sh --bundle ./vui-linux-x86_64-gnu.zip --sha256 "$SHA" \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --port 8443 --node-port 10443 --accept-terms
```

管理员密码只从控制终端交互读取，不接受密码命令参数。

`--dry-run` 会验证包并输出平台、防火墙和端口结果，不创建账号、服务或规则。

## 4. 服务后端

### systemd

- `v-ui.service`：非 root HTTPS 面板、核心与证书任务。
- `v-ui-http01.socket`：systemd 持有公网 TCP 80。
- `v-ui-http01.service`：非 root challenge responder。

### OpenRC

- `v-ui`：通过 `supervise-daemon` 运行面板。
- `v-ui-http01`：仍以 `v-ui` 用户运行，仅获得绑定低端口所需 capability。

主面板不会因为 OpenRC 支持而变成 root 进程。

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

正式 Release 公布后：

```sh
sudo bash install.sh --version v0.3.1
```

脚本先检测 `x86_64/aarch64 + gnu/musl`，再从该明确版本下载对应目标包、安装控制器和 `SHA256SUMS`，逐个验证摘要后执行。不会下载 `latest`，也不会在发布时重新构建包。

HTTP-01 条件见 [CERTIFICATES.md](CERTIFICATES.md)，升级/备份见 [OPERATIONS.md](OPERATIONS.md)。

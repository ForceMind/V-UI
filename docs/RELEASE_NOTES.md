# V-UI 0.3.1

本版重点是把一键部署从 Ubuntu/amd64 单一环境扩展成**发行版无白名单的 Linux 能力检测 + 四目标固定运行包**，并把端口/防火墙处理改为显式、安全、可确认的流程。

## Linux 安装

安装入口会先检测：

- 发行版与 `/etc/os-release`；
- x86_64 / ARM64；
- glibc / musl；
- systemd / OpenRC；
- apt、dnf/yum、zypper、pacman、apk、xbps、emerge 等包管理器；
- TCP 80、面板端口和默认节点端口占用；
- UFW、firewalld、自定义 nftables/iptables。

运行版本使用包内固定 CPython 3.12，不要求系统自带 Python 3.12。系统 Python 只用于启动安装器；过旧时可使用固定摘要的便携引导 Python。

正式 Release 包按目标拆分：

- `vui-linux-x86_64-gnu.zip`
- `vui-linux-aarch64-gnu.zip`
- `vui-linux-x86_64-musl.zip`
- `vui-linux-aarch64-musl.zip`

`install.sh --version v0.3.1` 会先检测当前机器，再只下载对应包。

## 端口和防火墙

默认预检 TCP 80、8443 和 10443（节点端口可修改）。端口被其他服务占用时停止，不自动结束原进程。

UFW / firewalld 缺规则时会询问是否开放，只有输入 `yes` 才修改。自定义 nftables / iptables 不自动覆盖；云安全组也不会假装可以从本机修改，安装器会列出所需端口并等待人工确认。

## 验收

0.3.1 的正式发布要求同一 commit 同时通过常规测试、ToClash、真实代理/DNS、ACME、one-click systemd 安装、release deployment、文档以及 portable Linux matrix。ARM64 和 musl 都使用原生环境构建/启动，不用交叉平台结果冒充真实支持。

## 仍未扩大协议支持范围

公开导出和真实端到端代理链路仍以已经验收的 sing-box + VLESS + TCP + TLS 为基线。Trojan、Shadowsocks、VMess、HY2、TUIC、REALITY/Vision 等会在后续版本逐项完成创建、编辑、导出、核心检查、客户端检查和真实连接失败路径后再标记支持。

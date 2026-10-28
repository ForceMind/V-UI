<picture>
  <source media="(prefers-color-scheme: dark)" srcset="web/brand/vui-wordmark-dark.svg">
  <img src="web/brand/vui-wordmark-light.svg" alt="V-UI：蓝青双带 V 标识" width="176" height="183">
</picture>

# V-UI

面向 Linux 服务器的 Xray / sing-box 管理面板，提供中文 Web 界面、节点与订阅管理、证书管理，以及终端运维菜单。

[下载稳定版](https://github.com/ForceMind/V-UI/releases/latest) · [使用文档](docs/README.md) · [协议兼容性](docs/COMPATIBILITY.md) · [报告问题](https://github.com/ForceMind/V-UI/issues)

**稳定版：[v0.4.9](https://github.com/ForceMind/V-UI/releases/tag/v0.4.9) · v0.4.10 安装交互修复准备中**

## 功能

- **节点管理**：创建和编辑节点，管理核心状态，导出客户端配置与订阅。
- **代理与中转**：独立控制主机代理和服务端中转；每个入口可跟随全局、指定直连或指定中转。
- **证书管理**：通过 HTTP-01 申请和续期证书，也可使用已有证书。
- **终端管理**：安装后输入 `v-ui`，查看状态，执行受支持的服务、备份、恢复和版本管理操作。
- **数据保护**：下载校验、操作锁、升级备份、失败恢复；服务使用专用低权限账号运行。

后端使用 Python / FastAPI / SQLite，前端使用 Vue 3 / Element Plus；代理流量由官方 Xray、sing-box 核心处理。发行包自带固定 Python 运行时、离线依赖和前端资源，运行时无需 Node.js 或 Redis。

## 安装要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Linux，x86_64 或 ARM64，glibc 或 musl；由安装器检测实际支持情况 |
| 服务管理 | 推荐 systemd，支持完整受管终端菜单；OpenRC 的管理能力有差异，见[安装指南](docs/INSTALLATION.md) |
| 权限 | root 或具备 sudo 权限的管理员 |
| 下载 | 可以访问 GitHub 与相关官方依赖源；短命令需要已安装 Bash、curl |
| 域名与证书 | 使用 HTTP-01 自动证书时，需要自己控制的域名解析到服务器，并满足公网 TCP 80 验证条件；也可提供已有证书 |
| 网络端口 | 按提示核对面板、节点及云安全组端口；安装器不会接管已有网站或修改 SSH |

项目尚未承诺 512 MiB 整机长期运行资格。请为系统、代理负载、证书、升级及备份留出资源余量，详见[资源要求](docs/LOW_RESOURCE_TARGET.md)。NixOS、只读系统及未支持的 init 不适用此安装器。

## 快速开始

在服务器的 Bash 终端执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)
```

入口当前固定安装已公开的 v0.4.9，完成下载与完整性校验，并交互询问域名、邮箱、管理员、端口和密码；需要时通过现有 sudo 策略取得管理员权限。自动证书的 CA 条款由使用者本人确认。不要将密码写入命令行。

域名为空或格式无效时，会当场提示重新输入，合法后才继续邮箱等配置。v0.4.10 还提供中文安装提示及 `y/n/yes/no` 确认（回车默认否、非法回答重问）；旧 v0.4.9 安装器不包含后两项改动。请填写 DNS 域名，不能带 `https://`、路径、端口、IP 或通配符；国际化域名会按 IDNA 规范化。

按安装器最终输出的 HTTPS 地址登录。安装完成后，随时打开终端菜单：

```bash
v-ui
```

以下固定版本命令须在 v0.4.10 正式公开并更新短入口后使用；发布前请继续使用上方无版本参数的稳定入口：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh) --version v0.4.10
```

短入口会执行来自本项目仓库的脚本，请确认仓库和地址属于你信任的来源。完整包、安装 helper 和资产摘要仍需校验。离线安装、已有证书、自定义路径及端口参数见[安装指南](docs/INSTALLATION.md)；固定摘要入口见相应 [Release](https://github.com/ForceMind/V-UI/releases)。

## 日常使用

1. 登录 Web 面板，配置所需核心、证书和节点。
2. 在节点中选择已支持的协议与传输组合，导出配置或订阅到客户端。
3. 需要中转时先配置上游，再选择全局或每入口的 TCP 路由策略。
4. 使用 `v-ui` 或 Web 面板查看实际状态；更改配置后按提示应用。

默认面板地址使用 HTTPS 和 8443 端口，以本机安装器实际输出为准。证书错误应检查域名、证书链和系统时间，保持 TLS 验证开启。

## 协议支持

| 协议 | 公开支持的组合 | 已验应用负载 |
| --- | --- | --- |
| VLESS | TCP + TLS、WebSocket + TLS、gRPC + TLS、TCP + REALITY / Vision | HTTP/TCP |
| Trojan / VMess | TCP + TLS | HTTP/TCP |
| Shadowsocks | 指定 AEAD 方法 | TCP / UDP |
| Hysteria2 / TUIC v5 | 原生 QUIC + TLS | HTTP/TCP |

各组合有明确的参数和客户端边界，以[兼容矩阵](docs/COMPATIBILITY.md)为准。QUIC 使用 UDP 传输不代表通用应用 UDP 已验收；有效中转入口仍拒绝未获资格的 UDP。XHTTP / HTTPUpgrade 公开导出保持阻断。

支持 URI/Base64、Mihomo YAML 和 sing-box JSON 等对应导出格式；完整 ToClash 分流仅包含于 Mihomo YAML。不能表达的字段或组合会明确拒绝，不静默丢失配置或失败回退 DIRECT。

## 升级与备份

- 升级前先备份，再核对版本、平台和安装来源；切勿直接替换数据库或跨 CPU/libc 覆盖安装。
- `v-ui` 中的候选准备与激活是两个步骤；准备成功不代表已切换版本。
- 代码回退与数据恢复各有适用条件，数据库或配置变更后不要仅切换程序目录来处理故障。

操作步骤见[运维指南](docs/OPERATIONS.md)和[全局管理菜单](docs/MANAGED_OPERATIONS.md)。默认数据目录为 `/var/lib/v-ui/data`，安装记录为 `/etc/v-ui/service.json`。

## 常见问题

**找不到 `v-ui`？** 新短入口会注册全局菜单。旧式安装可能只有包内菜单，请按[管理菜单安装指南](docs/MANAGED_OPERATIONS.md)处理，避免直接以 root 执行服务用户可写的脚本。

**安装失败或面板打不开？** 保留退出码、版本和错误输出，核对 DNS、端口、证书及服务状态。不要反复安装或关闭 TLS 验证掩盖问题，见[故障排查](docs/TROUBLESHOOTING.md)。

**域名 `raw.githubusercontent.com` 是什么？** 它是 GitHub 提供仓库文件原始内容的地址，供安装入口下载脚本使用。它不能作为你自己的面板域名；面板域名需要由你控制并解析到服务器。

**如何报告问题？** 在 [Issues](https://github.com/ForceMind/V-UI/issues) 提供版本、系统/架构、复现步骤、预期和实际结果，以及已脱敏的日志。请勿公开密码、token、订阅地址或私钥。安全问题按 [SECURITY.md](SECURITY.md) 报告。

## 文档

| 内容 | 入口 |
| --- | --- |
| 安装、端口、已有证书 | [安装指南](docs/INSTALLATION.md) |
| 证书与续期 | [证书指南](docs/CERTIFICATES.md) |
| 代理、分流和中转 | [每入口路由](docs/PER_INBOUND_ROUTING.md) · [TCP 上游导出](docs/TCP_UPSTREAM_EXPORT.md) |
| 升级、备份、恢复 | [运维指南](docs/OPERATIONS.md) |
| 完整功能和参数边界 | [兼容矩阵](docs/COMPATIBILITY.md) · [文档索引](docs/README.md) |
| 版本变化与后续方向 | [变更记录](CHANGELOG.md) · [路线图](ROADMAP.md) |

## 开发与贡献

长期分支仅保留 `main` 和 `dev`：`main` 承载稳定代码与正式发布，`dev` 用于开发集成。每个版本从 `dev` 创建独立的 `release/vX.Y.Z` 分支，完成审查和测试后合入 `dev`，再通过 PR 合入 `main`；发布以 `vX.Y.Z` tag 和 Release 标识，完成后删除临时版本分支。

构建和制品处理使用 GitHub Actions；测试入口与贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。Go 后端迁移目前仅在路线图保留，尚未启动。

## 许可证与致谢

V-UI 使用 [MIT License](LICENSE)，Copyright © 2026 ForceMind。感谢 Xray、sing-box、Vue、Element Plus、Certbot 与 ToClash 等上游项目。第三方组件沿用各自许可证；来源及分发要求见 [third_party/NOTICE.md](third_party/NOTICE.md)。

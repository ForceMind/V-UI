<picture>
  <source media="(prefers-color-scheme: dark)" srcset="web/brand/vui-wordmark-dark.svg">
  <img src="web/brand/vui-wordmark-light.svg" alt="V-UI" width="176" height="183">
</picture>

# V-UI

面向 Linux 服务器的 Xray / sing-box 中文管理面板，用于创建节点、管理证书、分享订阅和维护服务。

[稳定版 v0.4.25](https://github.com/ForceMind/V-UI/releases/tag/v0.4.25) · [使用文档](docs/README.md) · [获取帮助](SUPPORT.md) · [MIT License](LICENSE)

## 快速开始

在服务器的 Bash 终端执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)
```

短入口固定下载已公开的 **v0.4.25**，校验安装器和完整套件摘要。发布源与资产记录见[发布规范](docs/RELEASING.md#当前稳定版)。脚本来自本项目仓库，请先确认软件来源可信；需要固定版本、离线部署或手工核验时，见[安装指南](docs/INSTALLATION.md)。

新安装按提示填写自己的域名、邮箱和端口，并本人确认首次自动证书的 CA 条款。管理员用户名和强密码自动生成，只在当前终端交付；保存凭据后，按安装器最终输出的 HTTPS 地址登录。域名不能带协议、路径或端口，不能用 GitHub 下载域名代替自己的域名。

已有安装先显示菜单：`1` 本地管理、`2` 在线升级、`3` 继续或修复、`0` 或回车退出。打开本地菜单和退出不下载发布套件；只有选择 `2` 或 `3` 才下载。升级沿用原配置、账号和数据，修复流程可保留原值或明确修改。

安装后运行 `v-ui`（需要时使用 `sudo v-ui`）管理服务。**12 在线升级**在选择并确认后下载官方稳定版；**13 维护授权**默认只读、30 分钟，最长 180 天，链接只能兑换一次，可随时撤销。完整步骤见[安装指南](docs/INSTALLATION.md)和[运维指南](docs/OPERATIONS.md)。

## 安装后做什么

| 任务 | 操作与结果 |
| --- | --- |
| 创建直连节点 | 登录面板，选择“创建直连节点”，确认名称、端口和适用证书，创建后获取客户端连接 |
| 创建中转节点 | 选择“创建中转节点”，选择已有出口或粘贴另一台 V-UI 导出的 TCP 上游链接，检查并应用 |
| 为服务器程序使用代理 | 选择“服务器程序代理”，选择出口并开启，按页面提供的方法连接本机 `127.0.0.1` 代理 |
| 分享给手机、电脑或另一服务器 | 在节点中复制链接、显示二维码或创建订阅；供另一 V-UI 使用时选择专用的“仅 TCP”上游导出 |

保存成功、核心正在运行和客户端实际连通是不同状态，请检查应用结果。节点连接地址独立于面板地址；证书错误应排查域名、证书链和时间，保持 TLS 验证开启。操作与参数说明见[配置指南](docs/CONFIGURATION.md)。

## Cloudflare DNS-01 与通配符证书

v0.4.23 新增 Cloudflare DNS-01、根域名与一级通配符证书、具体连接域名绑定及遗留验证记录清理。DNS 凭据只由管理员配置；真实 Cloudflare 账号签发与浏览器未验证。

[DNS-01使用指南](docs/DNS01_CLOUDFLARE.md)面向已经安装相应版本、能够以管理员登录的面板；初装仍默认HTTP-01，需要满足TCP 80条件。DNS私有备份含凭据且未加密，恢复后需重新验证启用；含DNS数据不能使用旧运行时回退。

需要固定版本或离线部署时，请按[安装指南](docs/INSTALLATION.md)核验完整套件，并在完整安装器中使用`--version v0.4.25`。

## 能力与边界

- 节点与订阅：支持符合[兼容矩阵](docs/COMPATIBILITY.md)的 VLESS、Trojan、VMess、Shadowsocks、Hysteria2 和 TUIC 组合，提供对应 URI/Base64、Mihomo YAML、sing-box JSON 导出。
- 代理与中转：服务器程序代理和入站中转独立控制；每入口可跟随全局、指定直连或指定中转，实例共用一个选定上游。服务器程序代理仅为本机程序提供代理入口，不接管全系统流量或提供系统 TUN。
- 证书：支持 HTTP-01 自动申请、续期与绑定，也可提供已有证书。申请成功和节点实际应用分别检查。
- 运维：提供服务管理、备份恢复、正式在线升级和限时远程维护；应用与核心使用专用低权限账号。
- 支持范围：有效中转仅限已验证 TCP，UDP 中转尚未获支持资格；QUIC 使用 UDP 承载不代表应用 UDP 已验证。XHTTP / HTTPUpgrade 保持公开导出阻断，未知字段或不支持组合会明确拒绝。

后端使用 **Python / FastAPI / SQLite**，前端使用 Vue 3 / Element Plus。流量由固定官方 Xray 26.3.27、sing-box 1.14.2 核心处理，客户端兼容基线为 Mihomo 1.19.32。发行包自带固定 CPython 3.12、离线依赖与前端资源，运行无需 Node.js 或 Redis。核心版本随经过验证的正式套件管理。

## 平台与资源

| 项目 | 要求 |
| --- | --- |
| 平台 | Linux，x86_64 / ARM64，glibc / musl 四目标；仍须满足安装器实际内核、libc 和系统能力检测 |
| 服务管理 | systemd 提供完整受管菜单；OpenRC 的全局菜单目前仅支持状态查询和本地候选准备 |
| 权限与下载 | root 或已有 sudo 权限；短命令需要 Bash、curl，以及可访问 GitHub 和必要官方依赖源的网络 |
| 自动证书 | 自己控制的域名解析到服务器，公网 TCP 80 可用于 HTTP-01；也可提供已有证书 |
| 网络 | 核对面板与节点实际端口、主机规则和云安全组；不会接管已有网站或修改 SSH |

NixOS、声明式或只读系统不在安装支持范围。项目尚未承诺 **512 MiB 整机长期运行资格**，请为系统、核心负载、证书、升级与备份保留资源，见[资源要求](docs/LOW_RESOURCE_TARGET.md)。

v0.4.25 已完成四目标构建、新装和十个公开旧版保数据升级验收。应用安装携带内置核心，独立组件按应用来源核对兼容性。**浏览器和真实 Cloudflare 账号签发未验证**。准确来源与验收见[发布记录](docs/RELEASING.md#当前稳定版)。

## 文档与社区

| 内容 | 入口 |
| --- | --- |
| 使用与维护 | [文档索引](docs/README.md) · [安装](docs/INSTALLATION.md) · [配置](docs/CONFIGURATION.md) · [证书](docs/CERTIFICATES.md) · [Cloudflare DNS-01指南](docs/DNS01_CLOUDFLARE.md) |
| 升级、备份与故障 | [运维](docs/OPERATIONS.md) · [故障排查](docs/TROUBLESHOOTING.md) |
| 版本与方向 | [变更记录](CHANGELOG.md) · [路线图](ROADMAP.md) · [历史资料](docs/history/README.md) |
| 参与项目 | [贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md) · [支持渠道](SUPPORT.md) · [安全报告](SECURITY.md) |

报告问题请提供版本、系统架构、复现步骤和已脱敏日志，不公开密码、完整订阅地址或私钥。安全漏洞按[安全政策](SECURITY.md)私下报告。

## 许可证与致谢

V-UI 使用 [MIT License](LICENSE)，Copyright © 2026 ForceMind。感谢 Xray、sing-box、Vue、Element Plus、Certbot 和 ToClash 等上游项目；第三方组件遵循各自许可证，见[来源与分发说明](third_party/NOTICE.md)。

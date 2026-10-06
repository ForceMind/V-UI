# V-UI

**个人自用的轻量代理面板：管理节点、图形化申请证书、设置 ToClash 分流，直接订阅完整 Mihomo 配置。**

版本目标：**v0.4.6**（TUIC v5/TLS 候选；独立审查和最终 exact-head / exact-master CI 待完成）。正式发布前须完成 [发布检查](docs/RELEASING.md) 中的全部 exact-head 验收和人工发布动作；版本号不代表 GitHub Release 已公开。

## 能做什么

V-UI 使用 FastAPI + SQLite，不依赖 Redis、常驻 Node 或在线订阅转换服务。运行包包含固定版本的核心、Python wheels 和本地前端资源；不在安装时临时解析 `latest`。

| 功能 | 内容 |
| --- | --- |
| 管理面板 | 真实管理员账号、登录/退出/改密、会话失效与接口鉴权，无默认密码 |
| 节点管理 | Xray/sing-box 核心独立控制，候选配置校验、失败保护、状态区分与重启恢复 |
| 图形化证书 | HTTP-01 自动签发、测试/正式环境、到期状态、自动续期、面板热更新、TLS节点绑定 |
| ToClash 分流 | 两种模式、40项服务预设、直连/代理、自定义内网DNS、CGNAT、规则预览 |
| 客户端订阅 | 节点/格式作用域、一次显示的专用令牌、到期、轮换、撤销；完整Mihomo配置直接导出 |
| 安装运维 | 一个入口完成受管服务设置，开机自启、权限隔离、停机备份、校验恢复、候选切换 |

**已验收的 v0.4.2 主线包含 sing-box + VLESS/TCP/TLS、Trojan/TCP/TLS、Shadowsocks AEAD 与 VMess/TCP/TLS。** 2026-10-05 的准确主线提交 `0225ce4` 已完成 16 个正常合并 PR、八组工作流共 11 个 job 及四目标 Linux 附件核验；当时未创建 Draft Release、版本 tag、公开 Release 或实际部署。详见[主线收口记录](docs/MAINLINE_CLOSURE_20261005.md)。

**v0.4.3 VLESS/WebSocket/TLS 已验收为继承基线。** [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，准确主线八组工作流、11 个 job 和每一步均成功。可选 WebSocket Host 是客户端路由信息，不是服务端访问白名单，也不代替 TLS SNI/证书验证；详见[WS 收口记录](docs/VLESS_WS_CLOSURE_043.md)。合并与 CI 不表示发布或附件晋升。

**v0.4.4 VLESS/gRPC/TLS 已验收为继承基线。** [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并至 `84729dfc53165003e7d459a5d56621ce89ba497c`，候选 `240edf23af8a12b2cbd71114fe65c693290e39f2` 与合并 tree 均为 `200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc`；准确候选及主线八组成功，最终主线 11 个 job 和每一步全部成功。固定 sing-box 1.14.2 实际使用 gRPC Lite，仍只有 HTTP/TCP；错误 CA/SNI 可能让调用者超时，真实 x509 诊断不表示及时错误传播。详见[gRPC 收口](docs/VLESS_GRPC_CLOSURE_044.md)，[首次失败与候选历史](docs/VLESS_GRPC_044.md)完整保留。

**v0.4.5 Hysteria2/TLS 已完成 [PR #20/#21 主线收口](docs/HYSTERIA2_CLOSURE_045.md)。** 最终 master `838c66d9974dd9f3a944641a2e9e03cc200e0bbe` 的八组工作流、11 个 job 和全部步骤 attempt 1 成功。首次前置缺日志、第二前置 ACME 端口碰撞、首次合并主线的继承 Trojan/gRPC TLS 证据失败和 portable API 限流均保留，不将历史失败写成全绿。

**普通节点响应已继承 [PR #23 安全修复](docs/INBOUND_RESPONSE_CLOSURE_20261006.md)。** 准确 master `8e0d07463e59b52856f55fa33346a760a38b4705` 独立审查和候选/主线八组完成。普通列表/创建/更新仅返回允许列表摘要，不返回 UUID/密码、原始配置或服务器材料路径；特权 `/editor` 保留手工证书路径字符串，明确授权导出保留客户端凭据且无服务端材料。没有证据声称真实泄露事件。

**v0.4.6 仅新增 sing-box / TUIC v5 / TLS 候选**，固定官方 sing-box 1.14.2 服务端/客户端和 Mihomo 1.19.32 不变。单 UUID/密码对、明确验证 SNI、服务端 ALPN 恰为 h3、原生 QUIC/默认拥塞和零 RTT 关闭。三格式为固定客户端支持的 TUIC URI 约定、完整 Mihomo YAML、sing-box JSON；URI 是上游临时/非官方约定，不能称官方通用标准。UUID/密码输入分别留空新建生成、编辑保留；未知或高级导入字段保留或明确拒绝，不能静默清除后导出。

裸前置 `21cb0bc721dd4f5e4d172d7602c8135f2f04db91` 八组/11 jobs/全部步骤 attempt 1 成功，[真实链路 69 项](https://github.com/ForceMind/V-UI/actions/runs/37356266391)含双客户端正确 HTTP、分别错误 UUID/密码/CA/SNI，真实原因、零目标送达和无 DIRECT。**最终集成独立审查、准确候选八组、授权正常合并和准确主线八组仍待完成。** 当前重建不复用丢失的旧本地通过证据，详见[TUIC 契约](docs/TUIC_046.md)、[参数](docs/CONFIGURATION.md#046-tuic-v5tls-候选)和[兼容矩阵](docs/COMPATIBILITY.md)。

TUIC 只验 HTTP/TCP；sing-box 出站为 `network: tcp`。Mihomo TUIC adapter 硬编码 UDP 能力，省略无效 `udp: false`，不能宣称已关闭 UDP。QUIC 传输要求节点 UDP 通行，但应用 UDP 未验收。未知字段或不支持组合拒绝，失败不退 DIRECT。

## 快速安装

安装器不再按 Ubuntu 白名单判断环境，而是检测 CPU、glibc/musl、systemd/OpenRC、包管理器、防火墙和端口。目标运行包覆盖 x86_64/ARM64 × glibc/musl；运行服务使用固定便携 Python 3.12。

将**同一验收提交**的安装套件解压后，在套件目录执行：

```sh
sha256sum -c SHA256SUMS
# 示例：x86_64 + glibc；其他机器使用对应 target 包
sudo bash install.sh --bundle ./vui-linux-x86_64-gnu.zip \
  --sha256 "$(awk '$2=="vui-linux-x86_64-gnu.zip" {print $1}' SHA256SUMS)"
```

脚本会先检查 TCP 80、面板端口和默认节点 TCP 端口。HY2/TUIC 的 UDP 端口须显式重复声明，例如 `--node-udp-port 10443 --node-udp-port 20443`；每个端口须为 1024–65535，TCP 放行不能替代 UDP。新安装做 IPv4/可用 IPv6 UDP 占用探测，升级明确提示人工核对所有权。声明不持久保存，后续运行须重传，也不会创建节点。本机 UFW/firewalld 缺规则时会按端口/协议分别列出，只有明确 `yes` 才会开放，且不自动启用防火墙；自定义 nftables/iptables 与云安全组只提示并等待人工确认。随后才创建低权限账号、HTTP-01 验证服务和 HTTPS 面板。

发布后可指定明确版本通过同一入口下载官方Release资产；**正式Release尚未生成时不要把下面命令当作当前可用下载地址**：

```sh
sudo bash install.sh --version v0.4.6
```

安装器本身也必须来自可信仓库/套件，不能只信任来源不明压缩包附带的摘要。详见 [安装指南](docs/INSTALLATION.md)。

## 日常使用

HTTPS登录 → 节点管理 → 新建已验证TLS节点，选择托管证书或填写已有证书路径 → 分流与订阅 → 保存规则 → 选择节点与格式并创建专用订阅 → 客户端导入并刷新。

修改草稿不会影响客户端。保存成功后客户端刷新原URL即可取得新配置，无需重新创建令牌，也无需再次打开ToClash转换。URI/Base64只承载节点；sing-box连接JSON不包含完整ToClash分流，不能与Mihomo完整YAML混称。

证书管理可从导航进入：申请、测试签发、查看到期与失败原因、暂停自动续期、绑定面板或TLS节点。测试证书不能用于上线；续期失败保留旧材料但不会延长旧证书有效期。服务停止期间续期检查暂停，恢复服务后继续。

## 文档

[完整文档入口](docs/README.md) · [安装](docs/INSTALLATION.md) · [证书](docs/CERTIFICATES.md) · [分流/订阅](docs/CONFIGURATION.md) · [维护/备份/恢复](docs/OPERATIONS.md) · [常见故障](docs/TROUBLESHOOTING.md) · [API](docs/API.md) · [兼容范围](docs/COMPATIBILITY.md) · [安全](SECURITY.md) · [开发](CONTRIBUTING.md) · [正式发布](docs/RELEASING.md) · [变更日志](CHANGELOG.md)

V-UI自身沿用原README声明的MIT许可；打包的核心、Certbot、其他依赖和ToClash仍受各自许可约束，不因项目MIT许可而变更。来源和许可见 [third_party/NOTICE.md](third_party/NOTICE.md) 及构建包中的provenance/license文件。

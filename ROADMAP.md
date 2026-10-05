# V-UI 开发路线

## 当前状态

v0.4.2 已在 2026-10-05 以 16 个正常 merge commit 收口到 `master` 的 `0225ce4`，PR #14 明确排除。准确主线的八组工作流、11 个 job 和四目标 Linux 附件均已核验；当时未创建 Draft Release、版本 tag、公开 Release 或部署。完整提交、tree 和验收链接见[主线收口记录](docs/MAINLINE_CLOSURE_20261005.md)。

v0.4.3 由 [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，八组工作流、11 个 job 和每一步均成功，成为已验证 WS 基线；见[WS 收口记录](docs/VLESS_WS_CLOSURE_043.md)。该阶段未创建 tag/Release/部署，也未晋升 WS 候选附件。

v0.4.4 由 [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并至 `84729dfc53165003e7d459a5d56621ce89ba497c`，候选 `240edf23af8a12b2cbd71114fe65c693290e39f2` 与合并 tree 均为 `200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc`；独立审查、八组 exact-head 与八组 exact-master 已完成，最终主线 11 个 job/全部步骤成功。它是 gRPC Lite 已验证基线，详见[gRPC 收口](docs/VLESS_GRPC_CLOSURE_044.md)。

当前独立版本为 **v0.4.5 Hysteria2/TLS 候选**，[Draft PR #20](https://github.com/ForceMind/V-UI/pull/20)。固定前置 `e18003670c6469489c7a63413be0a3f9bd77cf0b` 的真实链路 58 项通过，但同提交 ACME 因 DNS TCP/UDP 测试端口碰撞失败，不是八组全绿；详见[HY2 契约](docs/HYSTERIA2_045.md)。集成独立审查、exact-head 八组、授权正常 merge 和 exact-master 八组尚待完成。旧主线或前置链路通过不自动认可后续源码或附件；发布、附件晋升与部署仍由独立流程负责。

## v0.3.0 基线

认证、只读订阅、安全核心应用/恢复、ToClash 分流、真实 VLESS/TCP/TLS 链路、Certbot HTTP-01 生命周期和首版 systemd 一键安装已经形成基线。

## v0.3.1 — Linux 可移植安装与发布

PR #12。

交付门槛：

1. 发行版、CPU、libc、init、package manager 自动检测。
2. x86_64/ARM64 × glibc/musl 四目标固定运行包。
3. sing-box/Xray 按目标选择固定官方二进制。
4. systemd/OpenRC 服务后端。
5. TCP 80、面板、默认节点端口占用检查。
6. UFW/firewalld 仅在用户明确 `yes` 后开放；自定义 nftables/iptables 与云安全组等待人工确认。
7. Ubuntu/systemd 真实 sudo 一键安装与升级回归。
8. ARM64、musl 原生构建/启动，Debian/Fedora/Arch/openSUSE/Alpine 代表性探测。
9. 正式 Release 聚合同一 exact-head 已验收的四个目标包；在线安装脚本自动选择 target。

## v0.3.2 — 节点完整编辑与回填

PR #13。

- 从现有 `settings / stream_settings / _vui` 反解统一 visual profile。
- 新建/编辑共用 schema 与编译器。
- UUID、密码、Reality 私钥、Obfs 密码等秘密不因编辑而重新生成，也不为编辑便利回传浏览器。
- 核心/协议迁移与普通编辑分离。
- 证书绑定状态回填。
- 浏览器验收覆盖：创建 → 编辑 → 刷新 → 再编辑 → 导出。

## v0.4.x — 协议矩阵逐项完成

顺序：

1. Trojan + TLS — **v0.4.0 / PR #15**：sing-box TCP/TLS strict export、真实 Mihomo/sing-box 链路与证书续期；
2. Shadowsocks — **v0.4.1 / PR #16（已合并至 v0.4.2 主线）**：三种 AEAD 导出/真实配置检查，逐 cipher 双客户端 TCP/UDP 链路与负向拒绝；
3. VMess + TLS — **v0.4.2 / PR #17（主线验收已完成）**：strict export、真实 Mihomo/sing-box 链路、错误 UUID/CA/SNI；
4. VLESS WebSocket — **v0.4.3 / PR #18（主线验收已完成）**；
5. VLESS gRPC — **v0.4.4 / PR #19（主线验收已完成）**；
6. Hysteria2 — **v0.4.5 / Draft PR #20（当前候选，最终审查/CI 待完成）**；
7. TUIC；
8. REALITY / Vision；
9. XHTTP / HTTPUpgrade；
10. UDP / DNS 专项。

每一个版本都必须同时完成服务端、编辑 UI、URI/Mihomo/sing-box 导出、真实核心检查、真实客户端检查、正向连接和错误凭据/TLS/参数失败路径。

### v0.4.3 已验收 WS 基线的保留边界

- 固定 sing-box 1.14.2 服务端，Mihomo 1.19.32 / sing-box 1.14.2 客户端；VLESS、单 UUID、空 flow、TLS、明确 SNI、正常证书校验。
- WebSocket path 为 1–256 个 ASCII 字符、以 `/` 开头，只接受字母、数字、`.`、`_`、`~`、`/`、`-`；禁止 `.` / `..` 路径段、query、fragment、百分号转义、空白与 early data。
- Host 可省略；填写时只接受最长 253 字符、每段最长 63 字符的 ASCII DNS-style 名称，不含 scheme、port 或尾随点。它是保存在 `transport.headers.Host` 的客户端路由信息，生成实际 sing-box 服务端配置时去除；服务端不校验请求 Host。TLS SNI/证书验证保持独立。
- ALPN 省略或仅 `http/1.1`，可选 Chrome fingerprint；只新增 HTTP/TCP 验证，不扩大 UDP。
- 三格式保留 WS/TLS/凭据；未知字段、early data、不支持 profile 明确拒绝，不能静默 DIRECT 或泄露私钥/服务端材料路径。
- 双客户端分别验正确链路与错误 UUID/CA/SNI/path 拒绝；不同合法 Host 可连通是实际核心行为，不能把它写成拒绝用例；非法 Host 应在输入/导出校验阶段拒绝。
- 浏览器覆盖创建、取消、编辑回填、刷新、再编辑、恢复及 UUID 不变；证书覆盖绑定、续期、失败保留旧材料和 `CORE_STOPPED_PENDING_APPLY`。既有 FastAPI/SQLite、40 项 ToClash、四目标 Linux 与发布边界保留。
- Xray WS、gRPC、Hysteria2、TUIC、REALITY/Vision 等不纳入 WS 基线。后续修改仍需在自己的准确提交重验。

### v0.4.4 已验收 gRPC 基线的保留边界

- 沿用 sing-box 1.14.2 / Mihomo 1.19.32 固定官方二进制与摘要；sing-box 无 `with_grpc`，使用实际 gRPC Lite，不重编译、不替换 pin。
- 仅 sing-box / VLESS / gRPC / TLS、单 UUID、空 flow、明确 SNI 和正常证书校验；ALPN 省略或恰为 `["h2"]`，Chrome fingerprint 独立可选。四种组合必须分别配置检查及双客户端真实转发。
- `service_name` 必须是 `[A-Za-z0-9._-]{1,128}` 字面字符串，保留大小写，`.` / `..` 亦为合法字面 service；不 trim/强转，不允许 leading slash、path/query、百分号转义、Unicode、authority/header、health timer 或 multi-mode。
- URI `type=grpc` / `serviceName`、Mihomo `grpc-opts.grpc-service-name`、sing-box `transport.service_name` 无损对应。仅 HTTP/TCP，Mihomo `udp: false`、sing-box `network: tcp`，不扩展 UDP。
- 正向 HTTP/2/h2、边界 service name、错误 UUID/CA/SNI/service name 及大小写差异均需真实双客户端证据。失败目标无请求，不退 DIRECT。Lite 错误 CA/SNI 可能是调用者超时；测试专用 HTTP/2 诊断可证明真实 x509 拒绝，不承诺及时 TLS 错误传播。
- 共用编译器、编辑器与托管证书；未知导入字段不因编辑而消失，UUID 隐藏且不变，创建/取消/编辑/刷新/再打开/恢复和 `CORE_STOPPED_PENDING_APPLY` 继续验收。
- 不新增依赖、后端、迁移、反向代理或 authority/Host 白名单；Xray gRPC、h2c、HY2/TUIC 等仍排除。前置通过与候选审查、准确候选八组 CI、正常合并、准确主线八组 CI 已分别记录于[主线收口](docs/VLESS_GRPC_CLOSURE_044.md)。首次失败和当时候选历史完整保留于[阶段契约](docs/VLESS_GRPC_044.md)，后续修改仍须重验。

### v0.4.5 的边界与门槛

- sing-box 1.14.2 / Mihomo 1.19.32 固定官方二进制，单密码、明确验证 SNI、原生 QUIC 默认值；不含 obfs、hopping、带宽/拥塞、ALPN/uTLS 覆盖、多用户或 TUIC。
- 标准 `hysteria2://` URI 编码密码并写明 `sni` / `insecure=0`，完整 Mihomo YAML 与 sing-box JSON 保留密码/TLS；服务端材料不导出，未知字段拒绝，不退 DIRECT。
- 仅真实 HTTP/TCP 负载；Mihomo `udp: false`、sing-box `network: tcp`。QUIC 必须有节点 UDP 通行，但不宣称应用 UDP 通过。
- 新建不注入带宽/Chrome；密码输入留空新建生成、编辑保留，已有秘密不回传。高级/obfs 草稿保留或不可表达时拒绝，不能借编辑静默变成公开可用配置。
- 复用托管证书；绑定/续期/失败保留材料与 `CORE_STOPPED_PENDING_APPLY`，真实浏览器创建/取消/编辑/刷新/再打开/导出/损坏后停机恢复分别验收。
- 安装器可重复 `--node-udp-port`（1024–65535），TCP/UDP 独立，新安装 IPv4/可用 IPv6 探测，升级提示人工核对所有权；精确端口/协议确认，不自动启用防火墙，声明不持久保存且不创建节点。
- 首次前置失败、第二次真实链路 58 项通过及 ACME DNS 端口碰撞失败均保留；最终独立审查和准确候选/主线各八组仍待完成，不能复用 gRPC 的通过或裸 HY2 前置代替集成验收。

## 后续

DNS-01/通配符、核心自动升级/回滚、任意 YAML 导入合并、容器专用部署、NixOS/其他声明式系统专用模块继续作为独立版本。

所有状态继续区分：代码已写、单测通过、真实二进制通过、真实链路通过、已发布、已部署。

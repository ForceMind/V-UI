# 变更日志

## 0.4.6 — TUIC v5/TLS（候选，最终集成验收待完成）

- 独立 [Draft PR #22](https://github.com/ForceMind/V-UI/pull/22)，固定官方 sing-box 1.14.2 / Mihomo 1.19.32，单 UUID/密码对、明确验证 SNI、服务端 ALPN 恰为 h3、原生 QUIC/默认拥塞、零 RTT 关闭
- 三格式为固定客户端非官方 TUIC URI 约定、Mihomo YAML、sing-box JSON；不是官方通用 URI 标准，保留客户端凭据/验证语义，不导出服务端材料。仅 HTTP/TCP；sing-box `network: tcp`，Mihomo TUIC 硬编码 UDP 能力，省略无效 `udp: false`，应用 UDP 未验收
- UUID/密码分别空输入新建生成、编辑保留，普通响应继承 PR #23 allowlist，特权 `/editor` 仅保留手工证书路径字符串；高级导入草稿保留或拒绝，不静默降格后公开导出
- 裸前置 `21cb0bc721dd4f5e4d172d7602c8135f2f04db91` 八组/11 jobs/全部步骤 attempt 1 成功，[链路 69 项](https://github.com/ForceMind/V-UI/actions/runs/37356266391)含双客户端分别错误 UUID/密码/CA/SNI 的真实原因、零目标送达、无 DIRECT
- 正常 merge-forward `7c380d7c9d7948f4e1992cbb5404b805904a6b57` 继承安全 master `8e0d0746`；当前重建不复用工作区回退前丢失的本地证据，runtime/browser EPERM 不算通过
- 公开订阅、实际 URI provider 导入、托管证书完整生命周期、Chromium 创建/取消/编辑/刷新/再打开/导出/损坏后停机恢复为本次集成门槛。独立审查、八组 exact-head、授权正常 merge、八组 exact-master 仍待完成；无 tag/Release、附件晋升或部署，详见[TUIC 契约](docs/TUIC_046.md)

## 2026-10-06 — 普通节点响应安全修复主线收口

- [PR #23](https://github.com/ForceMind/V-UI/pull/23) 正常合并至签名 master `8e0d07463e59b52856f55fa33346a760a38b4705`，tree `fda9f9d8a26dd21bdf0e441ab5d9bbc7ec0dbdd8`，来源候选 `ae415ea5ce4879a2e8a9cf52e8a933136087e1cd`
- 独立源码审查、八组准确候选和八组准确主线成功；最终 11 jobs/全部步骤 attempt 1 成功。普通 discovery 371 项（含 80 项明确 skip）、激活真实链路 63 项、托管 HY2 8 项及 Chromium 响应/证书卡/编辑/导出/恢复分别验证，见[安全收口](docs/INBOUND_RESPONSE_CLOSURE_20261006.md)
- 普通响应不回传秘密/原始配置/服务端材料路径；特权编辑路径字符串、必要客户端导出凭据保留。只使用合成凭据，不宣称真实泄露事件；无生产轮换、发布或部署

## 2026-10-06 — v0.4.5 HY2 主线收口

- [PR #20/#21](docs/HYSTERIA2_CLOSURE_045.md) 正常合并最终 master `838c66d9974dd9f3a944641a2e9e03cc200e0bbe`，tree `e12287d8ebbea233142d58191ee5141a0a49a17a`；最终八组/11 jobs/全部步骤 attempt 1 成功，真实链路 63 项与托管 HY2 8 项分别通过
- PR #20 来源候选 `c9d45aac7b8e6951e231d3ca41a9eb8507638510` 已完成独立审查/候选验收；首次合并 `dbf1cfbe` 的继承 Trojan/gRPC TLS 原因日志失败保留。PR #21 来源 `5ee7e35` 仅修复有界真实原因收集，保持零目标请求、无 DIRECT 和 x509 断言
- 首次裸前置 `6daff89e` 日志不足、第二前置 `e1800367` 链路 58 项通过但 ACME DNS TCP/UDP 端口碰撞，以及 PR #21 portable 初次 API rate limit 失败/attempt 2 成功均保留，不改写为首次成功。无 tag/Release、附件晋升或部署

以下原安全候选及 HY2 候选条目完整保留当时状态；“待完成”不覆盖以上已完成收口，也不证明当前 TUIC 通过。


## 2026-10-06 — 普通节点响应凭据收敛（安全修复候选）

- 修复共用 serializer 在已鉴权普通列表/创建/更新时回传原始 settings/stream_settings 的问题，覆盖统一接口与 Xray/sing-box 旧别名；改为元数据、凭据存在状态和托管证书适用性允许列表
- 普通响应不返回 UUID、协议/obfs 密码、REALITY 私钥、导入未知配置或服务器证书/密钥路径；这是旧管理客户端响应契约的有意破坏性收窄，见[API](docs/API.md)
- 特权 `/editor` 原有手工证书路径字符串能力保留，秘密不返回；数据库、核心应用、明确授权导出与备份不改写
- 仅有临时 SQLite/合成凭据的复现与回归；不宣称真实泄露事件，没有生产轮换或部署。候选准确提交独立审查与八组 CI、正常合并后的八组主线 CI 分别验收

## 0.4.5 — Hysteria2/TLS（候选，最终验收待完成）

- 从已验收 v0.4.4 gRPC 主线开始独立 [Draft PR #20](https://github.com/ForceMind/V-UI/pull/20)，固定 sing-box 1.14.2 / Mihomo 1.19.32、四目标 Linux、40 项 ToClash 和发布边界不变。
- 仅单密码、明确验证 SNI、原生 QUIC 默认值；标准 `hysteria2://` 编码密码、`sni` / `insecure=0`，Mihomo YAML、sing-box JSON 无损映射。仅 HTTP/TCP 负载，Mihomo `udp: false`、sing-box `network: tcp`；QUIC UDP 不等于应用 UDP。
- 新增可选 Hysteria2 Password，空输入在创建时生成、编辑时保留；编辑响应不回传秘密，新 HY2 不默认写入带宽/Chrome。既有高级/obfs 草稿保留或在无法表达时拒绝，严格公开导出不放开 obfs、hopping、带宽、ALPN/uTLS 覆盖、TUIC 或未知字段。
- 共用托管证书、续期失败保留旧材料和停止核心 `CORE_STOPPED_PENDING_APPLY`；新增真实浏览器创建/取消/编辑/刷新/再打开/导出与故意损坏后的停机恢复门槛。
- 安装器新增可重复 `--node-udp-port`（1024–65535），与 TCP 独立；新安装双栈 bind 预检、升级人工核对所有权、精确端口/协议确认、不自动启用防火墙。声明不持久保存、不创建节点，后续运行须再次传入。
- 准确前置 `e18003670c6469489c7a63413be0a3f9bd77cf0b` 的[真实链路](https://github.com/ForceMind/V-UI/actions/runs/37293701022)通过 58 项测试，双客户端实际 HTTP 与错误密码/CA/SNI 有真实认证/x509、零目标送达和无 DIRECT。首次 `6daff89e` 缺日志失败保留；第二前置 ACME 因 DNS TCP/UDP 测试端口碰撞失败，夹具预留修复已通过 4 项本地回归，准确候选 ACME 重跑仍待完成，不能写成八组全绿。
- 当前独立审查、最终集成 exact-head 八组、授权正常 merge 与 exact-master 八组仍待完成；未创建 tag/Release、部署或晋升当前附件。完整证据见[HY2 契约](docs/HYSTERIA2_045.md)。

## 2026-10-05 — v0.4.4 gRPC 主线收口

- [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并至 `84729dfc53165003e7d459a5d56621ce89ba497c`；候选 `240edf23af8a12b2cbd71114fe65c693290e39f2` 与合并 tree 同为 `200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc`，来源分支保留。
- 独立审查、八组 exact-candidate 与八组 exact-master 成功；最终主线 11 个 job、每一步成功。普通 discovery 298 项 OK（含 68 项明确环境 skip），激活真实链路 53 项通过，真实 Chromium、托管证书、安装/四目标门槛分别完成。skip 不作通过。
- gRPC Lite 限制不变，真实 x509 证据不等于及时向调用者返回 TLS 错误；第一次失败和前置通过保留。此阶段无 tag、Draft Release、公开 Release、部署或附件晋升。详见[gRPC 收口](docs/VLESS_GRPC_CLOSURE_044.md)。

以下 v0.4.4 候选条目保留编写当时的状态；“待完成”不覆盖以上已完成主线收口。

## 0.4.4 — VLESS/gRPC/TLS（候选，最终验收待完成）

- 从已验收的 v0.4.3 WS 主线开始独立 gRPC 版本；FastAPI/SQLite、40 项 ToClash、四目标 Linux、固定依赖/核心和发布边界不变。
- 固定官方 sing-box 1.14.2 未含 `with_grpc`，实际使用 gRPC Lite；Mihomo 1.19.32 不变。前置提交 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 的真实链路通过 45 项测试，首次 `008608a` 两个 x509 文本断言失败仍保留在[阶段记录](docs/VLESS_GRPC_044.md)。
- 候选新增仅 sing-box/VLESS/gRPC/TLS 的严格 URI/Base64、Mihomo 和 sing-box 导出；单 UUID、空 flow、明确 SNI、正常证书校验，ALPN 省略或仅 h2，Chrome fingerprint 独立可选。
- service_name 使用 `[A-Za-z0-9._-]{1,128}` 字面字符串，保留大小写及 `.`/`..`；拒绝路径/query/百分号转义/空白/Unicode/类型强转和未知 transport/TLS/authority/header/timer/multi-mode 字段，不靠编辑静默清除导入选项。
- 前置双客户端真实 HTTP/h2、四种独立 Chrome/ALPN 组合、边界 service、错误 UUID/CA/SNI/service/case 均有证据；失败目标无请求、无 DIRECT。测试专用 HTTP/2 诊断证明 sing-box 真实 x509 拒绝，但 Lite 调用者可能超时，不承诺及时错误传播。
- 共用编译器/编辑器/托管证书，保留隐藏 UUID、受支持 ALPN、证书绑定和停止核心 `CORE_STOPPED_PENDING_APPLY`；集成门槛覆盖三格式、浏览器创建/取消/编辑/刷新/再打开/恢复和续期。
- 仅新增 HTTP/TCP：Mihomo `udp: false`、sing-box 出站 `network: tcp`。不新增 authority/Host 白名单、核心重编译、反代、Xray gRPC、HY2/TUIC 或依赖更新。
- 最终独立审查、准确候选八组 CI、授权正常合并及准确主线八组 CI 仍待完成；前置通过不能代替集成验收。本阶段未晋升当前附件、创建 tag/Release 或部署。

## 2026-10-05 — v0.4.3 WebSocket 主线收口

- [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `master` `1b3ec40cd3bb640246d12afa104db0aec08ce336`；准确候选 `f9dfa611edcf5946bb4c8ae3cee59118d36930e9` 与合并 tree 均为 `7f7e2df30774f614ecfe6989f22fbf8447d842e5`，来源分支保留。
- 最终主线八组工作流、11 个 job 和每一步均成功，独立审查已完成；无 queued/running/skipped 计作成功。普通测试 263 项通过（50 项环境 skip 另计），真实 loopback 37 项、激活 Chromium 与证书/安装/四目标门槛独立通过。
- 已验收 sing-box VLESS/WS/TLS、单 UUID、空 flow、明确 SNI/验证证书、严格 path 和可选 DNS-style Host，三格式无损导出。Host 是客户端元数据，服务端不做 Host 白名单；不同合法 Host 接受与非法 Host 输入拒绝分别记录。
- ALPN 省略或仅 http/1.1、Chrome 独立可选，仅 HTTP/TCP；错误 UUID/CA/SNI/path 双客户端拒绝且无 DIRECT，保留真实 x509 证据；浏览器/证书/UUID/停机恢复完成。
- 本地 netlink/socket 环境阻断不写成运行通过，真实证据来自准确候选及最终 master CI。本阶段未创建 tag、Draft Release、公开 Release 或部署，也未晋升 WS 候选附件；历史 v0.4.2 套件未被替换。详见[WS 收口记录](docs/VLESS_WS_CLOSURE_043.md)。

## 2026-10-05 — v0.4.2 主线收口

- PR #1–#13、#15–#17 共 16 个 PR 正常合并至 `master` `0225ce4b1301e70068421e54c409b303d45cf812`，tree `1a642d38bbd55d8cd12ebfabdbd421624a7a08f3`；PR #14 排除。
- 最终主线八组最新工作流及 11 个 job 成功，四目标 Linux 附件摘要/清单/源码与版本核验完成。没有 Draft Release、新版本 tag、公开 Release 或部署。
- 这是候选阶段之后的状态更新；以下历史条目保留当时的候选描述与验收边界。来源见[主线收口记录](docs/MAINLINE_CLOSURE_20261005.md)。

## 0.4.2 — VMess/TCP/TLS（候选）

- 新增 sing-box VMess/TCP/TLS strict public export：Mihomo YAML、VMess URI/Base64、sing-box JSON。
- 仅接受单 UUID 用户、TCP、TLS、证书校验与明确 SNI；未知字段、非 TCP、关闭 TLS 明确拒绝。
- 新增真实 sing-box VMess 服务端 + Mihomo/sing-box 客户端 loopback，覆盖正确 UUID、错误 UUID、错误 CA/SNI 与 DIRECT 失败回退检查。
- 正常 merge-forward 继承 PR16/PR15/PR13/PR12 修复与三 cipher 双客户端 Shadowsocks TCP/UDP 验收。
- 修复入站编辑和证书卡片两处 VMess/TLS 托管证书选择器缺失，新增前端/API 正反回归和真实浏览器创建、取消、编辑、刷新、UUID 保留、三格式导出与停机恢复。
- 既有 VMess 负向用例补齐 sing-box 客户端的错误 UUID/CA/SNI；要求目标无请求、无 DIRECT 回退，两个客户端均保留真实 x509 CA/SNI 失败证据。
- 当前入口文档对齐 v0.4.2 候选，最终结果以 exact-head 全组 CI 为准，未公开发布。

## 0.4.1 — Shadowsocks AEAD 候选

- 正常 merge-forward 继承 PR15/PR13/PR12 已验收修复，当前文档和安装示例对齐候选版本。
- 保留三种 AEAD cipher 的 strict 导出和固定真实客户端配置检查，以及 method 编辑时的密码保护。
- 补齐三种既有 cipher 的双客户端真实 TCP/UDP 正向及错误密码/错误 method 拒绝用例，要求目标不收到失败请求且不 DIRECT 回退。

## 0.4.0 — Trojan/TCP/TLS 候选

- 补齐合入后的协议默认安全模式回归：Trojan 省略/空/null security 仍按 TLS 保护续期；只有替换两条手工材料路径才可解除绑定。VLESS none/REALITY 与空 profile 语义保留。
- 正常合入已验收的 PR #13 节点编辑与 PR #12 Linux 安装修复；当前安装、发布和文档索引示例统一跟随 VERSION，并增加防漂移回归。
- 新增 sing-box Trojan/TCP/TLS strict public export：Mihomo YAML、Trojan URI/Base64、sing-box JSON。
- 单密码用户、TLS/SNI/证书校验、可选 ALPN/Chrome fingerprint 经过固定客户端验证。
- 真实 sing-box 服务端 + Mihomo 客户端 loopback 验证正确密码连通、错误密码拒绝、错误 CA/SNI 拒绝且不回退 DIRECT。
- 托管证书、自动续期和证书页面绑定扩展到 Trojan/TLS 节点。
- 未验证的 Xray Trojan、WS/gRPC Trojan 等继续明确拒绝，不随本版本放开。

## 0.3.2 — 节点编辑修复候选

- 合入已验收的 PR #12 Linux 安装修复，保留依赖 PR 链与发布边界。
- 修复托管 TLS 节点切换 `none` / `REALITY` 后被隐藏证书选择改回 TLS，以及非 TLS 解绑错误要求证书路径的问题。
- 保留 TLS 手工解绑保护、秘密保留和核心/协议锁定；空 profile 不会意外解除续期。
- 增加实际 Vue/Chromium、固定核心与临时 CA 的创建、编辑、取消、刷新、再编辑、TLS 导出和停机恢复回归；该编辑测试不扩大协议支持范围。

## 0.3.1 — Linux 可移植安装与发布链

- Linux 安装回归修复：OpenRC HTTP-01 在 bind 前设置 IPv6-only；新安装双栈预检 HTTP-01/默认节点端口；firewalld 仅处理明确的活动接口 zone，歧义时保留现有规则并等待人工核对。

- 安装器从 Ubuntu 24.04/amd64 白名单改为检测发行版、CPU、libc、init、包管理器和防火墙能力。
- 支持 x86_64 / ARM64 与 glibc / musl 四种目标运行包，运行服务使用固定便携 CPython 3.12 和 hash-locked wheels。
- sing-box 按 glibc/musl 选择官方对应构建；Xray 按 CPU 架构选择固定官方构建。
- systemd 与 OpenRC 分别使用受管服务后端；主面板保持非 root。
- 安装前检查 TCP 80、面板端口和默认节点端口。UFW/firewalld 只有用户明确确认后才修改；自定义 nftables/iptables 和云安全组仅提示人工处理。
- 在线 `install.sh --version` 自动检测目标并下载对应 Release 包。
- 正式发布门槛增加 portable Linux matrix；四个目标包必须来自同一 exact-head 提交的已验收 artifact，发布阶段不重新编译。


## 0.3.0 — 正式发布准备

本条记录代码目标版本，公开发布日期由正式Release动作确定，不把候选CI完成时间写成已公开发布。

### 新增

- 单管理员认证、会话撤销、持久限流、默认管理鉴权与独立只读订阅。
- 双核心安全配置应用、候选校验、手动停启与恢复状态。
- ToClash固定参考集成，完整Mihomo配置、40项服务目录、自定义DNS/规则与持久分流工作区。
- Certbot HTTP-01图形化签发、测试/正式隔离、自动续期、到期与任务状态、面板热更新、TLS节点绑定。
- Ubuntu24.04 amd64一键受管安装，非root服务、systemd自启和HTTP-01 socket、首次证书和管理员引导。
- 固定依赖离线套件、完整清单/摘要、备份恢复、升级失败保护及人工受门槛约束的正式发布流程。
- 用户、证书、运维、API、兼容、安全、贡献与发布文档。

### 修复与收口

- 不再使用mock登录或公开管理/订阅凭据出口。
- 不再把无节点/未知组合静默导出成DIRECT，也不把生成器测试当成真实兼容性证明。
- 移除被隔离站点导入时写入安装目录的副作用；主机防火墙操作不再假报成功。
- 核心固定版本字段经实际二进制核验；旧笼统REALITY兼容声明不作为支持承诺。
- 证书签发与消费者应用状态分离，续期失败不覆盖旧材料，不自动启动手动停止的核心。

### 已知边界

公开导出与实际链路首轮仅覆盖sing-box/VLESS/TCP/TLS单用户、空flow、验证证书。其他协议、UDP、DNS-01/通配符、ACME泛化provider、Docker/ARM64和任意YAML导入不在本版本验收范围。

历史各阶段提交与CI见[迭代记录](docs/ITERATIONS.md)。

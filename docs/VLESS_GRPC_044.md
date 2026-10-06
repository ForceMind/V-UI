# v0.4.4 VLESS/gRPC/TLS 验收契约

## 2026-10-05 状态补记

v0.4.4 已完成独立审查、准确候选八组 CI、[PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并与准确主线八组 CI，现为已验证继承基线。最终 master 为 `84729dfc53165003e7d459a5d56621ce89ba497c`，候选为 `240edf23af8a12b2cbd71114fe65c693290e39f2`，相同 tree 为 `200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc`；最终 11 个 job 和每一步全部成功。完整证据、限制和发布边界见[gRPC 主线收口](VLESS_GRPC_CLOSURE_044.md)。

**以下正文是原阶段历史，完整保留当时“候选/待完成”的描述、前置通过和首次失败，不代表当前 v0.4.4 状态。** 当前独立开发阶段见[v0.4.6 TUIC v5/TLS 契约](TUIC_046.md)；尚未因为本页收口而发布、部署或晋升附件。

## 历史：状态与前置验证

v0.4.3 已由 [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，该准确主线的八组工作流、11 个 job 和每一步均成功，详见[WS 收口](VLESS_WS_CLOSURE_043.md)。它不构成 gRPC 支持证据。

本阶段先刻画固定官方二进制的实际互通行为，再集成可视化与公开配置。前置提交 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 的[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37288165901)通过 45 项测试，满足开始集成的前提；本页保留第一次未通过的运行与本地沙箱阻断。

**当前 v0.4.4 是集成候选，尚未最终验收或发布。** 独立审查、准确候选八组门槛、授权正常合并和准确主线八组门槛仍须分别完成。前置裸核心链路不能代替公开订阅、编辑器、证书、浏览器和安装套件的最终验证。若真实互通失败，报告不兼容，不替换核心、重编译或增加反向代理来掩盖。

| 固定对象 | 版本 / 事实 |
| --- | --- |
| 服务端与 sing-box 客户端 | 1.14.2；现有官方构建没有 `with_grpc`，使用 gRPC Lite |
| Mihomo 客户端 | 1.19.32 |
| 二进制来源 | 继续使用 `scripts/fetch_test_cores.py` / `scripts/fetch_mihomo.py` 的现有校验固定下载 |
| Linux / 分流 | 既有 x86_64/ARM64 × glibc/musl 四目标和 40 项 ToClash，不扩大范围 |

固定源码用于解释测试结果，不能代替真实转发：
[sing-box 构建分流](https://github.com/SagerNet/sing-box/blob/v1.14.2/transport/v2ray/grpc_lite.go)、
[sing-box Lite 服务端](https://github.com/SagerNet/sing-box/blob/v1.14.2/transport/v2raygrpclite/server.go)、
[Mihomo Gun 传输](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/transport/gun/gun.go)。两端将 service name 映射到 `/service_name/Tun`；固定服务端不提供 authority/Host allowlist，本阶段不把它描述为鉴权边界。

## 限定公开契约

- 仅 sing-box / VLESS / gRPC / TLS，单 UUID，省略或空字符串 flow
- `service_name` 必填，严格匹配 `[A-Za-z0-9._-]{1,128}`；保留大小写和字面值，包括完整 `.` / `..`，不 trim、编码、路径归一化或类型强转；不允许前导 `/`、内嵌 path/query、百分号转义、空白、Unicode 或非字符串
- 显式 SNI、正常证书验证和 HTTP/2；ALPN 省略或恰为 `["h2"]`，显式 null 拒绝，Chrome fingerprint 独立可选
- 三格式分别保留 URI `type=grpc` / `serviceName`、Mihomo `grpc-opts.grpc-service-name`、sing-box `transport.service_name`，以及 UUID、TLS/SNI、可选 ALPN/指纹
- 仅新增 HTTP/TCP：Mihomo `udp: false`，sing-box 出站 `network: tcp`；不声明 UDP 能力
- 拒绝未知 transport/header/authority、health timer、多模式等未验收选项；已有无法由表单表达的参数不能因编辑而消失，畸形导入值必须由用户明确修正后才可公开
- 沿用托管证书、隐藏且保留 UUID、编辑/取消/刷新/回填/停机恢复、材料与应用状态分离、停止核心后的 `CORE_STOPPED_PENDING_APPLY`
- 私钥、服务端证书路径和密钥路径不得进入公开导出；失败不得静默 DIRECT

## 真实验证要求

`test_vless_grpc_preflight_loopback.py` 使用临时 CA、临时监听器和假 UUID，在未启用导出前直接运行固定核心：

1. 真服务端与两种客户端配置检查，默认/Chrome-only/h2-only/Chrome+h2 四种组合分别验证
2. TLS 握手实际协商 `h2`；正确客户端实际送达 HTTP 目标
3. 每种客户端错误 UUID、CA、SNI、service name 拒绝；目标先证明 IP 可直达，失败时不得收到请求，两种 TLS 错误有真实证书错误证据
4. 不使用 mock 核心、跳过 TLS、全局信任修改、替代代理或修改固定下载摘要

本地沙箱如拒绝 netlink/socket/Chromium，保留失败尝试并报告运行阻断；配置检查通过不等于真实链路通过，环境 skip 不等于通过。实际未削弱 CI 是运行门槛。

## 本地前置检查记录（2026-10-05）

固定服务端与两种客户端的配置检查通过，包含默认/Chrome/h2 独立组合与语法合法的错误 UUID、CA、SNI、service name。首次真实运行在服务端监听前失败：`start service: create netlink socket: operation not permitted`。这是当前本地沙箱阻断，不是已证明的协议不兼容，也不是运行通过；首次失败日志保留，真实转发和拒绝路径交由同一固定二进制的 CI 执行。

## 首次真实 CI 记录（保留失败）

前置提交 `008608afacf119780eacce97df0207d5e0fa9c9f` 的[首次真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37287322011)总计 45 个测试，两个断言失败，其余七组工作流成功。两种固定客户端已实际通过四种 Chrome/h2 正向组合、`.` / `..` / 128 字符字面 service name，协商 h2；错误 UUID、service name 和仅大小写差异均无目标送达。

失败项是 sing-box gRPC Lite 的错误 CA/SNI 测试：客户端请求未送达目标，但日志未在观察窗口出现要求的 `x509` 字样。Mihomo 两项有明确 x509 错误。不能把超时或没有目标请求单独等同于 TLS 拒绝证据；因此该次前置门槛未通过，当时公开导出保持关闭。

固定 [Lite client](https://github.com/SagerNet/sing-box/blob/v1.14.2/transport/v2raygrpclite/client.go) 与 [connection](https://github.com/SagerNet/sing-box/blob/v1.14.2/transport/v2raygrpclite/conn.go) 源码显示异步 RoundTrip 将错误保存在延迟连接，而写入使用独立 pipe；错误可未及时到达普通客户端日志。后续验证只为负向测试的 sing-box 子进程开启 HTTP/2 诊断日志，保留真正的 x509、正向控制与无目标送达断言；不改核心、TLS 行为或 pin，不用日志修复推导及时错误传播。

## 固定二进制前置通过（2026-10-05）

准确前置提交：`a0205fe545fabd958fe7aa80835a3ec6abeada92`。对应[真实链路 CI](https://github.com/ForceMind/V-UI/actions/runs/37288165901)全部 45 项测试通过，固定核心、客户端版本和摘要未改变。

- 运行版本明确确认 sing-box 1.14.2 无 `with_grpc`，测试实际 gRPC Lite；Mihomo 保持 1.19.32。
- 两种客户端分别通过默认、仅 Chrome、仅显式 h2、Chrome+h2 四种独立组合；真实 TLS 握手协商 `h2`，HTTP 请求实际送达目标。
- 字面 `.`、`..` 和 128 字符 service name 都实际连通，大小写保留；不依据 path normalization 猜测行为。
- 两种客户端错误 UUID、CA、SNI、service name 及仅大小写差异均拒绝，无目标请求、无 DIRECT 回退；目标 IP 的直连可达性先独立证明。
- 只有 sing-box 负向测试子进程开启 `GODEBUG=http2debug=1`，HTTP/2 诊断暴露真实 unknown-CA / wrong-name x509 错误；Mihomo 保留自己的 TLS 错误证据。没有更换二进制、关闭证书校验、修改主机信任、放宽拒绝断言或增加代理。

### 已知 gRPC Lite 错误传播限制

前置通过证明固定二进制互通及上述失败拒绝，并不证明及时向调用者返回错误。**错误 CA/SNI 下，实际 sing-box Lite 调用者可能等待到超时，而不是立即得到 TLS 原因。** 异步 HTTP/2 诊断提供可观测的真实 x509 错误；普通请求的超时和目标无请求本身仍不足以归因 TLS，不能写成“及时 TLS 错误传播已验收”。诊断设置只作用于测试子进程，生产默认日志没有因此改变。

固定 Lite 服务端没有 authority/Host allowlist，service name 是字面路由选择；本候选不提供或宣称 authority/Host 访问控制。仅 HTTP/TCP，现有 Shadowsocks 的 UDP 验收也不延伸到 gRPC。

## 集成候选与待完成门槛

候选复用现有编译器、视觉编辑器、严格订阅与托管证书，不新增后端、依赖、迁移或核心下载变更。

- `test_vless_grpc_profile.py`：严格字面输入、三格式映射、未知字段/不支持组合拒绝、导入配置无静默字段损失、编辑往返、UUID 与受支持 ALPN 保留。
- `test_vless_grpc_credentials.py`：已有节点缺失/畸形 UUID 编辑返回 422，不重新生成；省略 flow/fingerprint/skip 字段保留原值，不使错误导入被静默修正为可公开配置。
- `test_export_real.py`：公开导出的 Mihomo/sing-box 配置由固定实际客户端检查；配置检查不能代替转发。
- `test_vless_grpc_loopback.py`：由应用编译、公开订阅生成真实客户端配置，再运行正向和拒绝链路；与前置裸核心测试分开记录。
- `test_inbound_editor_browser.py`：创建、取消、编辑 service name、刷新/再打开/回填、隐藏 UUID 不变、三格式导出、停机备份/恢复。
- `test_vless_grpc_managed_certificate.py` 与证书回归：绑定/改绑/解绑、续期保留 transport/凭据、失败保留旧材料、停止核心 `CORE_STOPPED_PENDING_APPLY`。
- 最终独立审查、准确候选八组 CI、授权正常 merge，以及准确 master 八组 CI 均待完成；结果必须指向最终提交，不能引用 a0205fe 的裸核心前置通过冒充全套集成成功。

## 发布与下一阶段

本阶段仅独立 Draft PR 与计划内正常提交/推送。合并须等独立审查和八组 exact-head 验收后由父任务授权，再核验八组 exact-main。没有 tag、Release、部署、真实 CA 账户、生产凭据、主机防火墙或 HY2/TUIC 扩展。

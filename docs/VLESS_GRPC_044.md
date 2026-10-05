# v0.4.4 VLESS/gRPC/TLS 验收契约

## 状态与前置验证

v0.4.3 已由 [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，该准确主线的八组工作流已完成。它不构成 gRPC 支持证据。

本阶段首先刻画固定官方二进制的实际互通行为，尚未放开新的公开导出。只有真实服务端和两种客户端的前置链路通过后才实现可视化/公开配置；最终独立审查、准确候选和准确主线八组门槛仍须分别完成。若真实互通失败，报告不兼容，不替换核心、重编译或增加反向代理来掩盖。

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
- `service_name` 必填，1–128 个 ASCII 字符，只允许字母、数字、`.`、`_`、`-`；保留大小写和字面值，不 trim、编码或类型强转；不允许 `/`、query、百分号转义、空白或非字符串
- 显式 SNI、正常证书验证和 HTTP/2；ALPN 省略或恰为 `["h2"]`，Chrome fingerprint 独立可选
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

## 发布与下一阶段

本阶段仅独立 Draft PR 与计划内正常提交/推送。合并须等独立审查和八组 exact-head 验收后由父任务授权，再核验八组 exact-main。没有 tag、Release、部署、真实 CA 账户、生产凭据、主机防火墙或 HY2/TUIC 扩展。

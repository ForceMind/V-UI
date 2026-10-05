# v0.4.5 Hysteria2/TLS 验收契约

## 前置阶段

基线是 [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并后的 `84729dfc53165003e7d459a5d56621ce89ba497c`，准确主线八组工作流、11 个 job 和全部步骤已通过。gRPC 的通过不构成 Hysteria2 证据。

本阶段先以裸配置刻画不变的官方 sing-box 1.14.2 服务端/客户端及 Mihomo 1.19.32。配置检查已本地通过；实际链路仍待前置 CI。未开启 Hysteria2 公开导出，也不声称完整支持。若固定二进制缺乏能力，报告不兼容，不换 pin、不重编译、不降低 TLS 校验。

## 限定契约

- sing-box、Hysteria2、单密码、显式 SNI、正常证书验证、原生 QUIC；默认 ALPN 和默认拥塞行为，不加入 ALPN/uTLS 指纹/带宽覆盖
- 仅 HTTP/TCP 应用负载通过 QUIC；Mihomo `udp: false`，sing-box 出站 `network: tcp`
- QUIC 自身需要节点端口的 UDP 监听与云/主机防火墙通行；不等于应用 UDP 转发已验收，TCP 端口开放不能代替 UDP
- 标准 `hysteria2://` URI、Mihomo YAML、sing-box JSON 保存密码与 SNI/校验语义；不得输出服务器私钥、证书或密钥路径，不得失败后 DIRECT
- 不含 obfs、port hopping、多用户、masquerade、realm、TUIC 或额外带宽/拥塞调优；已有 obfs 设置及秘密不得因为普通编辑被静默抹除
- 共用编辑器/证书生命周期；密码不回传编辑表单、空白保留；创建/取消/编辑/刷新/再打开/导出/停机备份恢复与续期失败保留旧材料分别验收
- 安装器不自动启用主机防火墙；任何已支持后端的规则变更均先列精确端口/协议并取得明确 yes，云安全组/自定义规则保持人工确认
- 四目标 Linux、40 项 ToClash、依赖和二进制 pins 不变；无真实证书申请、账户、生产、tag 或 Release 变更

官方协议参考：[URI 规范](https://v2.hysteria.network/docs/developers/URI-Scheme/)、[sing-box 服务端](https://sing-box.sagernet.org/configuration/inbound/hysteria2/)、[sing-box 客户端](https://sing-box.sagernet.org/configuration/outbound/hysteria2/)、[Mihomo Hysteria2](https://wiki.metacubex.one/en/config/proxies/hysteria2/)。协议文档只是设计依据，支持边界以固定二进制实际测试为准。

## 门槛与证据

`test_hysteria2_preflight_loopback.py` 使用临时 CA、隔离监听器和假密码。先证明 HTTP 目标 IP 可直接到达，再逐客户端验证正常转发和错误密码/CA/SNI；负向每次只改变一项，必须零目标请求、无 DIRECT，并记录真实认证或 x509 拒绝原因。超时不能单独充当 TLS 拒绝证据。

前置通过后才实现完整公开配置、编辑器与托管证书测试。随后仍须独立审查、准确候选八组 CI、授权正常合并和准确主线八组 CI；不能复用旧主线结果。当前附件未晋升或发布。

## 首次前置失败记录

首个前置提交 `6daff89e562c93c77b797aa27131b1a438e654a9` 的[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37292930480)证明两种客户端均可通过 QUIC 转发 HTTP，但 Mihomo 的一例错误 CA 没有取得 x509 日志，继承的 gRPC 前置一例 Mihomo 错误 SNI 也没有原因日志，故整组失败。不能将其记作通过。

后续测试仅在限定观察期内重复失败请求，每次仍必须零目标送达，最后仍必须出现真实 x509 原因。密码证据收紧为 `authentication failed`，避免将本地 mixed listener 的 `Auth success` 当作 HY2 拒绝证明。未换二进制、跳过 TLS、改协议或加 DIRECT；结果待新准确提交 CI。

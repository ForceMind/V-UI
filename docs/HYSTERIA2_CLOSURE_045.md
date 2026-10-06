# v0.4.5 Hysteria2/TLS 主线收口

本页更新 2026-10-06 已完成的 HY2 状态。[原阶段契约](HYSTERIA2_045.md)完整保留当时的候选描述、首次失败和后续修复，不以当前成功改写历史。后续 [TUIC v0.4.6](TUIC_046.md) 仍须独立验收；没有由此创建 tag、Release、部署或晋升附件。

## 准确提交与失败历史

- [PR #20](https://github.com/ForceMind/V-UI/pull/20) 候选 `c9d45aac7b8e6951e231d3ca41a9eb8507638510` 完成独立审查与八组候选门槛，正常合并至 `dbf1cfbe7e3799481d6b8bffec3e028f7367219b`
- 该首次主线的[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37351940554)因继承 Trojan/gRPC 的真实 TLS 原因日志断言失败，不能记为全绿；失败使后续托管证书命令未执行
- 独立 [PR #21](https://github.com/ForceMind/V-UI/pull/21) 的 `5ee7e35` 修复有界负向原因收集；不修改产品、核心、TLS 验证或安全断言。首次 portable 候选因 API rate limit 失败，attempt 2 通过；不能隐去首次失败
- PR #21 正常合并后的准确 master 为 `838c66d9974dd9f3a944641a2e9e03cc200e0bbe`，tree `e12287d8ebbea233142d58191ee5141a0a49a17a`；独立审查、准确候选及最终主线门槛完成，最终主线八组工作流、11 个 job、全部步骤均为 attempt 1 成功
- 更早裸前置 `6daff89e` 缺少真实原因日志的失败，以及 `e1800367` 的真实链路 58 项通过但 ACME DNS TCP/UDP 测试端口碰撞失败，仍完整保留在[原契约](HYSTERIA2_045.md)。后来的成功不把它们改成通过

## 最终 master 工作流

以下均对应上述准确 `838c66d9`，不是 TUIC 或后续安全修复提交的证据：

| 门槛 | 最终 master 记录 |
| --- | --- |
| 普通/真实核心/浏览器 | [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/37354363867) |
| 真实链路 63 项及独立托管 HY2 8 项 | [Real loopback](https://github.com/ForceMind/V-UI/actions/runs/37354364040) |
| 40 项 ToClash | [ToClash](https://github.com/ForceMind/V-UI/actions/runs/37354363953) |
| 真实 HTTP-01/续期 | [ACME](https://github.com/ForceMind/V-UI/actions/runs/37354363880) |
| 一键安装/升级 | [Installation](https://github.com/ForceMind/V-UI/actions/runs/37354364064) |
| 四目标 Linux | [Portable](https://github.com/ForceMind/V-UI/actions/runs/37354363901) |
| 文档/发布契约 | [Documents](https://github.com/ForceMind/V-UI/actions/runs/37354364016) |
| 临时包部署门槛 | [Deployment gates](https://github.com/ForceMind/V-UI/actions/runs/37354364007) |

已验证范围仍为固定官方 sing-box 1.14.2 / Mihomo 1.19.32、单密码、明确验证 SNI、原生 QUIC 默认值、HTTP/TCP。三格式、公开订阅链路、Chromium 创建/取消/编辑/刷新/再打开/导出/损坏后停机恢复、证书绑定/续期/失败保护/停止待应用和协议区分 UDP 安装预检分别验收。QUIC 需要节点 UDP 通行，不代表应用 UDP 验收。

证据收集只允许在有界观察期内重复已被拒绝的请求，固定目标计数从第一次负向前开始；必须有真实认证或 x509 与具体原因、零目标送达、无 DIRECT。超时或一般 TLS 日志不算拒绝证明；没有重置计数掩盖晚到数据。

后续 [PR #23 安全收口](INBOUND_RESPONSE_CLOSURE_20261006.md)是当前 TUIC 候选的另一项继承依赖。已验收源码、原始 CI artifact、已核验附件、正式发布与真实部署始终分开记录。

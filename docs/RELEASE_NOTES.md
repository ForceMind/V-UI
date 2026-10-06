# V-UI 0.4.7

REALITY/Vision 已由 [PR #24](https://github.com/ForceMind/V-UI/pull/24) 完成独立审查、准确候选八组、正常合并和准确主线八组，见[收口记录](REALITY_VISION_CLOSURE_047.md)。本文件仍为发布准备说明；没有创建正式 Release、附件晋升或生产部署。当前 XHTTP 刻画不改变产品版本 0.4.7 或公共支持。

## 继承基线

[v0.4.2](MAINLINE_CLOSURE_20261005.md)、[WS](VLESS_WS_CLOSURE_043.md)、[gRPC](VLESS_GRPC_CLOSURE_044.md)、[HY2](HYSTERIA2_CLOSURE_045.md)、[PR #23 普通响应安全修复](INBOUND_RESPONSE_CLOSURE_20261006.md)与 [TUIC v0.4.6](TUIC_CLOSURE_046.md)分别保持已验收边界，PR #14 仍排除。TUIC master `df8a980b` 八组/11 jobs/全部步骤成功，deployment 首次上游 403 后 unchanged-code attempt 2 通过，其余七组 attempt 1。旧[候选发布说明](RELEASE_NOTES_046.md)保留为历史。

## 已验收范围

- sing-box 1.14.2 服务端/客户端与 Mihomo 1.19.32，官方 pin/摘要/构建不变；VLESS、direct TCP、精确 `xtls-rprx-vision`、一个 UUID、一个规范 16 位 hex short ID、匹配 X25519 公私钥、显式 SNI、Chrome
- VLESS URI、Mihomo YAML、sing-box JSON 保留必要客户端参数；私钥及参考握手地址仅在服务器。无 ALPN 覆盖、托管证书、额外传输/核心、multiplex 或应用 UDP 扩围
- UUID/私钥/short ID 不回显，空输入保持；独立 UUID/short ID 替换，未知/高级导入字段拒绝，不能静默清除后公开。普通响应保持 PR #23 allowlist
- 参考 TLS 握手与应用目标分别计数；认证失败可产生参考回落，异步伪装 GET 不保证完成。错误 UUID/flow/key/short ID/SNI 要求真实拒绝原因、应用零送达、无 DIRECT；不以超时/EOF 代替
- REALITY 证书绑定在持久化前拒绝；有效 legacy raw 安全切换在保存后清除旧绑定。复现只观察到旧代码残留 binding/error 状态，未观察到 REALITY 被续期覆盖；有效 TLS 的 desired/applied 与停止待应用语义保留

## 当前证据

裸前置准确 `a44b5ce20edcaee1ee3b26c34b7c21badfe5bf7b` 八组/11 jobs/全部步骤 attempt 1 成功。[链路 82 项](https://github.com/ForceMind/V-UI/actions/runs/37471617751)及独立 HY2 8/TUIC 8 通过，Mihomo YAML、sing-box JSON、实际 URI importer 均验证正向和五类负向。首次 `6776950` 的 9 个伪装 HEADERS 断言失败保留，修正没有更换核心或放松认证/应用送达门槛。

最终候选 `70cc2f4` 与正常签名 master `6b049262` tree 相同；候选八组全部 attempt 1，主线八组最终成功。主线链路首次继承 VMess/Mihomo CA 用例缺少必需 x509 原因，unchanged-code attempt 2 取得真实 unknown-authority 原因后通过，其余七组 attempt 1；该重跑不证明被动日志轮询不稳定性已永久修复。真实链路 89 项及独立 HY2 8/TUIC 8，公开订阅、严格编辑、Chrome/秘密回填、取消/刷新/再编辑/导出及损坏后停机恢复均有准确主线证据。原有 ACME/安装/40 项 ToClash/四目标 Linux 与完整八组不减，见[主线收口](REALITY_VISION_CLOSURE_047.md)；完整来源与前置/审查失败历史见[阶段契约](REALITY_VISION_047.md)。

下一阶段仅 [XHTTP 刻画前置](XHTTP_CHARACTERIZATION_048.md)。sing-box 1.14.2 不支持 XHTTP，HTTPUpgrade 另有固定 Mihomo URI 转换缺口，公开导出保持阻断，准确提交的真实 CI 验收仍待完成。

历史本地 netlink/socket/Chromium EPERM 按原结果保留，不写成运行通过；当前 XHTTP 本地运行与 HTTPUpgrade netlink 阻断另见刻画记录，不泛化为所有本地测试不可运行。源码、配置检查、真实链路、浏览器、包验证、发布与部署分别记录。无 tag/Release、附件晋升、真实 CA/账户或实际防火墙变更。

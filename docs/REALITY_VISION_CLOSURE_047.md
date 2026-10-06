# v0.4.7 REALITY / Vision 主线收口

## 准确提交与结论

[PR #24](https://github.com/ForceMind/V-UI/pull/24) 已在 2026-10-06 正常合并。以下结果只对应这些准确提交，不替代后续变更的验收：

- 最终候选：[`70cc2f4f7dc4349c7ecffbc33fc088f51b8651fa`](https://github.com/ForceMind/V-UI/commit/70cc2f4f7dc4349c7ecffbc33fc088f51b8651fa)
- 正常签名 master：[`6b049262457b259c602c5e74be296ef52780c462`](https://github.com/ForceMind/V-UI/commit/6b049262457b259c602c5e74be296ef52780c462)
- 相同 reviewed tree：`316f0159e3ae3d7362022d9c9ac61bc0afa867c1`
- master parents：已验收 TUIC `df8a980beb682a981d72e42760705f1831cacf9b` 与最终候选 `70cc2f4f7dc4349c7ecffbc33fc088f51b8651fa`

独立源码审查在复现并修复非直连 REALITY 草稿未知字段丢失问题后通过。最终候选八组工作流、11 个 job、全部步骤 attempt 1 成功。最终 master 八组、11 个最新 job、全部步骤成功；链路为 unchanged-code attempt 2，其余七组 attempt 1。首次失败并未被重跑覆盖，详见下节。

## 八组工作流

| 工作流 | 最终候选 70cc2f4 | 最终 master 6b049262 |
| --- | --- | --- |
| Documents and release contracts | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605862) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768903) |
| Test V-UI | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605790) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768808) |
| Real loopback proxy and DNS chain | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605785) | [attempt 2 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768982/attempts/2)，attempt 1 失败保留 |
| ToClash reference and export verification | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605796) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768926) |
| ACME certificate acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605816) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768809) |
| One-command installation acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605894) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479769207) |
| Portable Linux runtime matrix | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605799) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768985) |
| Selected release deployment gates | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37478605807) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37479768791) |

Portable matrix 含四个目标 job：x86_64/ARM64 × glibc/musl。临时 CI deployment gate 不表示生产部署或附件晋升。

## 实际验收内容

准确 master 的链路门槛执行 89 项，另有独立 HY2 托管证书 8 项与 TUIC 托管证书 8 项。固定双客户端和实际 Mihomo URI provider 均转发 HTTP。错误 UUID、缺少 Vision flow、错误合法公钥、错误规范 short ID、不允许的 SNI 分别取得真实拒绝原因、应用目标零送达和无 DIRECT。key/ID/SNI 失败的参考 TLS 回落单独观察；异步伪装 HEADERS 可以为 0，只按实际数记录。

准确 master 的 [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/37479768808) job `112324519336` 默认测试共 469 项，其中 109 项为明确环境 skip；真实核心与浏览器由单独激活的门槛验证，不能把这 109 项算作本地运行通过。实际 Chromium 覆盖 REALITY 创建、取消、编辑、刷新、再打开、UUID/short ID 独立替换、三格式导出、非法输入和损坏后的停机恢复，普通响应仍无秘密。

只验收 HTTP/TCP 应用流量，不包含应用 UDP、XUDP/DNS 专项或 Vision 性能优化承诺。

## 保留失败与审查历史

1. 裸前置 [`6776950`](https://github.com/ForceMind/V-UI/commit/67769509401804fbbdd617acd01d047424035f0b) 的[首次链路](https://github.com/ForceMind/V-UI/actions/runs/37470333699)有 9 项错误的必需伪装 HEADERS 断言失败。`a44b5ce` 改为固定核心确实保证的参考 TLS 回落证据；二进制、信任、认证原因、零应用送达和无 DIRECT 门槛未变。其八组/11 jobs/全部步骤 attempt 1 成功、链路 82 项及独立 HY2 8/TUIC 8，与最终集成分开记录于[阶段契约](REALITY_VISION_047.md)。
2. 集成 `243434aff287afb6ceecfd1eb9f3de41b12e39c3` 八组/11 jobs/全部步骤 attempt 1 成功，链路 89 项和 Chromium 成功，但独立审查仍阻断非直连 REALITY 草稿在转换时静默丢失未知字段的问题。最终 `70cc2f4` 在 `ensure_credentials` 前验证原始凭据/旧状态，并保留省略的可表达传输字段；经过重新审查和完整八组后才合并。CI 全绿没有代替审查。
3. master `6b049262` 的[首次链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/37479768982/attempts/1)中，继承 VMess/Mihomo untrusted-CA 测试在被动 2 秒轮询内仅取得启动日志，缺少必需 x509 原因；整组保持失败。未更改代码、pin 或凭据的失败 job attempt 2 取得真实 unknown-authority x509 日志并成功。**这次重跑不证明旧被动日志轮询不稳定性已永久修复。** 后续提交仍须要求真实原因，不能接受超时、EOF 或单独零送达。
4. [TUIC 收口](TUIC_CLOSURE_046.md)、[HY2 收口](HYSTERIA2_CLOSURE_045.md)与 [PR #23 安全收口](INBOUND_RESPONSE_CLOSURE_20261006.md)保留各自失败历史，包括 TUIC 首次 master deployment 上游 403 后 unchanged-code attempt 2 成功。

## 继承边界

- 官方 sing-box 1.14.2 服务端/客户端与 Mihomo 1.19.32 的 pin、摘要、构建不变
- VLESS/direct TCP/精确 `xtls-rprx-vision`，一个规范 UUID、一个小写 16 位 hex short ID、匹配的规范 X25519 pair、显式字面 DNS SNI、Chrome；无 ALPN 覆盖、multiplex、其他核心/传输或多用户
- URI、Mihomo YAML、sing-box JSON 仅包含客户端所需信息；服务端私钥和握手参考地址不导出。URI importer 的 `udp=true` 不构成应用 UDP 验收或禁用 UDP 承诺
- 普通响应继续 [PR #23 allowlist](INBOUND_RESPONSE_CLOSURE_20261006.md)。UUID/私钥/short ID 不回显、空输入保留；特权 `/editor` 手工证书路径字符串和明确授权客户端导出仍分别处理
- 未知/高级导入保留或拒绝，不能借编辑补齐缺失凭据、静默清除字段后解锁公开支持
- REALITY 不适用托管证书；非法绑定/更新保持原状态，明确合法转换保持既有 desired/applied 与停止待应用语义，备份恢复不改变凭据
- 四目标 Linux、40 项 ToClash、所有已验收协议、证书、安装和八组门槛保持

真实 runtime/Chromium 在隔离 CI 验收；REALITY 验收时本地 socket/netlink/Chromium EPERM 仍按历史环境限制保留，不能覆盖后续单独取得的本地运行证据。没有 tag、Draft Release、公开 Release、附件晋升、生产部署、真实 CA/账户或主机安全设置变更。

后续 [v0.4.8 XHTTP 刻画](XHTTP_CHARACTERIZATION_048.md)从此准确主线独立推进；产品版本仍为 0.4.7，能力刻画不自动产生公共支持。

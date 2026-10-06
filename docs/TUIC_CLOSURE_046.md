# v0.4.6 TUIC v5/TLS 主线收口

本页记录 2026-10-06 已完成的 TUIC 状态。[原阶段契约](TUIC_046.md)保留裸前置、集成重建、当时的 pending 状态与环境阻断；当前收口以上述历史之后的准确候选和主线证据为准。已合并源码不等于附件晋升、正式发布或实际部署。

## 准确提交与验收

- [PR #22](https://github.com/ForceMind/V-UI/pull/22) 的最终候选为 [`14f570e8406098aa7570f2daaf24bdbe550a1858`](https://github.com/ForceMind/V-UI/commit/14f570e8406098aa7570f2daaf24bdbe550a1858)，独立源码审查完成，八组工作流、11 个 job、全部步骤均为 attempt 1 成功
- 正常合并后的签名 master 为 [`df8a980beb682a981d72e42760705f1831cacf9b`](https://github.com/ForceMind/V-UI/commit/df8a980beb682a981d72e42760705f1831cacf9b)，候选与合并 tree 同为 `f68098e398cb7ac29e2e1e809b8f741bb3c30533`
- 最终 master 八组工作流、11 个最新 job、全部步骤均成功；七组 attempt 1，deployment gate 为 attempt 2。不能把这个结果写成八组全部首次通过
- 真实链路 75 项通过；同一工作流中独立执行的 HY2 托管证书 8 项与 TUIC 托管证书 8 项分别通过，不混入链路 75 项计数。Chromium 完整编辑/导出/恢复流程另由 Test V-UI 验收
- PR #23 普通响应允许列表已通过正常 merge-forward 继承；原始提交与独立安全验收见[普通响应安全收口](INBOUND_RESPONSE_CLOSURE_20261006.md)

## 最终 master 工作流

以下链接只证明准确 `df8a980b` 的 TUIC 收口；不能作为后续 REALITY/Vision 的通过记录。

| 门槛 | 最终 master 记录 | attempt |
| --- | --- | --- |
| 文档/发布契约 | [Documents and release contracts](https://github.com/ForceMind/V-UI/actions/runs/37467457290) | 1 |
| 真实链路 75 项、独立 HY2 8 项/TUIC 8 项 | [Real loopback proxy and DNS chain](https://github.com/ForceMind/V-UI/actions/runs/37467456757) | 1 |
| 普通/真实核心/Chromium 完整流程 | [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/37467457023) | 1 |
| 临时包部署门槛 | [Selected release deployment gates](https://github.com/ForceMind/V-UI/actions/runs/37467456812) | 2 |
| 40 项 ToClash | [ToClash reference and export verification](https://github.com/ForceMind/V-UI/actions/runs/37467456815) | 1 |
| 一键安装/升级 | [One-command installation acceptance](https://github.com/ForceMind/V-UI/actions/runs/37467456730) | 1 |
| 四目标 Linux | [Portable Linux runtime matrix](https://github.com/ForceMind/V-UI/actions/runs/37467456639) | 1 |
| 真实 HTTP-01/续期 | [ACME certificate acceptance](https://github.com/ForceMind/V-UI/actions/runs/37467456712) | 1 |

Deployment 的 [attempt 1](https://github.com/ForceMind/V-UI/actions/runs/37467456812/attempts/1) 在上游 GitHub 源码身份查询遭遇 HTTP 403 后失败；未变更代码、官方核心 pin 或凭据，同一准确提交的 [attempt 2](https://github.com/ForceMind/V-UI/actions/runs/37467456812/attempts/2) 成功。首次失败继续保留，不能改写为通过或删去。该工作流是隔离临时环境验收，不表示生产部署。

## 已验收边界

固定官方 sing-box 1.14.2 / Mihomo 1.19.32、单 UUID/密码对、显式验证 SNI、服务端与客户端 ALPN 恰为 h3、原生 QUIC 默认拥塞与关闭零 RTT。未换 pin、重编译核心或增加反代；不支持 v4/token、多用户、调优/指纹或其他协议扩展。

三格式为固定客户端支持的非官方 TUIC URI 约定、完整 Mihomo YAML 与 sing-box JSON；不称官方通用 URI 标准。真实 URI provider/converter、公开订阅的双客户端链路、分别错误 UUID/密码/CA/SNI、真实认证/x509 原因、固定应用目标零送达和无 DIRECT 均保留。超时、单独零送达、普通 TLS 行或本地 listener 成功不够证明上游拒绝。

只验 HTTP/TCP 应用负载；sing-box `network: tcp`。Mihomo TUIC adapter 硬编码 UDP 能力，省略无效的 `udp: false`，不宣称它关闭 UDP。QUIC 要求节点 UDP 可达，但应用 UDP 未验收，URI 也不承诺限制导入客户端的应用 UDP。

UUID/密码输入分别隐藏且空输入保留，普通响应沿用 PR #23 allowlist；特权编辑器的手工证书路径能力与明确授权客户端导出分开。高级导入草稿保留或拒绝，不能由无关编辑清除不支持字段而变成公开可用配置。Chromium 已覆盖创建、取消、编辑回填、刷新、再打开、独立凭据稳定、三格式导出和损坏后的停机恢复。

证书生命周期覆盖绑定/换绑/编辑、续期后新 QUIC 会话、失败保留旧材料及已应用 revision、停止核心 `CORE_STOPPED_PENDING_APPLY`，不因续期自行启动。协议区分 UDP 安装预检、40 项 ToClash、四目标 Linux、ACME 与安装/升级门槛全部保留。

## 历史与后续

裸前置 `21cb0bc7` 八组/11 jobs/全部步骤 attempt 1、链路 69 项；merge-forward `7c380d7c` 八组/11 jobs/全部步骤 attempt 1；二者与最终集成的 75 项分别记录。工作区回退丢失的旧本地集成证据没有复用。重新尝试时的 sing-box netlink/socket EPERM 与 Chromium socket EPERM 仍是本地环境阻断，后来的 CI 成功不会将它们改为本地通过，详见[原契约](TUIC_046.md)。

更早 HY2 缺真实日志、ACME DNS 端口碰撞、首次主线继承 TLS 日志失败以及 portable 首次限流保留于[HY2 收口](HYSTERIA2_CLOSURE_045.md)。未删除历史、未强推或改写已有提交。

下一阶段 [v0.4.7 REALITY/Vision](REALITY_VISION_047.md)从本页准确 master 独立起步，先通过固定核心裸前置，再推进严格公开导出、编辑和浏览器集成。仍无 tag、Draft Release、公开 Release、生产部署、附件晋升、真实 CA/账户或实际防火墙变更。

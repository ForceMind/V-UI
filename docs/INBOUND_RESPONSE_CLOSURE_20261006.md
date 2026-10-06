# 普通节点响应安全修复主线收口

[PR #23](https://github.com/ForceMind/V-UI/pull/23) 已正常合并至签名 master `8e0d07463e59b52856f55fa33346a760a38b4705`，tree `fda9f9d8a26dd21bdf0e441ab5d9bbc7ec0dbdd8`，来源候选 `ae415ea5ce4879a2e8a9cf52e8a933136087e1cd`。独立源码审查、八组准确候选与八组准确主线验收完成；最终主线 11 个 job 和全部步骤均为 attempt 1 成功。

## 有意收窄的响应契约

统一 `/api/inbounds` 及旧 Xray/sing-box 别名的普通列表、创建、更新响应使用允许列表摘要，不回传原始 `settings` / `stream_settings`。UUID、认证/obfs 密码、REALITY 私钥、导入未知秘密和服务端证书/密钥路径均不进入普通响应。客户端依赖旧原始响应时须适配，见[API](API.md)。

鉴权后的特权 `/editor` 仍可返回手工证书路径字符串，但不返回凭据或密钥内容。明确授权的客户端导出仍保留连接所需凭据，永不导出服务端材料；数据库、核心应用与停机备份未做脱敏写回。证书卡使用允许列表中的适用性标志，不再消费原始 TLS 配置。

验证包括 371 项普通 discovery（其中 80 项明确环境 skip，不能计作实际运行通过）、激活真实链路 63 项、独立托管 HY2 8 项及 Chromium 普通响应/证书卡/编辑/导出/损坏后停机恢复。复现和回归只使用合成凭据与临时数据库；没有证据声称发生真实泄露事件，没有生产凭据轮换或部署。

## 最终 master 工作流

| 门槛 | 准确 master 记录 |
| --- | --- |
| Documents and release contracts | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462806961) |
| One-command installation acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462807075) |
| ACME certificate acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462807029) |
| Portable Linux runtime matrix | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462807005) |
| ToClash reference and export verification | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462807048) |
| Selected release deployment gates | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462806992) |
| Real loopback proxy and DNS chain | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462806971) |
| Test V-UI | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37462807123) |

## TUIC 继承边界

[TUIC 候选](TUIC_046.md)以正常 merge-forward `7c380d7c9d7948f4e1992cbb5404b805904a6b57` 继承此修复，两个父提交为 TUIC 裸前置 `21cb0bc721dd4f5e4d172d7602c8135f2f04db91` 与安全 master `8e0d07463e59b52856f55fa33346a760a38b4705`，tree `5cce37c23cfc4282f169cbaec4693df4c1e86a8b`。该 merge-forward 后续已单独核对八组/11 jobs/全部步骤 attempt 1 成功，链接见[TUIC 新鲜证据](TUIC_046.md)；它不证明之后尚未提交的 TUIC 集成通过。

TUIC UUID/密码同样必须留在普通响应允许列表之外，并保持空输入独立保留语义。当前 TUIC 集成的独立审查、准确候选八组、授权正常合并和准确主线八组仍待完成；安全修复绿灯不能代替这些门槛。无 tag、Release、附件晋升、真实 CA、主机防火墙变更或实际部署。

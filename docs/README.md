# V-UI 文档

当前产品文档按 **v0.4.7** 组织，[REALITY/Vision](REALITY_VISION_CLOSURE_047.md)及 [TUIC v0.4.6](TUIC_CLOSURE_046.md)与此前协议/安全修复均有准确主线验收。REALITY 首次主线 VMess CA 原因日志缺失和 unchanged-code attempt 2 成功分别保留，重跑不证明轮询不稳定性已永久修复。[PR #25 XHTTP 刻画](XHTTP_CHARACTERIZATION_CLOSURE_048.md)已在准确候选/主线各八组、11 jobs、全部步骤 attempt 1 通过；不提高产品版本、不开放 XHTTP/HTTPUpgrade 公共导出。产品 0.4.7 范围冻结并进入[发布准备](RELEASING.md)，最终文档提交与同提交附件的验收以对应 PR/发布准备证据为准，本文不预先声明通过。截至 2026-10-06 本次检查，未新建 tag/Draft Release/公开 Release 或晋升附件。源码、配置检查、候选/主线、附件晋升、发布和部署分别记录。

| 阅读目标 | 文档 |
| --- | --- |
| 1 vCPU / 512 MiB 安装运行目标与实测边界 | [LOW_RESOURCE_TARGET.md](LOW_RESOURCE_TARGET.md) |
| 405 同提交四平台及完整资源基线、未达预算 | [LOW_RESOURCE_405_BASELINE.md](LOW_RESOURCE_405_BASELINE.md) |
| 新服务器安装/首次证书/创建管理员 | [INSTALLATION.md](INSTALLATION.md) |
| 申请/测试/自动续期/面板或节点绑定 | [CERTIFICATES.md](CERTIFICATES.md) |
| 节点、规则、DNS、专用订阅 | [CONFIGURATION.md](CONFIGURATION.md) |
| 更新、停机备份、恢复、回滚、卸载 | [OPERATIONS.md](OPERATIONS.md) |
| 访问失败/签发失败/核心未生效 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 管理API、令牌和错误状态 | [API.md](API.md) |
| 已验证与未验证的协议/部署范围 | [COMPATIBILITY.md](COMPATIBILITY.md) |
| PR #25 准确候选/主线验收与首次失败 | [XHTTP_CHARACTERIZATION_CLOSURE_048.md](XHTTP_CHARACTERIZATION_CLOSURE_048.md) |
| XHTTP 刻画、sing-box 不支持与 HTTPUpgrade URI 缺口 | [XHTTP_CHARACTERIZATION_048.md](XHTTP_CHARACTERIZATION_048.md) |
| REALITY/Vision 已验收、主线重跑与失败历史 | [REALITY_VISION_CLOSURE_047.md](REALITY_VISION_CLOSURE_047.md) |
| REALITY/Vision 参考流量、认证边界与阶段历史 | [REALITY_VISION_047.md](REALITY_VISION_047.md) |
| TUIC v5/TLS 已验收与失败历史 | [TUIC_CLOSURE_046.md](TUIC_CLOSURE_046.md) |
| Hysteria2/TLS 已验收基线与失败历史 | [HYSTERIA2_CLOSURE_045.md](HYSTERIA2_CLOSURE_045.md) |
| 普通响应允许列表与 PR #23 安全收口 | [INBOUND_RESPONSE_CLOSURE_20261006.md](INBOUND_RESPONSE_CLOSURE_20261006.md) |
| gRPC Lite 已验收基线与错误传播限制 | [VLESS_GRPC_CLOSURE_044.md](VLESS_GRPC_CLOSURE_044.md) |
| 威胁边界/漏洞报告/秘密数据处理 | [安全说明](../SECURITY.md) |
| 本地开发与各类测试入口 | [贡献指南](../CONTRIBUTING.md) |
| 准备官方Release而非手工上传未知包 | [RELEASING.md](RELEASING.md) |

## 历史实现与验收资料

以下文件保留阶段性事实，不作为当前安装命令的首选来源：
[alpha.2认证](AUTH_ALPHA2.md)、[alpha.5导出](EXPORT_ALPHA5.md)、[alpha.6工作区](WORKSPACE_ALPHA6.md)、[rc.1真实链路](LOOPBACK_RC1.md)、[rc.2手动部署](DEPLOYMENT_RC2.md)、[v0.4.2主线收口](MAINLINE_CLOSURE_20261005.md)、[v0.4.3 WS主线收口](VLESS_WS_CLOSURE_043.md)、[v0.4.4 gRPC主线收口](VLESS_GRPC_CLOSURE_044.md)、[gRPC 原候选与失败历史](VLESS_GRPC_044.md)、[HY2 原候选与失败历史](HYSTERIA2_045.md)、[原始计划复核](ROADMAP_REVIEW_20261001.md)、[迭代证据](ITERATIONS.md)。其“未实现/当前版本”指当时状态，不覆盖当前功能文档。

文档使用example域名和假凭据。不要把真实令牌、私钥、完整数据库或未加密备份复制进公开Issue。

# V-UI 文档

当前使用文档按 **v0.4.6** TUIC v5/TLS 候选组织，最终独立审查、准确候选八组、授权正常合并和准确主线八组 CI 仍待完成。[v0.4.2 主线](MAINLINE_CLOSURE_20261005.md)、[WS](VLESS_WS_CLOSURE_043.md)、[gRPC](VLESS_GRPC_CLOSURE_044.md)、[HY2](HYSTERIA2_CLOSURE_045.md)及 [PR #23 普通响应安全修复](INBOUND_RESPONSE_CLOSURE_20261006.md)是继承基线。当前 [TUIC 契约](TUIC_046.md)将裸前置 69 项真实链路和新集成门槛分开；不复用丢失的旧本地证据。历史失败、版本号、候选/主线验收、附件晋升、发布与部署分别记录，不互相推断。

| 阅读目标 | 文档 |
| --- | --- |
| 新服务器安装/首次证书/创建管理员 | [INSTALLATION.md](INSTALLATION.md) |
| 申请/测试/自动续期/面板或节点绑定 | [CERTIFICATES.md](CERTIFICATES.md) |
| 节点、规则、DNS、专用订阅 | [CONFIGURATION.md](CONFIGURATION.md) |
| 更新、停机备份、恢复、回滚、卸载 | [OPERATIONS.md](OPERATIONS.md) |
| 访问失败/签发失败/核心未生效 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 管理API、令牌和错误状态 | [API.md](API.md) |
| 已验证与未验证的协议/部署范围 | [COMPATIBILITY.md](COMPATIBILITY.md) |
| TUIC v5/TLS 当前候选、URI 约定、QUIC/UDP 边界与证据 | [TUIC_046.md](TUIC_046.md) |
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

# V-UI 文档

当前稳定版为 **v0.4.26**。新用户从[项目首页](../README.md)和[安装指南](INSTALLATION.md)开始；下载以[官方 Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.26)为准。

本文档对应 v0.4.26。正式应用源为 `71f0e1689cd356f22e12553f74a9ec4fd8bba388`，与可变短入口的源码分开记录；公开资产读回见[发布记录](RELEASING.md#当前稳定版)。

本索引按使用目的组织。专题契约和历史验收记录保留其原始日期及验证范围，旧文档中的“当前版本”“候选”“未发布”不能覆盖这里的稳定版信息。

## 用户指南

| 目标 | 文档 |
| --- | --- |
| 安装、首次登录、端口与已有证书 | [安装指南](INSTALLATION.md) |
| 创建直连或中转节点、服务器程序代理、分享订阅 | [配置指南](CONFIGURATION.md) |
| 申请、续期及绑定证书 | [证书指南](CERTIFICATES.md)；[Cloudflare DNS-01指南](DNS01_CLOUDFLARE.md) |
| 查看支持的协议、客户端与部署限制 | [兼容矩阵](COMPATIBILITY.md) · [标准客户端说明](STANDARD_CLIENT_COMPATIBILITY.md) |
| 排查安装、连接或导出失败 | [故障排查](TROUBLESHOOTING.md) · [获取帮助](../SUPPORT.md) |

## 维护指南

| 目标 | 文档 |
| --- | --- |
| 终端菜单、在线升级、备份与恢复 | [运维指南](OPERATIONS.md) |
| 全局管理入口与受管操作的详细边界 | [受管运维](MANAGED_OPERATIONS.md) |
| 远程维护的权限与撤销规则 | [维护权限契约](REMOTE_MAINTENANCE_CONTRACT.md) |
| 资源预算及尚未获得的整机资格 | [低资源目标](LOW_RESOURCE_TARGET.md) |
| 报告漏洞 | [安全政策](../SECURITY.md) |

## 开发与贡献

| 目标 | 文档 |
| --- | --- |
| 提交问题、变更和验证要求 | [贡献指南](../CONTRIBUTING.md) · [行为准则](../CODE_OF_CONDUCT.md) |
| 计划与版本变化 | [路线图](../ROADMAP.md) · [变更记录](../CHANGELOG.md) |
| 管理 API | [API 参考](API.md) |
| 发布与制品核验 | [发布指南](RELEASING.md) · [当前发布说明](RELEASE_NOTES.md) |
| 第三方来源和许可证 | [第三方说明](../third_party/NOTICE.md) · [项目许可证](../LICENSE) |

## 协议与数据契约

[Cloudflare DNS-01与通配符契约](DNS01_CLOUDFLARE_CONTRACT.md)包含本版已审实现、准确 main 的九组/21 jobs、同源材料和公开记录。原始失败保留，不能把本版能力写成旧22已有功能。

这些专题保留精确字段限制和原始验证范围，适合高级配置或实现变更时查阅。

| 主题 | 文档 |
| --- | --- |
| 每入口跟随全局、直连和中转 | [使用说明](PER_INBOUND_ROUTING.md) · [三态契约](PER_INBOUND_ROUTING_CONTRACT.md) |
| 服务器程序代理、全局中转与恢复 | [双开关契约](DUAL_PROXY_RELAY_CONTRACT.md) · [离线恢复](DUAL_PROXY_RELAY_OFFLINE_RECOVERY.md) |
| 专用 TCP 上游配置 | [使用说明](TCP_UPSTREAM_EXPORT.md) · [导出契约](TCP_UPSTREAM_EXPORT_CONTRACT.md) |
| UDP、DNS 和导入保真 | [能力门槛](UDP_DNS_CAPABILITY_GATES.md) |
| VLESS 的 WS / gRPC 限制 | [WS 已验范围](VLESS_WS_CLOSURE_043.md) · [gRPC 契约](VLESS_GRPC_044.md) |
| Hysteria2 / TUIC / REALITY | [HY2 契约](HYSTERIA2_045.md) · [TUIC 契约](TUIC_046.md) · [REALITY 契约](REALITY_VISION_047.md) |
| XHTTP 分享与传输刻画 | [XHTTP 分享](XHTTP_PUBLIC_PROFILE.md) · [XHTTP / HTTPUpgrade 历史刻画](XHTTP_CHARACTERIZATION_048.md) |

## 历史资料

[历史索引](history/README.md)保留旧入口快照、阶段计划、准确提交和原始成功或失败证据。历史记录用于追溯，不作为当前安装步骤或当前版本已验证的替代依据。

公开版本的构建、核心与安装验收在 GitHub Actions 中完成；v0.4.26 的准确提交验收与公开资产匿名回读已通过。**浏览器和真实 Cloudflare 账号签发未验证**；用户主机、真实客户端与长期运行需各自验证。
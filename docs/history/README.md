# 历史文档与验收证据

当前使用说明从[文档首页](../README.md)进入，当前版本与剩余计划以[路线图](../../ROADMAP.md)为准。

早期入口整理以已验收主线 `39181e0cdb9643422a19049df942b208f876faf4` 为归档点。后续状态校准按下节分别固定快照，保留整理前的完整原文、提交与失败记录。旧文中的“当前”“候选”“未发布”“未授权”和空复选框均反映当时阶段，不是最新产品状态。

## 2026-11-19 状态校准快照

本次以主线 `b58a76dda1d2b26c831988a53db88e25646f5b54` 固定文档原文，将过时的 v0.4.7 兼容结论、v0.4.23 交接与 P3/P4 候选叙述和当前 v0.4.26 分开。只校准事实、索引和实施方案，权限、浏览器政策、失败记录及发布约束保持。

| 原文 | 固定快照 |
| --- | --- |
| 兼容矩阵与历史协议范围 | [COMPATIBILITY.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/docs/COMPATIBILITY.md) |
| 开发约束及阶段授权记录 | [AGENTS.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/AGENTS.md) |
| 开发交接原文 | [DEVELOPMENT_HANDOFF.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/docs/DEVELOPMENT_HANDOFF.md) |
| 路线与阶段证据 | [ROADMAP.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/ROADMAP.md) |
| 运维与核心候选说明 | [OPERATIONS.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/docs/OPERATIONS.md) · [MANAGED_OPERATIONS.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/docs/MANAGED_OPERATIONS.md) |
| 静态 YAML 候选演进 | [YAML_APPLICATION_CONTRACT.md](https://github.com/ForceMind/V-UI/blob/b58a76dda1d2b26c831988a53db88e25646f5b54/docs/YAML_APPLICATION_CONTRACT.md) |

## 整理前的完整快照

| 原文 | 固定快照 |
| --- | --- |
| 项目首页 | [README.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/README.md) |
| 开发路线 | [ROADMAP.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/ROADMAP.md) |
| 贡献和历代验证说明 | [CONTRIBUTING.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/CONTRIBUTING.md) |
| 编码约束与历次交接 | [AGENTS.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/AGENTS.md) |
| 文档索引 | [docs/README.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/README.md) |
| 开发交接流水 | [DEVELOPMENT_HANDOFF.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/DEVELOPMENT_HANDOFF.md) |
| 安装手册与阶段说明 | [INSTALLATION.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/INSTALLATION.md) |
| 配置手册 | [CONFIGURATION.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/CONFIGURATION.md) |
| 运维手册 | [OPERATIONS.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/OPERATIONS.md) |
| 发布流程与阶段记录 | [RELEASING.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/RELEASING.md) |
| 版本说明 | [RELEASE_NOTES.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/RELEASE_NOTES.md) |
| 安全政策 | [SECURITY.md](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/SECURITY.md) |

## 正式功能与批次证据

- [v0.4.22 维护分享修复、发布和入口验收](../MAINTENANCE_SHARE_0422.md)
- [v0.4.21 简单操作改版](../SIMPLE_OPERATIONS_PROGRESS.md)
- [v0.4.20 systemd / sing-box 修复](../SING_BOX_SYSTEMD_0420.md)
- [v0.4.19 长期维护授权](../LONG_MAINTENANCE_0419.md)
- [v0.4.18 简单节点向导](../SIMPLE_NODE_WIZARD_PROGRESS.md)
- [v0.4.17 远程维护与终端升级](../REMOTE_MAINTENANCE_PROGRESS.md)
- [v0.4.16 TLS 证书自动绑定](../TLS_CERTIFICATE_BINDING_FIX.md)
- [v0.4.15 界面整理](../UI_REDESIGN_PROGRESS.md)
- [v0.4.14 启动等待](../STARTUP_READINESS_0414.md)
- [v0.4.13 自动账号与安装检查](../INSTALLER_AUTOMATION_0413.md)
- [v0.4.12 系统 CA 兼容](../CERTIFICATE_SYSTEM_CA_0412.md)
- [v0.4.11 已有安装交互](../INSTALLER_EXISTING_0411.md)
- [v0.4.10 安装交互](../INSTALLER_INTERACTION_0410.md)
- [v0.4.9 一行安装](../ONE_LINE_INSTALL_049.md)
- [每入口 TCP 路由收口](../PER_INBOUND_ROUTING_CLOSURE_20261018.md)
- [既有低资源软件阶段](../LOW_RESOURCE_CLOSURE_20261011.md)

[变更日志](../../CHANGELOG.md)保留历代产品变化；各协议的契约和收口记录仍留在原路径。历史通过仅属于记录中的准确提交、目标和负载，不授予后续提交、新协议或其他环境资格。

## 研究与产品资格边界

- [UDP / 标准客户端兼容边界](../STANDARD_CLIENT_COMPATIBILITY.md)
- [XHTTP / HTTPUpgrade 历史刻画](../XHTTP_CHARACTERIZATION_CLOSURE_048.md)；v0.4.26 的[XHTTP 最小分享](../XHTTP_PUBLIC_PROFILE.md)另行取得资格，其他模式和 HTTPUpgrade 不因此开放。
- [Tailscale 私有管理设计](../TAILSCALE_PRIVATE_MANAGEMENT_DESIGN.md)
- [真实低资源整机与长期稳定性合同](../LOW_RESOURCE_TARGET.md)

研究记录中的局部通过、失败与环境限制保持原判。后续是否执行、执行顺序及依赖统一在当前路线图跟踪，不能由这些旧记录自动开放功能或操作生产实例。

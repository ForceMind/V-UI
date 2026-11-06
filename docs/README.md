## **v0.4.19** 长期维护授权（发布准备）

网页可选180天，终端可输入180天或180d；默认30分钟、截止不续期和权限/撤销保护保持。已批准独审、正常合并、公开发布和短入口更新，准确结果见[范围与验证](LONG_MAINTENANCE_0419.md)。公开前短入口仍安装18；构建和安装仅Actions，浏览器未验证。

## **v0.4.18** 简单节点向导

[已确认线稿](node-simple-flow.html) · [实施契约](SIMPLE_NODE_WIZARD_CONTRACT.md) · [发布与验收记录](SIMPLE_NODE_WIZARD_PROGRESS.md)。按用途创建并直接交付，旧节点和高级编辑器保持。已从准确main27862759d0d73a36fa1a7bdccf165d35a3b851b5 [公开发布](https://github.com/ForceMind/V-UI/releases/tag/v0.4.18)，19资产、tag、latest和真实安装器/完整清单摘要已匿名核验，旧17资产不变。应用与短入口分别验自己的资格；真实构建、核心和安装只Actions，浏览器未验证。

## **v0.4.17** 远程维护与终端在线升级（历史）

[线稿](remote-maintenance-wireframe.html) · [权限契约](REMOTE_MAINTENANCE_CONTRACT.md) · [实现与验收记录](REMOTE_MAINTENANCE_PROGRESS.md)。已从准确main95c201c [公开发布](https://github.com/ForceMind/V-UI/releases/tag/v0.4.17)，19资产与真实摘要核验；候选/dev/main各自检查、四目标和14/15/16保数据升级通过。公开后短入口及其准确资格另记，旧16资产保持。构建和真实安装只Actions，浏览器未验证。

## **v0.4.16** TLS已有证书自动绑定

新增TLS唯一适用证书默认选、多张手选，支持Xray VLESS RAW/TCP TLS托管绑定和续期，保留既有配置数据。已从准确main `18fe631bcf69b68425b454db1795a101e0378842` [公开发布](https://github.com/ForceMind/V-UI/releases/tag/v0.4.16)，19项资产及真实摘要已核验；[统一记录](TLS_CERTIFICATE_BINDING_FIX.md)保留准确资格和旧失败。构建与真实安装仅Actions，浏览器未验证。

## v0.4.15 统一界面与安装流程

统一桌面左导航、手机抽屉、页面URL与返回行为；敏感弹窗离开即清理，保留原配置和数据。普通安装不重复询问防火墙，主动诊断和安全放行放在终端菜单。已从准确main `65b1e75d90de733408101ec280a92be935731baa` [公开发布](https://github.com/ForceMind/V-UI/releases/tag/v0.4.15)，19项资产及真实摘要已核验；[统一账本](UI_REDESIGN_PROGRESS.md)记录准确资格及旧版升级验收。构建、安装由Actions执行，浏览器未验证。

## v0.4.14 启动等待与诊断（历史）

当前稳定版已公开：面板从启动请求起最多等待120秒，验证真实TLS/域名及HTTP401，不以systemd active代替就绪；明确终态及时失败，回滚前采集有界脱敏诊断，保留配置/账号/证书/数据。[准确范围与证据](STARTUP_READINESS_0414.md)。构建和真实慢启动/故障恢复验收仅Actions，浏览器未验证。

## v0.4.13 自动账号与安装检查（历史）

历史版本已公开：新安装自动生成用户名和强密码，只在当前终端交付，之后可在面板修改；已有账号和数据保留。取消重复防火墙确认，检测后准确提示缺口；首次 CA 明确同意保留。[范围与准确证据](INSTALLER_AUTOMATION_0413.md)。构建及真实安装由 GitHub Actions 验收，浏览器未验证。

## v0.4.12 系统CA兼容修复

历史稳定版 **v0.4.12** 已公开，正式源为 `dacaa7c26bbadf3ba6e7a4e2cc36f7c85a38d7cf`；修复默认CA枚举为空，严格保留证书链、域名、时间与密钥校验。[本版范围和准确证据](CERTIFICATE_SYSTEM_CA_0412.md)。短入口使用公开套件真实摘要，后续引导入口与冻结应用源码身份分别记录。

## v0.4.11 已有安装交互修复

历史稳定版 **v0.4.11** 已公开：已有安装自动读原配置，选择升级、未完成续装或修改，保留用户与节点数据。[范围与验收记录](INSTALLER_EXISTING_0411.md)。短入口与已发布应用套件分别记录源码身份。

## v0.4.10 安装交互修复

历史稳定版 **v0.4.10** 及其资产保留，准确资格见 [Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.10) 和[执行账本](INSTALLER_INTERACTION_0410.md)。下方带日期内容保留各阶段历史。

## 历史入口（2026-10-28）

稳定版为 **v0.4.9**。从项目 [README](../README.md) 的短命令开始安装，参数、域名和已有证书见[安装指南](INSTALLATION.md)，日常管理输入 `v-ui`。main/dev 分支、旧工作归档及本批验收见[维护账本](REPOSITORY_MAINTENANCE_20261028.md)。下面按日期保留历史状态。

> 2026-10-22：[V-TS-03 私有管理入口设计](TAILSCALE_PRIVATE_MANAGEMENT_DESIGN.md)仅设计与源码阻断，未实施或授予部署资格；V-TS-02 [只读状态软件](TAILSCALE_STATUS_CONTRACT.md)已由 PR54/55 收口，真实 Tailscale 共存/恢复仍未测。

## 2026-10-27 v0.4.9 一键安装批次

本批目标版本为 **v0.4.9**。用户已批准在线一行安装、交互配置、全局 `v-ui`、独审、正常合并和公开发布；当前执行状态与准确证据集中在[一键安装计划](ONE_LINE_INSTALL_049.md)。发布时的真实一行命令由受验资产生成并写入 [v0.4.9 Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.9)。下方旧日期状态保留历史。


# V-UI 文档

先确认[冻结版本与自行部署验收清单](FROZEN_VERSION_TEST_HANDOFF.md)：旧正式版、已验主线与本地可选功能不是同一套安装材料。

## 离线 YAML 预览（开发中，未发布）

[只读预览库与 CLI](OFFLINE_MIHOMO_PREVIEW.md)支持默认 JSON 和 `--format text` 中文摘要，包括结构差异、诊断解释及列表位置。CLI 仅本地检查；[只读面板入口](MIHOMO_PANEL_PREVIEW_CONTRACT.md)由用户显式提交给面板服务器。两者都不是任意 YAML 导入 / 合并器，不应用配置或验证真实核心兼容性。

## 当前开发入口（2026-10-18）

接手先读[开发状态与下一步](DEVELOPMENT_HANDOFF.md)，再读[每入口直连/中转 TCP 契约](PER_INBOUND_ROUTING_CONTRACT.md)和[标准客户端兼容边界](STANDARD_CLIENT_COMPATIBILITY.md)。用户已确认现成客户端导入不同标准 VLESS 节点，服务器分别直出/中转；本轮从准确产品主线 a98 独立实现，不依赖自研 App或实验核心发布。D1已在a98主线，三态实现已通过 [PR49](https://github.com/ForceMind/V-UI/pull/49) 正常合并，准确0962主线及四目标已独立验收，见[收口记录](PER_INBOUND_ROUTING_CLOSURE_20261018.md)；产品、实验、发布、实机资格分开记录。以下按日期保留历史入口。

## 当前入口（2026-10-16）

正式版为 [v0.4.8](https://github.com/ForceMind/V-UI/releases/tag/v0.4.8)，发布于 2026-10-15，来源 `a9f9b5d9` 的安装器修复；正式版用户使用[固定 v0.4.8 文档](https://github.com/ForceMind/V-UI/blob/v0.4.8/docs/README.md)。当前源码版本仍标 **v0.4.8**，其后新增功能尚未正式发布。

双开关 M0–M4 已在 `9a97ad13` 完成准确主线和同提交四目标验收，见[最终验收 PR #44](https://github.com/ForceMind/V-UI/pull/44)。面板入口“代理与中转” `/proxy`，双开关默认关；[使用说明](DUAL_PROXY_RELAY_M3.md)、[一期边界](DUAL_PROXY_RELAY_CONTRACT.md)、[离线恢复](DUAL_PROXY_RELAY_OFFLINE_RECOVERY.md)及[证据账本](DUAL_PROXY_RELAY_PROGRESS.md)分别说明行为和已测范围。用户真实线路/外部设备仍未测试。

[UDP / DNS 能力门槛与导入保真](UDP_DNS_CAPABILITY_GATES.md)已在 `13c884a` 完成准确主线和同提交四目标验收，见 [PR #46](https://github.com/ForceMind/V-UI/pull/46)；不代表通用 UDP 中转。下一有界功能 [D1：显式 TCP-only 上游导出](TCP_UPSTREAM_EXPORT_CONTRACT.md)正在实现与独立验收，操作入口见[专用导出使用说明](TCP_UPSTREAM_EXPORT.md)。普通导出/导入拒绝边界保持，不预称新功能已发布。

## 以下为 2026-10-15 收口前入口快照（历史保留）

双开关候选新增[有界离线恢复](DUAL_PROXY_RELAY_OFFLINE_RECOVERY.md)：准确计划、完整数据交换和写入者停机证明；不代表 M4 或四目标验收完成。

双开关功能的 [M1 安全导入与草稿实现](DUAL_PROXY_RELAY_M1.md)单独记录实现、恢复边界与包内解析门槛；草稿接口不代表代理运行已生效。

2026-10-15 新增：[双开关代理与中转一期契约](DUAL_PROXY_RELAY_CONTRACT.md)及[进度与证据账本](DUAL_PROXY_RELAY_PROGRESS.md)。已批准 M0–M4 独立阶段开发，当前首先收口 M0；这些是待实现契约和实际证据入口，不表示中转已可用或已发布。下方安装器/发布文字保留原阶段状态，新范围及资格以该契约和准确证据为准。

当前源码/开发文档为 **v0.4.8**（UNRELEASED），仅包含[安装器退出状态与清理修复](INSTALLER_EXIT_STATUS_048.md)。当前正式发布为 [v0.4.7](https://github.com/ForceMind/V-UI/releases/tag/v0.4.7)，发布于 2026-10-13，tag 指向 `c5d50ee14b08da57bdc13c150b4ed501e9484023`；正式版用户请使用[固定 v0.4.7 文档](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/README.md)并先阅读[旧安装器已知问题](INSTALLER_EXIT_STATUS_048.md)。当前分支的 0.4.8 安装示例仅供发布后准备，现在不要执行。新用户从[产品 README](../README.md)开始，再按下表选择操作指南；不存在或不完整的版本不能安装，不追随 latest。

网页后台、服务用户菜单、全局受管控制与低资源软件已进入主线；主线93c8的文档收口及独立准确验收见[PR #36](https://github.com/ForceMind/V-UI/pull/36)。后续提交必须核自己的CI与包。默认安装不添加全局v-ui，真实f509 root/systemd资格、普通四平台包消费、正式发布和用户部署分别归属。

[当前发布说明](RELEASE_NOTES.md)聚焦使用者，旧[README快照](README_HISTORY_20261012.md)和[发布准备原始记录](RELEASE_PREPARATION_HISTORY_20261012.md)保留历史状态与失败，不作为当前可下载或已部署的证明。

## 协议文档导读（2026-10-06 历史）

以下保留当时的状态与失败背景，不替代上方当前集成入口。

当前产品文档按 **v0.4.7** 组织，[REALITY/Vision](REALITY_VISION_CLOSURE_047.md)及 [TUIC v0.4.6](TUIC_CLOSURE_046.md)与此前协议/安全修复均有准确主线验收。REALITY 首次主线 VMess CA 原因日志缺失和 unchanged-code attempt 2 成功分别保留，重跑不证明轮询不稳定性已永久修复。[PR #25 XHTTP 刻画](XHTTP_CHARACTERIZATION_CLOSURE_048.md)已在准确候选/主线各八组、11 jobs、全部步骤 attempt 1 通过；不提高产品版本、不开放 XHTTP/HTTPUpgrade 公共导出。产品 0.4.7 范围冻结并进入[发布准备](RELEASING.md)，最终文档提交与同提交附件的验收以对应 PR/发布准备证据为准，本文不预先声明通过。截至 2026-10-06 本次检查，未新建 tag/Draft Release/公开 Release 或晋升附件。源码、配置检查、候选/主线、附件晋升、发布和部署分别记录。

## 当前文档入口

| 阅读目标 | 文档 |
| --- | --- |
| 可选 Tailscale 管理通道、HTTPS 兼容与安装前共存门槛（仅研究，未实测） | [方案](TAILSCALE_MANAGEMENT_OVERLAY.md) / [最低共存与回滚验收计划](TAILSCALE_MANAGEMENT_ACCEPTANCE.md) / [私有管理入口设计](TAILSCALE_PRIVATE_MANAGEMENT_DESIGN.md) |
| 每入口三态、首次直连/中转与保存/应用状态（主线已验、尚未发布） | [PER_INBOUND_ROUTING.md](PER_INBOUND_ROUTING.md) |
| 每入口 TCP 准确候选/主线收口与保留失败 | [PER_INBOUND_ROUTING_CLOSURE_20261018.md](PER_INBOUND_ROUTING_CLOSURE_20261018.md) |
| 专用 TCP 上游 URI/YAML 导出、另一实例导入与凭据处理 | [TCP_UPSTREAM_EXPORT.md](TCP_UPSTREAM_EXPORT.md) |
| 双开关四状态、上游导入、失败关闭及分阶段门槛 | [DUAL_PROXY_RELAY_CONTRACT.md](DUAL_PROXY_RELAY_CONTRACT.md) |
| 双开关 M0–M4 实际进度、准确提交与未验收边界 | [DUAL_PROXY_RELAY_PROGRESS.md](DUAL_PROXY_RELAY_PROGRESS.md) |
| 0.4.8 未发布安装器修复、shell 回归与剩余验收 | [INSTALLER_EXIT_STATUS_048.md](INSTALLER_EXIT_STATUS_048.md) |
| 产品代码集成基线、准确02d8验收及后续边界 | [MAINLINE_OPERATIONS_CLOSURE_20261012.md](MAINLINE_OPERATIONS_CLOSURE_20261012.md) |
| 2026-10-11 低资源软件收口、准确证据与剩余整机验收 | [LOW_RESOURCE_CLOSURE_20261011.md](LOW_RESOURCE_CLOSURE_20261011.md) |
| 1 vCPU / 512 MiB 安装运行目标与实测边界 | [LOW_RESOURCE_TARGET.md](LOW_RESOURCE_TARGET.md) |
| 405 同提交四平台及完整资源基线、未达预算 | [LOW_RESOURCE_405_BASELINE.md](LOW_RESOURCE_405_BASELINE.md) |
| 071 大规则优化与完整同提交验收基线 | [LOW_RESOURCE_071_BASELINE.md](LOW_RESOURCE_071_BASELINE.md) |
| 大规则导出索引、差分合同与本地诊断边界 | [LOW_RESOURCE_RULE_INDEX.md](LOW_RESOURCE_RULE_INDEX.md) |
| 停机备份私有缓存与无覆盖发布边界 | [LOW_RESOURCE_BACKUP_CACHE.md](LOW_RESOURCE_BACKUP_CACHE.md) |
| 离线安装同包短边界归因 | [LOW_RESOURCE_INSTALL_TRACE.md](LOW_RESOURCE_INSTALL_TRACE.md) |
| 新服务器安装/首次证书/创建管理员 | [INSTALLATION.md](INSTALLATION.md) |
| 申请/测试/自动续期/面板或节点绑定 | [CERTIFICATES.md](CERTIFICATES.md) |
| 节点、规则、DNS、专用订阅 | [CONFIGURATION.md](CONFIGURATION.md) |
| 终端菜单、只读状态/完整校验、候选准备；更新、备份与恢复 | [OPERATIONS.md](OPERATIONS.md) |
| 显式全局 `v-ui` 入口、受信 kit、systemd 维护事务与资格边界 | [MANAGED_OPERATIONS.md](MANAGED_OPERATIONS.md) |
| 后台、低资源与运维菜单的同版本完成矩阵 | [INTEGRATED_OPERATIONS_CANDIDATE.md](INTEGRATED_OPERATIONS_CANDIDATE.md) |
| 历史 PR #33/f509 普通 CI、准确 root 资格及首次失败（不替代新头验收） | [MANAGED_OPERATIONS_CLOSURE_20261012.md](MANAGED_OPERATIONS_CLOSURE_20261012.md) |
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
| Logo 图标、浅/深色字标与归属 | [BRAND.md](BRAND.md) |

## 历史实现与验收资料

以下文件保留阶段性事实，不作为当前安装命令的首选来源：
[alpha.2认证](AUTH_ALPHA2.md)、[alpha.5导出](EXPORT_ALPHA5.md)、[alpha.6工作区](WORKSPACE_ALPHA6.md)、[rc.1真实链路](LOOPBACK_RC1.md)、[rc.2手动部署](DEPLOYMENT_RC2.md)、[v0.4.2主线收口](MAINLINE_CLOSURE_20261005.md)、[v0.4.3 WS主线收口](VLESS_WS_CLOSURE_043.md)、[v0.4.4 gRPC主线收口](VLESS_GRPC_CLOSURE_044.md)、[gRPC 原候选与失败历史](VLESS_GRPC_044.md)、[HY2 原候选与失败历史](HYSTERIA2_045.md)、[原始计划复核](ROADMAP_REVIEW_20261001.md)、[迭代证据](ITERATIONS.md)。其“未实现/当前版本”指当时状态，不覆盖当前功能文档。

文档使用example域名和假凭据。不要把真实令牌、私钥、完整数据库或未加密备份复制进公开Issue。

- [新runtime提前写回](LOW_RESOURCE_RUNTIME_WRITEBACK.md)：仅新提取树的缓存提示、失败清理与准确候选对照。

- [新payload core写回](LOW_RESOURCE_CORE_WRITEBACK.md)：已证实的驻留边界与仅新文件的缓存优化实验。
- [有界双核心并存与恢复](LOW_RESOURCE_DUAL_CORE.md)：同服务组真实两路、控制停止与显式重启的短验收合同。
- [CPU配额饱和后的恢复](LOW_RESOURCE_CPU_PRESSURE.md)：有界真实代理压力、原始配额证据和固定恢复时限。

- [Root 安装父组资源合同](LOW_RESOURCE_ROOT_INSTALL.md)：2026-10-11 准确 a373 安装/新目录升级/恢复通过，父组峰值 471.21484375 MiB、max/OOM 全零；历史失败及未现场复现的停启竞态保留。

- [新 runtime 压缩归档冷读](LOW_RESOURCE_RUNTIME_ARCHIVE_CACHE.md)：私有新文件的身份保护与本地机制实验；该产品字节已在 a373 root 验收，不据单次峰值断言因果或稳定改善。

- [双开关 M2：来源路由与运行事务](DUAL_PROXY_RELAY_M2.md)：候选实现、固定内核边界修订、恢复和准确验收入口。

- [双开关 M3：面板、分层诊断与严格导出](DUAL_PROXY_RELAY_M3.md)：候选交互、固定诊断目录、证据语义与独立导出边界。

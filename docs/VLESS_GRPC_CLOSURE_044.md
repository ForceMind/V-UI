# v0.4.4 VLESS/gRPC/TLS 主线收口

本页记录 2026-10-05 已完成的 gRPC Lite 主线验收。它将 v0.4.4 提升为已验证继承基线，不构成 v0.4.5 Hysteria2/TLS 的通过证明，也不表示附件晋升、公开发布或部署。[原阶段契约](VLESS_GRPC_044.md)完整保留首次失败、前置通过与当时的候选待验收文本；更早 [v0.4.2](MAINLINE_CLOSURE_20261005.md) 和 [WS](VLESS_WS_CLOSURE_043.md) 历史保持不变，PR #14 继续排除。

## 准确合并身份

- [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常 merge，来源分支 `iteration/v0.4.4-vless-grpc` 保留。
- 最终准确 master：`84729dfc53165003e7d459a5d56621ce89ba497c`。
- 审查通过的准确候选：`240edf23af8a12b2cbd71114fe65c693290e39f2`。
- 候选与合并 tree 相同：`200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc`。
- 合并父提交：`1b3ec40cd3bb640246d12afa104db0aec08ce336`、`240edf23af8a12b2cbd71114fe65c693290e39f2`。
- 签名合并对象已准确重建并核对哈希；签名验证、两个父提交、来源祖先关系及本地/远端 tree 一致性均已核查。终态复读 master 未变化。

## 准确候选与主线门槛

八组 exact-candidate 和八组 exact-master 工作流均成功。最终 master 共 11 个 job，每一步成功，均为首次 attempt；最新准确主线检查集与下列八个运行一致。queued、running、skipped 不计作通过。

| 工作流 | 准确 master 结果 |
| --- | --- |
| Test V-UI | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481946) |
| Selected release deployment gates | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481843) |
| Documents and release contracts | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481848) |
| Real loopback proxy and DNS chain | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481786) |
| One-command installation acceptance | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481839) |
| ACME certificate acceptance | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481865) |
| ToClash reference and export verification | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481909) |
| Portable Linux matrix | [成功](https://github.com/ForceMind/V-UI/actions/runs/37291481755) |

## 已验收的限定契约

- 官方 sing-box 1.14.2 服务端/客户端未变，实际构建没有 `with_grpc`，使用 gRPC Lite；Mihomo 1.19.32 未变。
- 仅 sing-box/VLESS/gRPC/TLS、单 UUID、空或省略 flow、明确 SNI、正常证书校验和 HTTP/2；ALPN 省略或恰为 `["h2"]`，Chrome fingerprint 独立可选。
- 字面 `service_name` 严格为 `[A-Za-z0-9._-]{1,128}`，区分大小写；`.`、`..` 和 128 字符名称均由双客户端真实验证。不 trim、强转、路径归一化或允许 slash/query/百分号转义/Unicode、authority/Host/header、timer、多模式。
- 标准 URI `type=grpc` / `serviceName`、完整 Mihomo YAML `grpc-opts.grpc-service-name`、sing-box JSON `transport.service_name` 保留凭据、TLS/SNI、受支持 ALPN/指纹，不包含私钥或服务端材料路径。
- 仅 HTTP/TCP；Mihomo `udp: false`、sing-box `network: tcp`。无 authority/Host 白名单、QUIC、Xray gRPC、反代/CDN 部署或其他协议扩展。
- 共用编辑器/编译器/托管证书；已有 UUID 隐藏且稳定，已有 gRPC UUID 缺失或畸形时编辑拒绝，不生成替代凭据。未知导入 TLS/transport/meta 字段、显式 null ALPN、畸形 SNI/flow/type 不能通过普通编辑静默变为可公开配置。
- 创建/取消/编辑/刷新/再打开/回填、h2 保留、绑定、三格式导出与故意损坏后的停机备份/恢复完成；失败签发保留旧材料和已应用 revision，停止核心继续 `CORE_STOPPED_PENDING_APPLY`。

## 验证深度与保留限制

普通本地及 CI discovery 共运行 298 项测试并报告 OK，其中 68 项明确为环境门控 skip；skip 不是激活通过。七项本地激活真实配置/导出方法无 skip 通过。准确主线真实链路共 53 项测试通过，包括裸核心前置与匿名、无 cookie 的公开订阅客户端配置。

两种客户端均实际送达正确 HTTP 请求；错误 UUID/CA/SNI/service name/大小写分别拒绝，先以可达 IP 证明正向目标，负向零目标请求且无 DIRECT。公开 CA/SNI 负向覆盖默认/Chrome × 默认/显式 h2 四种独立组合，并要求实际 x509 原因。实际 Chromium 完成完整编辑/证书/恢复流程。独立审查在修复后清除阻断，另行重跑 73 项聚焦测试与文档、JS、shell、diff 检查。

第一次前置 `008608afacf119780eacce97df0207d5e0fa9c9f` 的两个 sing-box x509 日志断言失败，后续 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 才通过八组工作流；详细历史和链接仍在[原阶段契约](VLESS_GRPC_044.md)。只有 sing-box 负向测试子进程使用 `GODEBUG=http2debug=1`，取得真实 unknown-CA/wrong-name 证据；没有改 pin、TLS 校验或主机信任。**Lite 调用者遇到错误 CA/SNI 仍可能等到超时；诊断可证明拒绝，不能承诺及时错误传播。**

本地实际运行在 netlink socket EPERM 阻断；本地 Chromium 在 UI 交互前因 socket EPERM 阻断。两类尝试保留，不写成本地运行通过；真实运行和浏览器通过来自未削弱的 CI。发布前对象验证还捕获过重建提交时区错误及中间 tree 的 install.sh mode 不符，修正后准确对象/tree 才获确认，没有 force/reset/stash/clean 或凭据、Git 身份更改。

## 发布边界

本 gRPC 阶段没有创建 tag、Draft Release、公开 Release 或生产部署，没有下载、晋升或发布新的准确 master CI 附件。先前 v0.4.2 套件不被替换或重建；附件晋升与发布准备由单独流程负责。四目标 Linux、40 项 ToClash、FastAPI/SQLite、固定来源与依赖清单保持。后续 HY2 修改必须验收自己的准确提交，不能复用本页绿灯。

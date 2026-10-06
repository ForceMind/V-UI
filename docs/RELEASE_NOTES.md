# V-UI 0.4.6

当前为 [Draft PR #22](https://github.com/ForceMind/V-UI/pull/22) 的 TUIC v5/TLS 集成候选发布说明。独立源码审查、八组准确候选 CI、授权正常合并和八组准确主线 CI 仍待完成；不表示 tag、Draft Release、公开 Release、附件晋升或部署。

## 已验证继承基线

- [v0.4.2](MAINLINE_CLOSURE_20261005.md)、[v0.4.3 WS](VLESS_WS_CLOSURE_043.md)、[v0.4.4 gRPC Lite](VLESS_GRPC_CLOSURE_044.md)保持各自已验收边界，PR #14 继续排除
- [v0.4.5 HY2 PR #20/#21](HYSTERIA2_CLOSURE_045.md)收口到 master `838c66d9974dd9f3a944641a2e9e03cc200e0bbe`、tree `e12287d8ebbea233142d58191ee5141a0a49a17a`，最终八组/11 jobs/全部步骤 attempt 1 成功；真实链路 63 项和独立托管 HY2 8 项通过
- [PR #23 普通响应安全修复](INBOUND_RESPONSE_CLOSURE_20261006.md)收口到签名 master `8e0d07463e59b52856f55fa33346a760a38b4705`、tree `fda9f9d8a26dd21bdf0e441ab5d9bbc7ec0dbdd8`，准确候选/主线各八组完成；最终 11 jobs/全部步骤 attempt 1 成功。普通 discovery 371 项含 80 项明确 skip，真实链路 63、托管 HY2 8 和 Chromium 流程独立验证

普通列表/创建/更新使用允许列表，不返回 UUID/密码、原始配置、未知秘密或服务端材料路径。特权 `/editor` 保留手工证书路径字符串；明确授权客户端导出仍含必要凭据且无服务端材料。数据库/核心/备份不做脱敏写回；没有证据声称真实泄露事件或生产轮换。

## TUIC v5/TLS 候选

- 不改变官方 sing-box 1.14.2 / Mihomo 1.19.32 pin、摘要、构建或依赖；单 UUID/密码对、明确验证 SNI、服务端 ALPN 恰为 h3（不可省略）、原生 QUIC/默认拥塞和 heartbeat、零 RTT 关闭
- 三格式为固定 Mihomo 导入器支持的 TUIC URI 约定、完整 Mihomo YAML、sing-box JSON。URI 是临时/非官方客户端约定，不是官方通用标准；保留编码后的凭据、SNI/h3 和验证语义，无服务端材料
- 仅 HTTP/TCP；sing-box `network: tcp`。Mihomo TUIC adapter 硬编码 UDP 能力，省略无效 `udp: false`，不宣称禁用 UDP；应用 UDP 未验收，QUIC 传输本身仍须节点 UDP 可达
- UUID/密码分别空输入新建生成、编辑保留；非法已有凭据拒绝，不靠无关编辑生成替代。高级/未知导入字段保留或拒绝，不能静默清除后公开导出，失败不退 DIRECT
- 复用证书绑定/换绑/续期、失败保留旧材料与已应用 revision、停止核心 `CORE_STOPPED_PENDING_APPLY`；当前集成须验新 QUIC 会话和 Chromium 创建/取消/编辑/刷新/再打开/导出/损坏后停机恢复
- FastAPI/SQLite、40 项 ToClash、四目标 Linux、systemd/OpenRC 和已有协议基线保持。UDP 安装声明继续显式、可重复、区分协议与精确确认，不自动启用防火墙

## 证据与待完成门槛

裸前置 `21cb0bc721dd4f5e4d172d7602c8135f2f04db91` 八组/11 jobs/全部步骤 attempt 1 成功，[真实链路 69 项](https://github.com/ForceMind/V-UI/actions/runs/37356266391)含双客户端正确 HTTP、分别错误 UUID/密码/CA/SNI，真实 unknown user/token mismatch/x509 原因、零送达和无 DIRECT。该证据不包括后续集成变更。

正常 merge-forward `7c380d7c9d7948f4e1992cbb5404b805904a6b57` 的父提交为上述前置与安全 master `8e0d0746`，tree `5cce37c23cfc4282f169cbaec4693df4c1e86a8b`。此前本地集成/证据因工作区回退丢失，当前重建重测，不复用旧本地通过；历史 runtime/browser EPERM 不算成功。

该 merge-forward 已核对八组/11 jobs/全部步骤 attempt 1 成功；本地九个固定二进制配置/parser 方法通过，加入 Unicode/空格密码后的两个 parser 方法再次通过。新鲜公开链路和 Chromium 尝试仍受 socket/netlink EPERM 阻断，不计运行通过。

新公开订阅、实际 URI provider 导入、证书和浏览器门槛仍须最终准确提交验证；独立审查、八组 exact-head、授权正常 merge、八组 exact-master 都待完成。详见[TUIC 契约](TUIC_046.md)。

## 保留失败历史与发布边界

[HY2 原历史](HYSTERIA2_045.md)保留首次 `6daff89e` 缺日志、`e1800367` 链路 58 项通过但 ACME DNS TCP/UDP 端口碰撞，以及首次 `dbf1cfbe` master 继承 Trojan/gRPC 原因日志失败；[后续收口](HYSTERIA2_CLOSURE_045.md)记录 `5ee7e35` 的有界证据修复及 portable 首次 API rate limit 失败/attempt 2 成功。后来的成功不改写这些记录。

gRPC Lite 错误 CA/SNI 仍可能向调用者超时，真实诊断不是及时错误传播保证。所有负向都要求真实拒绝原因，不能只用超时/零送达推断。

本阶段无 tag/Release、附件晋升、真实 CA/账户、实际主机防火墙变更或部署。版本安装 URL 仅在正式公开后存在，源码、CI 原始附件与已核验发布套件分开，历史套件不替换或重建。正式发布须另按[发布检查](RELEASING.md)完成。

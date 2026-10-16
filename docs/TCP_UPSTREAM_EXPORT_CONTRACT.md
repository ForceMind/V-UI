# D1：显式 TCP-only 中转上游导出契约

## 1. 目标与基线

基线为已验 master `13c884a0539cf9822fca2fe2db58325fa4644048` / tree `ede0dd0626fb34b06e0a5f7dbbae6c46d98384d5`，[PR #46](https://github.com/ForceMind/V-UI/pull/46)记录 D0 的准确主线八组与四目标验收。D0 的普通 Shadowsocks UDP/DNS 证据不授予中转 UDP 资格。

本期交付一个可用功能：管理员在节点操作中明确选择“导出为中转上游（仅 TCP）”，将本项目已验 VLESS/TCP/TLS 或 REALITY-Vision 节点导出为 URI 或单节点 Clash YAML，直接导入另一 V-UI 的现有上游导入流程。新导出是单独的用途 profile，非对用户粘贴配置的静默降级。

不改普通分享、订阅和客户端导出的既有字节语义；不放宽上游导入 allowlist，不删除输入中的 `udp` / `packetEncoding` / `packet-encoding` 绕过拒绝。不开放 UDP 双跳、TUN、全机路由或新的协议/传输，不升级固定核心、VERSION 或数据 schema。

## 2. 固定接口

- `POST /api/subscription/upstream/{inbound_id}`，仅现有管理员鉴权及同源写请求防护可用；订阅 grant、匿名请求均无权获取。导出不保存状态或应用核心。
- JSON 对象恰为 `format`（`uri` / `clash-yaml`）、`server`（显式字面主机/IP）、`acknowledge_tcp_only`（严格布尔 true）。额外字段、类型强转、无确认或无主机均拒绝。不从请求 Host 自动生成未确认目标。
- 成功返回实际文本内容：URI 为 `text/plain`，YAML 为 `application/yaml`。`Cache-Control: no-store`、`X-VUI-Export-Profile: tcp-upstream-v1` 和安全固定下载文件名；不在普通 JSON 列表、错误或日志中回显凭据。
- 错误采用固定非秘密说明：404 `TCP_UPSTREAM_INBOUND_NOT_FOUND`、409 `TCP_UPSTREAM_EXPORT_INELIGIBLE`、422 `INVALID_TCP_UPSTREAM_EXPORT_REQUEST`、503 `TCP_UPSTREAM_EXPORT_UNAVAILABLE`；既有 401/403 鉴权规则保持。不能返回半成品或旧普通导出作 fallback，也不能由框架 422 回显原始输入。
- 只收 `application/json`，拒绝重复 JSON key、非有限数字及 query 参数；实际流式请求体上限 2048 bytes、总读取 10 秒，分别以固定 413/408 拒绝，断连为固定 400。不得仅信任 Content-Length，不完整或失败请求不导出。

## 3. 资格与保真

仅已启用、非定时过期、sing-box 的单用户 VLESS、直接 TCP、验证 TLS 或已验 REALITY-Vision。复用现有节点严格校验与 `UpstreamProfile`，完整保留服务器/端口、UUID、TLS SNI、允许的 ALPN/Chrome 指纹、flow、REALITY 公钥与 short ID。多凭据、未知或不能保真字段、不支持传输/TLS 跳过校验均拒绝。

从经过校验的节点模型构造新的 TCP-only 用途 profile，URI 不宣称应用 UDP；单节点 YAML 显式 `udp: false`，无整个客户端规则/provider。服务端证书、私钥和材料路径均不输出。省略 URI 编码本身不被当作禁用 UDP 的证据，导入后的既有运行策略仍须 `network: tcp` 与所选入口应用 UDP reject。

目标 server 先执行严格字面主机/IP 校验，再明确将 ASCII 大写转换为小写，使 URI 现有解析器与 YAML 产生同一规范 profile。此 host-only 表示正规化不 trim、不扩展可接受地址、不改变源 TLS SNI 或其他字段；无效字面值不能先正规化再接受。主机校验纯解析、不联网或解析 DNS；另一实例实际应用时仍完整执行既有地址固定/私网、自身及 metadata 拒绝。导出成功不保证该上游公网可达。

## 4. 面板交互与秘密生命周期

在节点操作提供独立入口，明确说明与普通设备导出不同、仅作 TCP 中转上游、内容含客户端连接凭据。管理员填写/确认目标主机、格式及 TCP-only 用途后，才请求内容并显示复制/下载。取消、关闭、切换节点/主机/格式、失败、刷新或会话失效清除旧内容；异步旧响应不得落到新选择。复制失败有明确反馈和安全手动选择路径，下载通过本地 Blob 且及时回收 object URL。

不得把导出内容写入 localStorage/sessionStorage、URL、控制台或普通节点响应。使用纯文本显示，不解释备注/主机/错误中的 HTML。默认双关、四组合及旧页面操作保持。

## 5. 必须验收

1. 普通导出/订阅 golden 不变；真实节点模型→专用 API 导出→现有解析/预览/明确选择/保存，TLS/REALITY × URI/YAML 的规范 profile 保真。
2. 严格未知字段、多凭据、额外传输、禁用/过期、错误类型、缺确认、host 注入与鉴权/订阅权限；错误和普通响应无秘密、失败无状态变化或核心操作。
3. 两个隔离数据目录、实际 HTTP API 的独立面板实例：源实例节点→专用导出→目标实例现有导入/保存/应用；固定真实核心完成 TLS/REALITY 两跳 HTTPS、可核出口来源及同上游业务 DNS。不能只用手造等价 profile 或 mock API 代替。
4. 错误 UUID/CA/SNI、上游停止必须单次真实失败且有明确原因、目标零送达、无 DIRECT/普通 DNS 兜底；既有 UDP 拒绝和生命周期最终 drain 不弱化。
5. 实际浏览器的确认/复制/下载/导入、取消/刷新/异步过期/会话失效与不支持节点流程；浏览器 UI mock 与真实网络链路分别登记，缺依赖在显式 CI 门槛中 fail。
6. 独立审查、冻结同树完整本地套件、准确候选八工作流、正常合并后准确主线八工作流及同提交四目标 19 项资产。所有失败保留，不借 D0 旧绿灯。

## 6. 提交与交付边界

本期常规开发、测试、文档、提交、PR 与准确验收后的正常合并已授权；无新 Release/tag、生产部署、用户电脑网络设置、外部服务凭据或全局权限。公开 v0.4.8 仍是 a9f9b5d9 安装器基线，本期功能不覆盖其旧资产。准确实现 SHA、计数和最终失败/修复账本在承载 PR 记录，不在本合同预填成功。

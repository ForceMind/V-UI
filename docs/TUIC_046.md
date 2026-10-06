# v0.4.6 TUIC v5 验收契约

## 当前状态与继承

当前为独立 [Draft PR #22](https://github.com/ForceMind/V-UI/pull/22) 的 TUIC v5/TLS 集成候选，不表示已发布或部署。HY2 [PR #20/#21 主线收口](HYSTERIA2_CLOSURE_045.md)的 master 为 `838c66d9974dd9f3a944641a2e9e03cc200e0bbe`，tree `e12287d8ebbea233142d58191ee5141a0a49a17a`；它和 [PR #23 普通响应安全修复](INBOUND_RESPONSE_CLOSURE_20261006.md)是已验收继承基线，历史失败不删除。

裸前置 `21cb0bc721dd4f5e4d172d7602c8135f2f04db91`（tree 前缀 `ad230c24`）已完成八组工作流、11 个 job、全部步骤 attempt 1 成功。[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37356266391)共 69 项通过，含双客户端正确 HTTP，以及分别错误 UUID、密码、CA、SNI；保留真实 `authentication: unknown user` / `authentication: token mismatch` / x509 原因、零目标送达、无 DIRECT。这只是裸前置证据。

安全修复通过正常 merge-forward `7c380d7c9d7948f4e1992cbb5404b805904a6b57` 继承，父提交为上述前置与 `8e0d07463e59b52856f55fa33346a760a38b4705`，tree `5cce37c23cfc4282f169cbaec4693df4c1e86a8b`。此前本地集成与证据在工作区回退时丢失；当前重新构建、重新测试，不复用旧本地通过。历史本地 runtime/browser 的 EPERM 阻断不算成功。

**最终 TUIC 集成仍待独立源码审查、八组 exact-head、授权正常 merge 和八组 exact-master。** 新增的公开订阅、真实 URI provider 导入、证书和 Chromium 测试不因代码已写而算作通过；仅追加当前准确提交的新鲜验证结果。

## 本次重建的新鲜证据（2026-10-06）

正常 merge-forward `7c380d7c` 已逐项核对八组工作流、11 个 job 和全部步骤 attempt 1 成功。以下只对应该准确合入安全修复的前置提交，不覆盖当前尚未提交的 TUIC 集成源码：

| 门槛 | merge-forward 记录 |
| --- | --- |
| Documents and release contracts | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810189) |
| ToClash reference and export verification | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810169) |
| ACME certificate acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810203) |
| One-command installation acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810190) |
| Portable Linux runtime matrix | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810340) |
| Test V-UI | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810258) |
| Real loopback proxy and DNS chain | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810198) |
| Selected release deployment gates | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37463810228) |

当前本地重新执行的九个固定二进制服务端/客户端/parser 方法已通过，包括真正 Mihomo URI-provider 导入；加入空格和 Unicode 合成密码后，两项实际 parser 方法再次通过。这是配置/解析证据，不能写成转发通过。

本地公开订阅链路再次被未改变的 sing-box netlink socket EPERM 阻断；新鲜 Chromium 尝试同样被 `socket()` EPERM 阻断。两者均记为本地未运行完成/环境阻断，不计 runtime 或浏览器通过。

当前源码审查发现并修复“原导入缺失 TLS 可被部分编辑变为合格配置”的问题：有意修复须明确 `security: 'tls'`，已有 TLS 对象缺少 ALPN 仍拒绝。审查未留该问题，但最终准确候选的独立审查与八组门槛仍须完成。新增两个 TUIC 专用模拟安装回归覆盖同号 TCP/UDP 与仅 UDP 同意；不操作真实防火墙，不将测试代码存在等同通过。

## 固定实现与严格公开范围

- 官方 sing-box 1.14.2 服务端/客户端、Mihomo 1.19.32、归档 SHA-256 pin 和构建保持不变，不重编译、不替换核心
- sing-box [固定 go.mod](https://github.com/SagerNet/sing-box/blob/v1.14.2/go.mod)使用 sing-quic `6a3a24d65b99587fad1d4cdd567c88f212acdd63`，[TUIC wire version](https://github.com/SagerNet/sing-quic/blob/6a3a24d65b99587fad1d4cdd567c88f212acdd63/tuic/protocol.go)为 5；[固定 Mihomo adapter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/tuic.go)的 UUID/password 路径使用 v5，token/v4 排除
- 单 UUID/密码对、TLS、明确验证 SNI；服务端 `tls.alpn` 必须恰为 `["h3"]`，不能省略、null、空列表或替换为 h2。新建明确写入 h3，客户端同样保留 h3
- 原生 QUIC，默认拥塞控制（cubic）、默认 heartbeat、零 RTT 关闭；无 uTLS/指纹覆盖、调优、obfs、hopping、多用户、REALITY 或其他协议扩展
- 严格公开导出仅三格式：固定客户端支持的 TUIC URI 约定、完整 Mihomo YAML 和 sing-box JSON，保留 UUID/密码、SNI、h3 与正常 TLS 验证；不含服务端私钥或材料路径
- TUIC URI 来自[固定 Mihomo converter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/convert/converter.go)支持的临时/非官方客户端约定，**不是官方通用 URI 标准**。格式为 `tuic://<encoded-uuid>:<encoded-password>@<server>:<port>?sni=<name>&alpn=h3#<name>`；凭据用百分号编码，正常验证/零 RTT 关闭沿用该固定导入器安全默认值，不虚构 URI 开关
- 仅 HTTP/TCP 应用负载验收。sing-box 出站写 `network: tcp`；Mihomo TUIC adapter 硬编码 UDP 能力，**省略无效的 `udp: false`，不能宣称它禁用了 UDP**。应用 UDP 未验收，URI 也不承诺导入客户端的应用 UDP 限制
- QUIC 传输本身要求实际节点 UDP 端口可达，TCP 放行不能代替 UDP；QUIC 会话、UDP socket 或既有 Shadowsocks UDP 证据不构成本阶段应用 UDP 验收

## 凭据、导入与证书

`profile.tuic_uuid` 与 `profile.tuic_password` 独立处理：省略/空字符串在新建时生成，在编辑时分别保留；非空输入仅替换对应项。UUID 为规范的连字符 UUID 字符串；密码为 1–256 字面字符，不 trim，不允许全空白、Unicode Cc 或无效 UTF-8 surrogate。错误类型/值与已有缺失/畸形凭据拒绝，不能靠无关编辑生成新凭据掩盖导入问题。

普通列表/创建/更新沿用 PR #23 允许列表，不回传 UUID/密码、原始配置或服务端材料路径。特权 `/editor` 返回空凭据输入与存在标志，仅保留手工证书路径字符串能力；明确授权导出仍有客户端凭据，没有服务端材料。

已有不支持参数保留或在无法表达时拒绝，不靠编辑静默清除。高级拥塞/relay/零 RTT/ALPN 草稿即使可保存也不符合严格公开导出；缺少服务端明确 h3 的原始导入不能被无关编辑悄悄修复成公开配置。没有失败时 DIRECT 回退。

复用正式托管证书：按 SNI 绑定/换绑，续期保留 UUID/密码/配置，以新 QUIC 会话证明新材料生效；失败保留旧活动材料及已应用 revision。停止核心保留 `CORE_STOPPED_PENDING_APPLY`，不因续期自动启动。浏览器须覆盖创建、取消、编辑回填、刷新、再打开、独立凭据保留、三格式导出和故意损坏后的停机恢复。

## 集成门槛

1. `test_tuic_profile.py`：严格字段与服务端 h3、凭据编码/隐藏/独立保留、导入草稿保护、三格式、普通响应允许列表
2. `test_export_real.py`：两种固定客户端 config check；TUIC URI 还须通过固定 Mihomo 实际 provider/converter 导入，不以手工解析替代
3. `test_tuic_preflight_loopback.py` 与 `test_tuic_loopback.py`：裸前置和应用编译/无 cookie 公开订阅分别验收；每客户端正向 HTTP，以及分别错误 UUID/密码/CA/SNI。负向先有可达 IP 正向控制，要求真实原因、零目标请求、无 DIRECT
4. `test_tuic_rejection_evidence.py` 与继承的 TLS collector 回归：同一行 x509 与具体原因；有限观察窗口内目标计数固定，不重置计数掩盖晚到数据。超时、一般 TLS 行或本地 listener 成功日志都不够
5. `test_tuic_managed_certificate.py`、`test_inbound_editor_browser.py`：证书完整生命周期、新 QUIC 会话与 Chromium 完整编辑/损坏恢复，默认 skip 不算实际通过
6. 原 40 项 ToClash、四目标 Linux、显式且协议区分的 UDP 安装预检、ACME、部署 gate 和文档版本检查完整保留
7. 独立审查、八组准确候选工作流、授权正常合并、八组准确 master 工作流分开核验；queued/running/skip、旧主线或裸前置成功不能替代

本阶段无 tag、Draft Release、公开 Release、附件晋升、真实 CA/账户、生产凭据、实际主机防火墙变更或部署。`Selected release deployment gates` 是临时环境验收，不等于生产部署。

## 原始前置记录（保留）

以下是首次前置文档原文；其中 local pass / pending 仅是当时记录，不是本次重建结果，当前状态以上文为准。

# 原始 TUIC v5 bounded characterization

Base: master `838c66d9974dd9f3a944641a2e9e03cc200e0bbe`, tree `e12287d8ebbea233142d58191ee5141a0a49a17a`. HY2 PR #20/#21 final mainline passed eight workflows / 11 jobs; their earlier failures remain historical evidence. This stage is a candidate, not a released or deployed version.

## Actual fixed implementation

- Official sing-box 1.14.2 and Mihomo 1.19.32 binaries, archive SHA-256 pins and builds unchanged
- sing-box [go.mod](https://github.com/SagerNet/sing-box/blob/v1.14.2/go.mod) pins sing-quic `6a3a24d65b99587fad1d4cdd567c88f212acdd63`; its [TUIC wire constant](https://github.com/SagerNet/sing-quic/blob/6a3a24d65b99587fad1d4cdd567c88f212acdd63/tuic/protocol.go) is Version 5
- Mihomo [fixed adapter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/tuic.go) uses the v5 client for UUID/password, v4 only for token. Tokens/v4 are excluded
- One UUID/password pair, normal verified TLS with explicit SNI, native QUIC, explicit `alpn: [h3]` and zero-RTT disabled. Congestion and heartbeat defaults stay unchanged; no tuning, obfs, hopping, REALITY or protocol expansion
- HTTP/TCP payload qualification only. QUIC needs node UDP reachability, separate from application UDP relay. sing-box outbound is `network: tcp`. Mihomo's fixed TUIC adapter advertises UDP support regardless of `udp: false`; no enforcement or tested application-UDP support is claimed
- TUIC URI uses the [fixed Mihomo converter's supported client convention](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/convert/converter.go), which upstream explicitly calls temporary/unofficial. Do not describe it as an official universal URI standard. UUID/password are percent-encoded userinfo; `sni`/`alpn` are preserved, insecure and zero-RTT stay disabled by supported defaults

## Evidence gates

Bare config checking of server and both clients passes locally with fixed binaries. Actual local forwarding is separately attempted and may be blocked by environment socket/netlink permissions; such failures never count as runtime success. Real CI must prove both clients reach an HTTP target, then independent wrong UUID, password, CA and SNI refuse delivery without DIRECT. Credential reasons must distinguish `authentication: unknown user` and `authentication: token mismatch`; TLS needs x509 and the specific reason on the same log line. Timeout alone is never evidence. Target delivery count remains fixed throughout bounded negative observation.

Preflight must pass before public TUIC exports are admitted. Integration then covers three exports, real parser checks, shared hidden/blank-preserving credentials, imported advanced fields retained or rejected, certificate bind/renew/failure/stopped application, Chromium create/cancel/edit/refresh/export/damaged stopped restore. Four Linux targets, 40 ToClash presets and explicit protocol-aware UDP installer tests remain mandatory. No real firewall changes, real CA/account, tag/Release, deployment or artifact promotion.

Independent review and all eight exact-head workflows are required before parent-approved normal merge; all eight exact-master workflows are required afterward. This preflight does not claim final support.

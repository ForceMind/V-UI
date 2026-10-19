# 精简国内 UDP：同头认证、目的地址与 DNS 守卫计划

状态：2026-10-19 安全中止，尚未运行本组，见[阻断交接](DOMESTIC_UDP_GUARDS_BLOCKED_20261019.md)。下文保留已冻结设计，不能据此恢复执行。接续 [精简结果](DOMESTIC_UDP_REDUCTION_RESULTS_20261018.md)，从公开 `4224dce2379650482223644e3522403a952b59dc` 开始。先冻结本计划、独审源码，再启动一次有界 CI；旧完整八补丁认证成功不能给精简核心授资格。

## 固定角色与边界

- 设备只用 stock Mihomo 1.19.32，海外只用官方 sing-box 1.14.2；国内与国内重配置进程均为四原补丁 + 独立 Count 的新同头构建。三个角色 owner 选项均缺省。国内研究配置统一显式 `udp_timeout=2s`（原护栏默认 30s），这是沿用精简候选已测配置的受控适配，本阶段没有 idle 资格。沿用全部准确源码、模块图、编译器与 124 文本构建证明；不再构建完整八补丁参考臂。
- 普通生产导出器生成的 VLESS 节点原样进入隔离 mixed listener shell，所有重配置/恢复仍从同一份普通导出节点生成，只有明确的设备认证坏字段在对应负控改变。每个 fixture 先通过普通导出路径的一次 1200 字节完整 payload/目标 tuple 正向。这里验证生产导出器，不称重新执行产品 HTTP API。
- 现有 raw VLESS/XUDP 探针只用于服务端恶意目的地址与 DNS 守卫。它是独立的已验证 TLS 同协议攻击夹具，不是 Mihomo 域名路径、手机 App 或 TUN 验收。payload 不超过 1200，不增加协议/核心角色、公共线路、端口服务或网络权限。
- 产品 pin、有效 relay UDP 拒绝、默认配置、普通导出语义、发布和部署不变。旧 C/D/B/E 与 idle 记录不改判，本轮不重跑；本轮也不继承旧 auth/P4 为新头资格。

## 唯一矩阵：9 源方法 / 14 fixture child

原来源为冻结 `afbdba100c5f0e3dc6888684fdbab31980720df3` 的 `tests/test_restricted_udp_preflight.py`、`restricted_udp_preflight_helpers.py` 与 `experiments/xudp_address/run_experiment.py`。原文件不修改；调度器复用这些源方法语义拆成 14 child，使用固定 guard-* 小负载与统一 1200 前置，并非逐字节调用原方法。

| 组 | 精确来源 / fixture 数 | 本轮语义 |
| --- | --- | --- |
| 第二跳认证 | test05，4 | 错 UUID / 错 CA / 错 SNI / 停海外；每项独立正向 → 一次坏包 → 完整 2 秒新拒绝原因及零目标 → 修复后正向 |
| 第一跳认证 | test07，3 | 错 UUID / 错 CA / 错 SNI；相同前置、坏包与恢复要求 |
| TCP sanity | 原 make_tcp_tests 的 Mihomo 方法，1 | 前置 UDP 后独立验证 TLS、HTTP 200、固定正文及唯一海外来源；不称 UDP 容量资格 |
| DNS 全答案 | test09，1 | good 正向；private、mixed、reverse、IPv6 sibling、public IPv6 sibling、self、upstream 七类拒绝，另 literal private |
| DNS family 失败 | test10，1 | 设备启动使用 Mihomo；除本节统一前置/负载/调度适配外，raw 域名语义不变。A 成功/AAAA SERVFAIL 仍正确转发；两 family SERVFAIL 无回退 |
| 裸 retarget 正控 | test11，1 | 普通导出 guarded 前置通过后，正常停止国内/device，再以裸研究配置重启；证明 raw 探针能实际命中不同目标。不能作为产品放行 |
| guarded retarget | test12，1 | 同 raw association 先合法包，再原 9 次地址/端口变化；只能送达最初已检查目标，所有改变目标 trap 不得收到负包；这是 udp_connect 固定初始目标，不称每包重新授权 |
| 同域名同端口重绑定 | 原 make_dns_tests，1 | 不重复较弱的 test13 不同端口向量；首次 checked answer 使用一次，原关联 DNS 改变仍绑定旧地址，新关联看到私网答案须拒绝 |
| 新关联 literal / low port | test15 + 原产品 TCP 测试 forbidden literal 向量移用，1 | 先高端口成功；53/443/1023 拒绝；13 个固定私网/metadata/self/IPv6别名 literal 拒绝，见下节 |

合计 3 个认证/TCP 源方法 + 6 个护栏源方法；实现按 14 个独立有界 child fixture 执行，逐 child 原始安全重核后再继续，不把源方法分母混作实际 child 数。7 个认证 fixture + 1 个 TCP fixture + 6 个护栏 fixture。每个 fixture 的计划动作提前固定，实际执行必须是合法前缀；前置失败后的故障注入/负向/恢复明确列入未执行，不以方法失败代替阶段事实。

literal 固定为：`127.0.0.7`、`127.0.0.1`、`127.0.0.2`、`100.100.100.200`、`169.254.169.254`、`10.0.0.1`、`::1`、`::ffff:127.0.0.7`、`::ffff:127.0.0.1`、`::ffff:169.254.169.254`、`::ffff:100.100.100.200`、`64:ff9b::7f00:3`、`2001:db8::1`。IPv6 是拒绝探针，不授 IPv6 转发。

## 出口隔离与负向证据

官方海外研究配置仅允许 `127.0.0.0/8` 字面 IP 目标，其他地址及未解析 domain terminal reject，不调用宿主 resolver。国内保留原被测规则顺序，并在其终止 reject 之后增加独立 containment：未解析 domain 拒绝、只有受控 loopback 可进入既有 DIRECT trap、其余终止拒绝。负向成功必须匹配原 guard 的准确规则 index/连接/目的地，新增 containment 的 reject 不能授通过。该限制不改变官方 binary，也不改变国内原被测守卫；它防止守卫缺陷引起实际外部访问。若国内未拒绝而把 forbidden 请求送至海外，即使海外 containment 拒绝，仍判本项失败。每 fixture 正前置必须实际观测固定海外 `inbound packet connection to` 标记，证明日志观测器有效；负向从真实 send 至完整窗口核无新增标记，最终 lifetime 日志继续兜底迟到。保留原始 ANSI/完整行并由 verifier 规范化；未知日志格式不能当作零。

可绑定的 loopback trap 先做独立 direct 可达性控制，保持整个生命周期 ledger。metadata、真实 private、IPv6 别名和未启动的低端口服务不能布置实际目标 ledger，故只授“国内实际拒绝 + 海外完整新请求/转发日志无对应尝试”；不得把本地 trap 零包外推为那些外部地址的真实零误送。

每次负包恰发一次，旁录真实 send tuple/payload hash/长度/返回值及紧邻发送的日志 checkpoint；拒绝观察结束时间须至少晚于实际 send 完成 2 秒。保留完整 fresh 换行记录边界及匹配原因，timeout 单独不足。SOCKS/raw 原关联和实际回复/控制帧均旁录，显式核无业务回复、无 DIRECT、无延迟/重复/错误来源目标交付。

DNS 不只保存预设 records：旁录真实问题、源、答复 wire/rcode/解析答案，并与目标 ledger 绑定。test09 精确 16 个查询，test10 精确 4 个，同域名同端口精确 4 个，共 24 个（各阶段 A/AAAA）；全部源 `127.0.0.4`、ordinary DNS 为零。原关联重绑定中间不得再查，完整清理后仍核迟到/重复查询。

## 安全、归档与停止

- 各 fixture 资源清理独核，容量/认证红与资源安全分开；只有全部已拥有 PID/子进程组、应用 FD、目标线程与 DNS/HTTPS 服务已回收，才进入下一独立 fixture/组。未知/不安全立即停止后续。
- 协调器有明确 owned-process-group 时间界、TERM/KILL/join 证据；不是仅依靠整个 CI job timeout。允许协议失败的合法红文本保存，不修期望来绿化。
- 新 kind/精确 allowlist，独立重算每 fixture 已执行语义和未执行集合，不能仅总 passed 时验证。完整 124 构建证明、源码前后 identity、实际国内/海外/device hash、launch/stop、普通导出/config 摘要、原始目标/DNS/wire 与最终 post-identity 必须绑定同 head/run/attempt。
- 复用 no-follow 输出、严格 JSON/UTF-8、敏感信息检查、成员/总量预算与 manifest 摘要。上传前独立验证整份 text-only 归档；binary、配置、证书/私钥不上传。
- 纯控制至少覆盖角色错换/旧 build/缺 post、失败前置伪装负控、缺 raw 或多 send、短拒绝窗、假 fresh 原因、目标错送/迟到、DNS 多答案遗漏/重查/普通回退、缺清理及归档成员。任何恢复仅限真正的 fixture/归档缺陷，保留原失败。
- 原 domestic-reduction workflow 改为 manual，新的分支限定 workflow 经源码独审后再启用。一次有界矩阵终态、合法归档、独审和结果入仓后即停止；不无限扩矩阵。若精简代码存在真实缺陷，先记录实际边界并审最小修复，不扩回三角色候选或要求自研客户端。

## 实现 WIP

已保存独立 14 child 合同、真实发送/接收及 DNS wire 旁录、逐 case 驱动、独立 verifier、单快照 text-only 归档和协调器 WIP。16 个合成控制通过（57.895 秒、0 skip），覆盖完整 14 种产出形状及日志/原始包/DNS/身份/清理变异；它们不代表核心运行。完整归档控制和新 workflow 尚未完成，仍不得启动核心或授运行资格。旧 reduction workflow 已转 manual。

静态计划共 117 步：21 direct 可达性控制、47 正向代理业务包（31 SOCKS + 16 raw）、33 负包（17 SOCKS + 16 raw）、7 fault、7 restore、1 naked restart、1 HTTPS。完整执行应为 80 次代理应用发送、68 次目标 UDP 交付（21 direct + 47 海外来源）、24 DNS 查询、1 HTTPS；最终必须按真实 started/unexecuted 另算。

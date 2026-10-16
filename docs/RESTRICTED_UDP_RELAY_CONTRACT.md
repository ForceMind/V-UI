# D2：受限设备 UDP 中转工程合同

日期：2026-10-16。起点：[D1 / PR #47](https://github.com/ForceMind/V-UI/pull/47)，准确 master `a98da9afbbf58a54f4f102cccec2f26b6e07d2ee`，tree `913f3e784d78861c703fb6a4b94195b9d0f90c3b`。

**状态：真实可行性门槛正在验证，尚未开放产品能力。** 本文件描述必须满足的最小合同，不是已支持声明。旧候选/主线成功、配置检查、UDP socket 或超时不能替代本阶段真实证据。

## 1. 唯一新增路径与不变边界

一期 D2 仅申请资格：固定 Mihomo 1.19.32 设备客户端 → 固定 sing-box 1.14.2 的已鉴权 VLESS / 直连 TCP / 验证 TLS / 空 flow 入口 → 同样的海外 VLESS / TCP / TLS / 空 flow 上游，明确 XUDP 编码 → IPv4 应用 UDP 目标。上游 TLS/SNI/单 UUID 保持严格校验，不改变固定二进制、依赖或 VERSION。

两个主开关仍独立且默认关闭。新增设备 UDP 能力是中转开关下的独立显式选项，默认 off；服务器自身 mixed/SOCKS 入口始终保持应用 UDP 拒绝。REALITY/Vision、IPv6 应用 UDP、全机 TUN、任意客户端/Clash 协议、任意 UDP 端口/载荷尺寸/分片/吞吐/长期运行均不由本阶段自动获得资格。普通节点、订阅和 D1 的 TCP-only 专用导出不改变字节语义。

“XUDP 路径已验”只描述固定客户端及配置编码；固定核心不能通过现有路由元数据区分所有 native VLESS UDP 与 XUDP 输入，不能宣称入口只接受 XUDP。相同入口接受的其他编码仍必须被相同安全规则限制。

## 2. 先取证，再开放

1. 首先保留独立真实前置：UDP 目标直达可达对照、固定核心裸双跳与拟采用安全路由双跳，准确返回载荷、目标地址和目标端口都必须一致。
2. 认证/TLS/上游停止须取得该次新鲜真实错误和全观察期零目标送达，禁止 DIRECT 或本机 DNS 兜底。
3. 目标地址/域名、XUDP 同关联变目标、资源/停止和恢复门槛通过后，才实现产品开关和相应配置编译；有固定内核限制先报告，不删断言、换 pin 或冒充成功。
4. 当前云工作区已遇固定核心启动 `create netlink socket: operation not permitted`，原失败保留；只在既有隔离 GitHub CI runner 继续真实门槛，不改本机安全设置或提权绕过。配置 parser 自测与真实流量测试分别记录。

## 3. 显式能力与版本化

- 默认导入 `capability_mode = tcp-only`，保持原支持矩阵、拒绝码和规范化 profile 的 v1 序列化。未知模式拒绝。
- 新显式模式 `capability_mode = vless-tls-xudp-ipv4` 仅接受完整匹配的 URI 或单个被明确选中的 Clash YAML 节点。URI 要求 `packetEncoding=xudp`；YAML 要求严格布尔 `udp: true` 且 `packet-encoding: xudp`。缺失、空值、错误类型、冲突、未知字段、非 TLS/空 flow/直连 TCP 仍拒绝。多节点仍明确选择；忽略容器区块仍逐项确认，远程 provider/脚本不执行。
- V2Ray/Xray JSON 的旧 TCP-only 导入保持；D2 模式的 JSON 未获资格，明确拒绝，不猜测字段或自动添加 XUDP。
- 新 profile `schema_version = 2` 且必须有 `udp_capability = vless-tls-xudp-ipv4`；其余既有字段保真。v1 不补这个字段。D2 profile 可以作为 TCP-only 使用，但保留其完整能力声明，不能静默降级成 v1。
- 新 settings `schema_version = 2` 且必须有 `relay_udp_mode = off | vless-tls-xudp-ipv4`。启用要求 `relay_enabled = true`、明确所选 v2 上游和全部入口获资格。v1 不补字段；v2 的 UI 初始选择为 off，但每份 v2 请求和持久记录必须显式包含 mode；缺字段拒绝，不能凭默认值修补。未写 schema_version 仍按 v1 解释，不能因为新字段出现就猜成 v2。
- 当前草稿已为 v2 时拒绝 v1 settings 覆写，避免旧 UI 携带新 ETag 无意删掉 UDP 状态。关闭使用 v2/off；不能让老客户端或普通恢复静默降级。
- store/state/secret 外层格式不凭空升级或重建。旧 revision 字节、内容哈希、秘密引用、profile 完整往返、旧编译配置与 policy identity 原样验证。只在明确保存新草稿时产生新 revision；读取旧数据不写回。

## 4. 来源路由、DNS 和防回环

受保护流量按 inbound tag 处理；服务器本机入口 UDP 总拒绝。设备 UDP 必须有独立的显式准入分支，任何未匹配项最终拒绝，不能流到普通 DIRECT 规则。TCP 分支保持已有路径。

固定 Mihomo 的 UDP adapter 会先解析 SOCKS 域名再转发，因此首批设备路径只以字面 IPv4 目标申请资格；不能把 raw XUDP 域名测试当成 Mihomo 设备 DNS 全路径通过，也不能用测试机环境 DNS 补齐证据。国内入口收到域名的恶意/兼容性边界由经过认证的原始 XUDP 测试单独验证。后续设备导出若要包含域名/DNS 行为，必须独立冻结并实测其实际解析位置，不能假设和原 TCP 路径一致。

上游地址经现有有界 bootstrap 校验全部返回地址并固定实际 IP；业务 DNS 仍通过同一上游的明确 DoH，不使用本机或 direct DNS 兜底。UDP 域名目标先在国内核心受控解析，再对完整成功答案执行私网、自身、上游、metadata、特殊网段和 IPv6 检查。一个禁止答案必须拒绝整组；解析失败或只返回未获资格的地址不能降级直连。通过的具体地址须被同一拨号使用，海外不能重新解析原域名绕过检查。

IPv4-only 不能只靠 `ip_version: 6` 匹配，因为该匹配不等于检查已经解析的所有答案；必须检查实际 DestinationAddresses 中的 IPv6 地址。固定 `prefer_ipv4` 会合并成功的 A/AAAA 地址，任一族成功可返回有效结果；失败族不会成为 direct/local fallback。全部成功返回地址都检查，包含任一 IPv6 则拒绝 UDP。因此不能承诺“某族查询失败必定整体失败”，也不能为了方便悄悄只查询 A 并忽略其余已取得答案。D2 暂限目标 UDP 高端口 1024–65535，不授予 UDP/53 DNS 或 UDP/443 QUIC 资格；低端口均明确拒绝。UDP 动态禁止集合覆盖全部已校验上游地址，不仅实际 pinned 地址；旧 TCP/v1 规则原样保持。固定协议解码会把 IPv4-mapped 表示归一化成 IPv4，因此以归一化后的实际地址执行相同规则：私网、回环、自身、上游和 metadata 不得借表示绕过；不能宣称一律拒绝所有公网 mapped 字面表示。

## 5. 同关联每包目标绑定是硬门槛

固定 sing-vmess XUDP 支持在同一关联的后续帧声明新目标，而 sing-box 路由通常只在建立 packet connection 时运行。首次地址通过不能证明以后每个数据包安全。

候选安全机制是固定核心已有的路由 `udp_connect: true`，将实际写入绑定到首次已校验的具体 IPv4 地址/端口，并设置明确路由级 `udp_timeout: 30s`；VLESS TCP listener 的同名字段不替代路由超时。上游网络用明确列表 `[tcp, udp]`，而不是无效字符串 `tcp,udp`。必须实际证明先成功一次，再在同关联将目标改为禁止地址、当前服务器、上游、其他端口、域名及重绑定目标都无法逃离初始绑定。测试只用受控 loopback 陷阱，不接触真实 metadata。

绑定语义可能使改目标帧仍送到原目标；必须记录原目标及陷阱的完整账本，不能把“未按恶意目标发送”谎称为“该帧丢弃”。同时必须检验返回数据报的 IP/端口是否仍为原应用目标。若绑定造成错误回复地址/端口且固定核心内无法安全表达，本阶段保持关闭并报告限制，不以 payload 相同代替可用性。

## 6. 资源、生命周期与事务

UDP 的明确空闲超时、关闭后的连接/FD 回收、少量并发与连续关联的资源上界由固定核心可表达字段和实际观测共同验证。不得把测试中的会话数量上限写成产品已有的硬配额；若核心不能强制某项配额，文档明确边界，不新增系统权限。

启用、关闭、换上游及回滚沿用检查后提交、持久证据和核心进程代次管理。切换完成后旧 UDP 关联不得跨代继续活动；证明旧进程已结束、监听所有权和资源释放。失败恢复旧策略或保持停止，不在未知状态套默认 off/direct。

旧备份恢复必须保持原两个开关及 secret 一致。普通 restore 不能把 active UDP-on 静默替换为 TCP-only 备份，即使 relay 仍为 true；须先明确关闭并提交，或使用既有绑定完整内容的离线恢复。离线恢复预览、计划哈希和最终状态包括安全的 draft/active UDP mode，不输出秘密。当前 D1 能拒绝未知 v2 状态，但公开 v0.4.8 早于整个代理状态层，不能承诺手工退回任意旧版本可保持保护；携带代理状态退回该旧版不受支持。受支持升级/回滚工具还须检查候选包状态兼容性，不能靠空数据健康检查授予旧包资格。

## 7. 面板、API 与设备配置

“代理与中转”页先展示设备 UDP 当前草稿和活动模式，只有受限能力被明确选择才进入新模式。保存、检查、应用和真实连通诊断分开，不把 check 成功标成真实 UDP 可用。切换模式使旧预览/选中项/检查 token 失效，取消和刷新不得隐式开启。

完成资格后提供独立明确的受限设备客户端导出，只包含国内入口凭据与新模式范围；普通分享/订阅、旧严格 TCP 导出和 D1 上游专用导出均保持。导出通过现有管理员鉴权、Origin、revision、无缓存和秘密清理规则；新路径不自动泄露海外上游凭据。具体端点/文件格式在可行性通过后随实现冻结。

## 8. 必需测试矩阵

- v1/v2 模型、未知字段/类型、URI/YAML 精确往返、默认拒绝、新模式缺字段/REALITY/传输/JSON 拒绝。
- 旧 store 字节/哈希/秘密/备份/恢复/编译身份不变；v2 保存、旧 UI 降级拒绝、双向不安全恢复、历史损坏不重建。
- 双主开关四组合与模式开关、全部入口资格、未完成应用/失败回滚/重启恢复/停止意图、跨代旧关联关闭。
- 固定真实 UDP 双跳成功及 UUID/CA/SNI/停止负向；载荷、来源 IP/端口、返回和全部生命周期账本。
- 公网对照、私网/自身/上游/metadata 模拟、A/AAAA 混合/失败族、同关联改目标/域名重绑定、禁止端口/IPv6、DoH 失败无兜底。
- 显式模式真实 API 及浏览器：导入、选择、保存、检查、应用、导出/复制/下载、取消/刷新/并发/过期会话；旧 TCP-only 流程完整回归。
- 默认环境真实门槛明确 skip；CI 开关打开后缺依赖或启动失败必须 fail。最终候选八工作流、准确 master 和同提交四目标资产单独验收。

## 9. 发布与证据

正常提交、Draft PR、准确候选验收后正常合并和准确 master 验收属于本次开发范围。所有原失败及修复因果保留，不能用旧绿或盲目重跑替代结论。本阶段不创建 tag/Release，不部署真实服务器，不改变用户电脑网络、安全设置或凭据。正式版仍为既有 v0.4.8 发布；新增源码能力与已发布版本必须区分。

## 10. 固定源码与前置执行入口

- [sing-box 1.14.2 依赖](https://github.com/SagerNet/sing-box/blob/v1.14.2/go.mod)固定 sing-vmess 0.2.8 与 sing `87c33f17688f`，不使用最新文档推断。
- [packet connection 路由](https://github.com/SagerNet/sing-box/blob/v1.14.2/route/conn.go)、[VLESS outbound](https://github.com/SagerNet/sing-box/blob/v1.14.2/protocol/vless/outbound.go)说明 UDP connect / timeout / 编码的实际路径。
- [XUDP](https://github.com/SagerNet/sing-vmess/blob/v0.2.8/xudp.go)、[mux 状态帧](https://github.com/SagerNet/sing-vmess/blob/v0.2.8/mux.go)说明同关联变目标；[绑定 wrapper](https://github.com/SagerNet/sing/blob/87c33f17688f/common/bufio/bind.go)、[NAT wrapper](https://github.com/SagerNet/sing/blob/87c33f17688f/common/bufio/nat.go)说明实际每包写入与回复 tuple 的待验边界。
- [固定 Mihomo VLESS adapter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/vless.go)为客户端对照。共享启动门槛必须等待当前进程、当前代的 Running 标记。

前置入口为 `tests/test_restricted_udp_preflight.py`：设置 `VUI_RESTRICTED_UDP_PREFLIGHT=1`、`VUI_RESTRICTED_UDP_PARSER=1` 及既有 `VUI_TEST_CORES`、`VUI_TEST_MIHOMO` 后运行对应 unittest。`VUI_RESTRICTED_UDP_EVIDENCE` 只保存假材料且脱敏后的核心日志与目标账本；不向工件上传临时 CA、私钥或完整配置。独立 CI 步骤在原真实链路前运行，失败保留，后续原链路未运行不能记通过。

初始 17 方法中：15 个真实门槛，1 个原始帧 fixture 自测，1 个固定 parser 门槛。初始本地 parser/wire 通过，15 个真实方法在核心启动处受 netlink EPERM 阻断；当前未宣称 UDP 成功。共用 readiness inventory 增加该工厂并验证 Mihomo/非 Mihomo 两条选择，不让新别名绕过既有审计。

# 标准客户端、两跳与实验边界

2026-10-18 更新。产品[每入口 TCP](PER_INBOUND_ROUTING_CONTRACT.md)已完成；独立标准客户端 UDP 研究已有新的实际结果，完整容量与同关联 idle 门槛仍红。准确源码、逐组结果、归档与剩余计划见[本轮研究记录](STANDARD_CLIENT_UDP_RESULTS_20261018.md)，不授手机真机资格。

## 已有事实

标准客户端只需连接一个入口。手机→国内 inbound 与国内 outbound→海外是两段独立的认证、TLS 和协议会话；服务器路由决定第二跳，手机不需要知道海外凭据。直连/中转可使用相同标准 VLESS 协议，以不同入口 ID/端口/UUID 区分。

准确实验 `afbdba100c5f0e3dc6888684fdbab31980720df3` 的原 runtime 有直接反例，证明“三角色必须全部自编”不能推广成产品前提：

- 官方 Mihomo 1.19.32 作 device，官方 sing-box 1.14.2 作 overseas，仅国内为候选。
- `test_03_guarded_mihomo_exact_payload_address_and_port` 的 1/1200 字节 UDP 完整 payload/address/port 往返；`test_tcp_https_mihomo_double_hop` 的验证 TLS HTTPS 200、精确正文与海外目标来源通过。
- 第一跳与上游 UUID/CA/SNI/停机负向和恢复分别留有真实证据。
- device SHA-256 `3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8`；overseas SHA-256 `07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793`。
- [准确 run](https://github.com/ForceMind/V-UI/actions/runs/39146603341) 原 runtime artifact `12143836674`，包含两 arm 的 `process-binaries.json` 与上述 `cases/`。这不是全矩阵成功：原矩阵各19/20及其他大包失败保留。

旧 `test_14_guarded_udp_idle_drains_and_new_association_recovers` 和 `lifecycle_idle_mihomo` 的 idle 主体是 raw XUDP probe，恢复时**新建** Mihomo SOCKS 关联。不能称已验证同一 Mihomo 应用关联跨 idle，更不能称手机 VPN/TUN 已验收。

## 固定实验 oracle 的范围

原 A/U/V 大包组 device 是 Linux sing-box mixed/SOCKS；它们的16 KiB相关截断与 End 后原 SOCKS control/relay 被关闭，不能概括成所有 App。B 组独立证明固定海外 sing-box 接收回包也有截断。owner-on 三角色成功是特定组合资格，不证明每角色最小补丁集合，更不要求分发自定义手机 App。

固定 Mihomo 的 SOCKS control TCP 与 NAT sender 独立，End/EOF 后 NAT 项删除，新应用报文有机会新建上游；这是源码推断，删除窗口/首包恢复待实测。其标准 SOCKS UDP buffer 同样约16 KiB、POSIX读路径忽略截断flags，因此不能先声称换 Mihomo 就解决所有大包。手机 TUN/VPN 是另一入口。固定来源：[标准池容量](https://github.com/MetaCubeX/mihomo/blob/88dcbf7f1614a67c3b36b848ee3592dfa92ada36/common/pool/buffer_standard.go)、[POSIX datagram 读路径](https://github.com/MetaCubeX/mihomo/blob/88dcbf7f1614a67c3b36b848ee3592dfa92ada36/common/net/packet/packet_posix.go)、[SOCKS control](https://github.com/MetaCubeX/mihomo/blob/88dcbf7f1614a67c3b36b848ee3592dfa92ada36/listener/socks/tcp.go)、[NAT sender 清理](https://github.com/MetaCubeX/mihomo/blob/88dcbf7f1614a67c3b36b848ee3592dfa92ada36/tunnel/connection.go)。

## 配置替代路径：仅源码结论

| 角色/路径 | 已核事实 | 尚不能声称 |
| --- | --- | --- |
| sing-box VLESS | `packet_encoding` 缺省/null 是 XUDP，显式空串才 native UDP | 删除字段即可避开 mux |
| Mihomo 1.19.32 VLESS | 非 packetaddr 配置强制 XUDP，`xudp:false`/空字段无效 | 任意标准 YAML 可选 native |
| Xray 26.3.27 | 默认 cone 可把非53/443的 UDP 转 XUDP；仅关 multiplex 不够 | 普通手机 App JSON 全端口 native 可用 |
| 国内 native VLESS + udp_connect | native PacketConn 同样继承外层 RemoteAddr | native 自动修复原代理端口回映问题 |
| 国内 native + 不开 udp_connect | 源码返回初始逻辑目标，值得独立测试 | DNS/改目标/拒绝/生命周期已安全 |
| packetaddr | 编码/路由metadata不同，入站清空初始目标 | 无损继承原初始目标守卫 |

固定来源：[sing-box VLESS outbound](https://github.com/SagerNet/sing-box/blob/af6e64c3b69e6132ebaee0e1a3d24e93903f6709/protocol/vless/outbound.go)、[sing-vmess native VLESS](https://github.com/SagerNet/sing-vmess/blob/31ec11e8790c48a4bf11711806a61e929ee6b89d/vless/client.go)、[Mihomo VLESS](https://github.com/MetaCubeX/mihomo/blob/88dcbf7f1614a67c3b36b848ee3592dfa92ada36/adapter/outbound/vless.go)、[Xray VLESS](https://github.com/XTLS/Xray-core/blob/d2758a023cd7f4174a5a5fa4ff66e487d4342ba0/proxy/vless/outbound/outbound.go)。

关闭 sing-mux 不会禁止 VLESS 自身 CommandMux；普通客户端选 native 并不证明认证用户无法进入服务端 XUDP路径。不得用配置绕过替代服务端安全验收。

## 本轮固定 UDP 方案与未过门槛

优先只替换原 sing device 为 stock Mihomo，保留两跳 VLESS/TCP/验证 TLS/XUDP，避免同时改多个协议。独立新计划、角色与 leaf 集合，不改原 sing 矩阵或失败判定：

1. 服务端候选组：复用原 C 的 VLESS 子集 26 case/66 leaf，与 D 的 2 case/4 leaf；所有角色 owner 关闭。保留原零包/大包/超大本地拒绝、精确地址与 payload、短包恢复、资源清理门槛。
2. 官方海外诊断组：原 B 的 VLESS 下行 1 case/4 leaf，8192/32768/60000与短包恢复。按实测分类，不预设必须失败。
3. 同应用关联 idle：保留原 SOCKS control、UDP source socket与relay tuple，60000→完整空闲释放窗→新的32768。允许 Mihomo内部新 XUDP session，但必须证明没有替换应用关联、没有重放/保活，完整 target ledger 与最终进程/线程/FD清理。
4. 优先用实际普通 VLESS 导出产生设备配置并核只有入口凭据。严格中转 exporter 当前仍TCP-only；若该组只能使用研究 fixture，应如实标注，不能称产品 UDP 导出已支持。
5. 当前产品每入口TCP独立完成。后续 UDP 出现具体固定客户端入口限制时，按版本/入口/方向记录实际能力与拒绝；不得泛化所有手机，亦不得将原完整门槛缩小后改称全通过。

手机端实际导入是否保真、具体 App/平台/TUN、UDP/53/443、域名全答案与重绑定、长时间/移动网络恢复均未在本轮获得新证据。native/packetaddr是后续选项，不与上述最小归因组混跑。

上述 C/D/B 已在 `experiment/standard-client-udp-20261018` 准确 `8089b3f` 实跑：全 stock C 的回复端口错误，候选两服务器 + stock Mihomo 的 0/1/8192 与短包恢复可完整往返；但大上行到目标仅 16374 字节前缀，大下行无应用回复，D 的 60000 前置失败使 idle/close 窗与恢复未执行。只把海外换回官方的 B 大下行是 16114 前缀，8192 与 7 完整。产品 relay UDP 拒绝控制仍通过。原始完整结果和后续归档修复重验分别记录，不混合不同提交资格。

产品最低已验能力仍是普通 VLESS 节点的服务器侧 TCP direct/relay 与既有普通 direct 能力。研究候选的有限小包成功尚未开放产品中转 UDP。下一阶段应先明确实际现成 App/平台、导入入口和 MTU 内负载需求，再做独立 TUN/网络路径与服务端最小改动对照；不能因 60000/65497 极大包失败就要求用户更换手机核心或自研 App，也不能用普通小包结果替代严格完整容量和 2s idle 门槛。

## 后续有限边界与普通包诊断

[准确诊断记录](STANDARD_CLIENT_UDP_DIAGNOSTIC_RESULTS_20261018.md)已将四个源码临界点独立实跑：候选两服务器下，上行 16374 完整、16375 到目标为 16374 精确前缀；下行 16384 完整、16385 由目标完整发送后应用等待四秒无回复。每点均绑定一次发送、原应用 I/O、目标 ledger 和进程来源。这支持固定 mixed/SOCKS 路径的容量边界，不给手机 TUN 设同样上限。

另列 1200 字节 first → 完整六秒静默观察 → 不同 hash 的单次新包，保持同一应用 control/UDP/relay，释放与 next-packet 可用性分别判定。全 stock 首包 tuple 错使窗口/恢复未执行；候选首轮 raw 两包完整但 verifier 模块缓存错误且缺 post-identity，保留红灯；两 case 有限夹具修复单独归档。详见准确结果，不与原 D 的 60000 前置或已有认证资格混用。

准确 a37 的有限修复补测中，candidate 国内 + candidate 海外、candidate 国内 + 官方海外两组均完成两个不同 1200 字节完整往返，原应用关联不变，末首包回复后整六秒观察窗中 server leg 约 2.05 秒释放，下一包只发一次。这说明本固定小包条件不必替换海外核心；八补丁最小子集、真实 App/TUN/MTU、长期/并发/多次恢复仍未验证。产品 UDP 拒绝保持，完整容量和原 D 仍红。

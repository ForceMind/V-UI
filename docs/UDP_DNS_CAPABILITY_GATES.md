# UDP / DNS 能力门槛与上游导入保真

日期：2026-10-16。阶段：D0，独立于已完成的双开关 M0–M4。本文件冻结下一阶段的最小范围；新增测试在准确候选及合并主线完成前均不预称通过。

## 1. 已验基线与目标

起点为 `9a97ad13aae2fabb5ed969fdc0f930c36fdc55b7`，tree `841ef19bf2ebc1e85f32758a3773b22c9b1f7c05`；[PR #44](https://github.com/ForceMind/V-UI/pull/44)记录 M0–M4 收口：准确主线八工作流 / 11 jobs / 122 steps 全部 attempt 1 成功，完整套件 1,775 项（1,599 pass / 176 明确 skip），独立真实核心/浏览器门槛与四目标 19 项资产另行验收。该基线成功不替代本次改动的验收。

本阶段交付三个可执行结果：

1. 上游导入对 UDP / packet encoding 请求给出明确的能力拒绝原因，保留原拒绝行为。
2. 以产品真实导出函数产生的配置验证导入保真与拒绝，不靠手写近似文件宣称往返兼容。
3. 固定核心的有界 UDP / DNS 实验与失败证据。先深化已有普通 Shadowsocks 应用 UDP，并证明 DNS 格式载荷不能绕过双开关的 UDP 拒绝。

固定 sing-box **1.14.2**、Mihomo **1.19.32**、Xray **26.3.27**，不换 pin、不重编译内核、不新增依赖。不改 `VERSION`、默认双关、普通节点/导出、双开关路由、DNS 目录、数据 schema、应用事务或秘密存储。

## 2. 四类能力不能互相代替

| 路径 | 当前事实 | 本阶段不作的推论 |
| --- | --- | --- |
| 普通 Shadowsocks AEAD | 既有三 cipher × 双客户端 TCP/UDP echo 证据 | 不因此证明所有 UDP 长度、分片、MTU、丢包、吞吐或长期稳定性 |
| 普通 VLESS/TCP/TLS、VMess、Trojan 导出 | 部分格式声明 `udp: true` 或 XUDP；配置能力与真实应用 UDP 资格不同 | 不擅自关闭普通节点既有配置，也不将配置字段解释成全面支持 |
| 双开关本机/设备中转 | 所选入口应用 UDP 明确 reject，业务 DoH 经同一上游 | 不因上游内核有 UDP 实现就放开产品路径 |
| 严格导出的独立 DNS listener | 可收 UDP/TCP DNS，经已验证的 DoH/TCP 路径；mixed 入口仍拒绝应用 UDP | 不称 DNS 查询等于任意 UDP 经代理，也不称设备全机 DNS 接管 |

QUIC 传输 UDP、SOCKS5 UDP ASSOCIATE 建立、配置 parser 成功、TCP listener 就绪均不是应用 UDP 转发证据。URI 只是节点资料，不能保证使用者完整客户端的 UDP、DNS 或 DIRECT 策略。

## 3. 输入格式与真实导出差距

双开关仍只转换一期 VLESS/TCP/TLS 与 REALITY-Vision 上游。URI、V2Ray/Xray JSON、Clash/Mihomo YAML 是三种输入容器，不等于所有协议或 sing-box JSON 均可导入。完整多节点配置仍须明确选择，provider/脚本不读取或执行，已识别但未导入区块逐项确认。

当前普通 VLESS/TCP/TLS 的 URI 导出包含 `packetEncoding=xudp`；Mihomo 导出包含 `udp: true` 与 `packet-encoding: xudp`。这些参数在双开关一期合同中没有获得资格，因此原先即拒绝。它们不能被自动删掉，再当作 TCP-only 节点保存。符合一期字段的 REALITY-Vision 导出可无损转换；普通节点仍按自己的原合同工作。

本阶段新增原因码 `UDP_CAPABILITY_NOT_QUALIFIED`，用于 URI 的 `packetEncoding` 字段、YAML 的 `packet-encoding` 字段（即使值为空或看似关闭也不接受），以及严格布尔 `udp: true`。消息是固定文本，不插值输入内容或秘密。新增识别仅解释拒绝，不把这些字段加入支持的规范化 profile。

- 输入编码、资源限制、语法、重复键/参数、协议等原检查保持；混有其他未知字段仍返回 `UNKNOWN_FIELD`，不能借新原因码遮盖其他不支持语义。
- YAML `udp` 的 `null`、数字、字符串不强转，仍走原不支持选项拒绝；`skip-cert-verify` / TLS 校验不弱化。
- 预览中不支持候选不可选；保存端重新解析，不能绕过 UI 保存。取消、刷新或失败后不产生新草稿、活动状态或秘密。
- 中文面板说明普通导出与中转上游支持范围不同，不引导用户删掉原配置中的关键参数来绕过拒绝。

## 4. 本阶段真实门槛

所有目标、账号、载荷及 CA 为隔离测试材料，只在临时目录和 loopback 进程使用；不改主机网络、信任库、防火墙或用户配置。正负例复用同一类真实可达目标，保存唯一请求标记及全生命周期账本。

| 门槛 | 计划场景 | 必须取得的证据 |
| --- | --- | --- |
| D01 二进制应用载荷 | 三 AEAD × 两客户端 × 1 / 1,200 bytes，12 场景 | 准确发送/目标收到/返回字节一致，SOCKS 源地址与目标对应，不仅“收到任意数据” |
| D02 DNS wire 载荷 | 三 AEAD × 两客户端 × A / AAAA / TXT，18 场景 | 实际 UDP 目标收包；transaction ID、question、响应内容匹配；端点均为字面 IP，Mihomo resolver 关闭，sing-box 不配置业务 DNS，这里不声称测量了客户端 resolver 查询 |
| D03 错误密码 | 三 AEAD × 两客户端，6 场景 | 该次真实服务端拒绝原因、无 DIRECT、全观察窗口及清理 drain 后目标零送达 |
| D04 错误 cipher | 三 AEAD × 两客户端，6 场景 | 不匹配确实经过固定服务端；新鲜解密/认证原因及零送达，超时不是原因 |
| D05 停止与恢复 | 三 AEAD × 两客户端，6 场景 | 目标直连可达对照；上游停止后新的唯一数据不送达；恢复的新请求可成功，不重新设置基线抹去迟到包 |
| D06 本机开/中转关 | 鉴权本机入口 × 普通二进制/DNS wire，2 场景 | 新鲜 UDP reject、目标零送达，不能借 DNS 格式绕过拒绝 |
| D07 本机关/中转开 | 两客户端 × 普通二进制/DNS wire，4 场景 | 国内入口到上游的应用 UDP 不可达，保持普通 DNS 零兜底与最终 drain |

预计 7 个真实测试方法、54 个独立场景；以最终代码实际发现数和 CI 输出为准。helper 单元、parser、真实核心、真实浏览器分别报告，不合并成“全部真实测试”。错误核心缺少预期原因时保留失败并核固定源码，不靠放宽为超时或重试到偶然成功。

此实验的 UDP 目标使用非特权动态高端口。它不验证 UDP 53/443 的特殊处理、Vision UDP、UoT/mux、任意协议中转、多用户、IP 分片、IPv6 网络可达性或实际公网质量。既有其他协议/严格导出/DNS负向仍完整回归。

## 5. 证据、回滚和下一阶段

- 默认测试明确 skip 真实核心/浏览器；对应 CI 开关打开后缺依赖、运行权限或启动失败必须 fail，不能转 skip。已有本地 netlink/socket EPERM 是环境失败，不经提权或改安全设置绕过。
- 保留普通八组工作流；新增独立真实测试接入既有 loopback 任务。新增 UI 流程使用真实管理员登录、隔离 parser/store，验证拒绝、重复激活、取消、刷新与保存状态不变。
- 每次最终候选需独立审查、完整本地测试、准确 head 八组终态；正常合并后核准确 master，不能借旧绿灯。四目标包来源/内容与功能测试分别登记。
- 这次仅改变不支持输入的拒绝分类和面板说明，不迁移用户数据或修改活动配置；回滚代码不会重建状态或启用 UDP。不得删除历史产物掩盖失败。
- 后续最小新能力必须依据本阶段事实单独冻结：例如独立 DNS 诊断证据，或一个协议/编码的上游转换与运行门槛。仍需完整的入站→上游→目标、DNS/禁止地址/失败关闭、四状态/事务/导出/恢复验收，不能直接从普通 Shadowsocks 结果开放双跳 UDP。
- 不含新 Release/tag、生产部署、TUN/全机路由、DNS-01/通配符或核心自动升级；用户真实外部设备/海外线路与真实小 VPS/24 小时另行验收。

## 6. 固定源码依据

- [sing-box 1.14.2 Shadowsocks outbound](https://github.com/SagerNet/sing-box/blob/v1.14.2/protocol/shadowsocks/outbound.go)：native UDP 与 UoT 是不同路径。
- [Mihomo 1.19.32 Shadowsocks adapter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/shadowsocks.go)：UDP 选项不等于一次实际载荷成功。
- [sing-box 1.14.2 VLESS outbound](https://github.com/SagerNet/sing-box/blob/v1.14.2/protocol/vless/outbound.go)：省略 packet encoding 可默认 XUDP，删除字段不能证明禁用 UDP。
- [Mihomo 1.19.32 TUIC adapter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/tuic.go)：其 UDP 能力硬编码，不能依靠无效的 `udp: false` 宣称已禁用。
- [Mihomo 1.19.32 DNS dialer](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/tunnel/dns_dialer.go)：命名 adapter 绑定及失败行为应按实际固定实现验证。
- [sing-box 1.14.2 DNS handler](https://github.com/SagerNet/sing-box/blob/v1.14.2/protocol/dns/handle.go)：异步 UDP DNS 错误不保证普通日志含 TLS 原因，既有测试专用取证边界保持。
- [Xray 26.3.27 VLESS outbound](https://github.com/XTLS/Xray-core/blob/v26.3.27/proxy/vless/outbound/outbound.go)：Vision UDP/443 与 UDP/53 编码分支有特殊语义，不从高端口实验外推。

## 7. 本阶段结果

候选冻结前的本地结果：导入组合 74 项通过；API 14 项通过；新增 UDP fixture 8 项及固定 parser 1 个方法 / 6 组合通过。真实核心 7 个方法 / 54 场景仍待 CI。默认浏览器集合 16 项中 7 项通过、9 项显式跳过，不能算真实浏览器通过。

保留的失败与修复：初始真实导出导入用例 21 个失败子例、旧原因码预期 1 项失败、嵌套未知字段被专码遮盖 32 个失败子例，均保留原输出并修复回归。独审还实际复现旧通用 helper 可把旧 partial 日志补全误认为新请求拒绝；新增 D06/D07 改用每个数据报独立核心/日志、关联成功后取 offset、单次真实发送记录、严格完整新日志及完整观察窗口，8 个 helper 自测和独审通过。未修改旧 helper 或抹去失败证据。

本地真实核心因 netlink EPERM 启动失败，实际 Chromium 因 socket EPERM 在 setUp 失败；没有为其提权或将失败改为跳过。它们不能替代准确候选 CI 的真实验收。准确提交、最终完整套件、真实成功/失败/未运行、独立审查和 CI 链接由本阶段 PR 追加；本文件不填写未来 SHA 或预先授予 UDP 资格。M0–M4 旧失败与最终验收保留于[进度账本](DUAL_PROXY_RELAY_PROGRESS.md)及 PR #44。

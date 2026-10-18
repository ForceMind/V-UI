# 标准现成客户端 UDP 两跳研究（2026-10-18）

## 结论与身份

最终准确运行源码为 `fa96996b8fa1f92819c4fb0fd97ce047e8a8521d` / tree `569cbfbcb222426ff03747440c6469aa072fefb7`，第二轮 run39169374994 attempt1 已完成。下文按次序保留首轮8089、归档修复及第二轮完整结果。

本轮在独立分支 `experiment/standard-client-udp-20261018` 收集实际证据；完整 UDP 容量与同应用关联 idle 门槛仍未取得资格。产品 relay UDP 继续拒绝，普通直连既有能力和普通标准节点字段保持。无需自研手机 App，但本轮 Linux SOCKS 结果不能代替手机导入、TUN/VPN 或公网资格。

- 产品基线：`1489bf5218fced9b42c57e06715478dc323dc782`。
- 首轮研究源码：`8089b3f94e5fa599943bd9d9456b0c92cf28ddfe`；tree `9f33a00bc9aa680d545992555db17ff7cf895f8b`。
- [首轮 run 39167829699，attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39167829699)。
- 冻结框架及原八补丁候选：`afbdba100c5f0e3dc6888684fdbab31980720df3`；tree `996d37d925be8edd18cbf921d3cc580f43c0730d`。
- Stock Mihomo 1.19.32：SHA256 `3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8`。
- 官方 sing-box 1.14.2：SHA256 `07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793`。
- 所有新运行角色均要求 owner 选项缺省，不能通过置 true 修复此组。完整源码前后身份、进程角色、配置摘要与二进制摘要分别核验。
- 29 项纯计划/验证器/归档回归控制通过；准确 `3941f6c9c3ba319944f1c00da81fd2c9b17e3ca4` 独立复跑 29/29、0 skip。8089 仅添加已审的分支限定 push 触发。这些控制不授 UDP 转发资格。

## 已执行的独立分组

产品控制使用实际 HTTP API 的普通 VLESS 导出，并执行原 P4 的 7 项控制与 3 项真实方法，共 10/10、0 skip，58.261 秒。普通 direct 小 UDP 成功，relay UDP 有实际拒绝。TCP 的四状态、12 条转换、实际目标来源和最终清理保持。其普通导出、严格中转 TCP-only 导出、研究用服务端配置是三种不同用途。

研究 C/D/B 从当前生产 exporter 函数生成普通节点，原样放入仅供测试的 Mihomo listener/selector shell；重新生成原始节点摘要、脱敏结构和实际设备配置摘要独立绑定。该函数证据不冒充实际 HTTP API 资格，后者由产品控制单列承担。

### 全 stock C/D

- C 的固定计划为 26 case / 66 leaf；26 case 全启动，27 leaf 实际启动、39 leaf 因原 fail-fast 未执行。26 个业务 leaf 失败，1 个本地 EMSGSIZE 通过。没有完整通过的 C case。
- 21 个收到并解码的回复都保留目标 host，但 port 错误；实际 port 在每个 case 中均等于海外 VLESS listener port。18 个回复 payload 完整仍因 tuple 错失败；另外 3 个下行 32768/60000/65497 都只得到 16114 字节精确前缀，兼有端口错误。
- 其余 5 个 C 业务 leaf 在完整 4 秒窗内目标零交付：首次 uplink 0/32768/60000/65497，以及 full-short-empty 的 65497 前置。无 raw reply 的超时不称显式拒绝。
- 本地 65498 payload + 10 SOCKS bytes = 65508，单次发送实际 errno 90，目标零交付；后续 7 字节恢复收到错误端口，完整 case 仍失败。
- D 的 2 case / 4 leaf：两个 60000 前置都在完整 4 秒内目标零交付。仅 2 leaf 启动，2 个恢复 leaf 未执行，close/idle 窗口均未执行，不能由最终进程清理推断 idle 通过。
- 全部 28 case 的应用 control/UDP 双 FD 闭合、目标线程/套接字释放、84 个核心角色单次启动与结束、owner 缺省及 owned process group 消失独立核验。容量红与资源安全分别成立。

### 全 stock 新认证负控与 TCP

重新启动原 test05/test07/TCP 的 3 个方法、8 个 fixture。TCP HTTPS sanity 通过：TLS 验证、HTTP 200、固定正文摘要及唯一海外来源均由 raw 观察独立复核。

7 个 UUID/CA/SNI/停上游场景全部在故障注入前的 UDP positive 因错误回复端口失败；observations/rejections 未进入故障阶段。实际故障注入、拒绝原因、零误送达与恢复断言都未执行，不能继承旧 afb 负控通过资格。整体 auth 为 1/3 方法通过，继续保持红色。

### Stock Mihomo + 两个候选服务器，owner 关闭

- 同一冻结八补丁在本轮 runner 重新构建；实际 binary SHA256 为 `1e369833a0863889d8738922461229daa4bf3819cbecf9060bfefb045bb303c1`，并非复用旧 `2080db…` 二进制字节。完整旧 builder 验证、补丁顺序、Go 1.25.14/编译参数/module graph、base/final 源码清单与原回归材料随归档绑定。
- C 仍是 26 case / 66 leaf：51 leaf 实际启动，37 次完整请求/回复、1 次本地 EMSGSIZE 通过，13 个业务 leaf 失败，15 leaf 未执行；13 case 完整通过，13 case 失败。
- 0/1/8192 字节首包与 warmed 双向场景，以及随后 7 字节恢复通过；本地超限之后 7 字节恢复也通过。每次成功都核原始地址/端口、完整 payload、目标来源、同应用与 relay tuple、一次发送。
- 7 个 C 大上行 leaf（首包/预热后的 32768、60000、65497 及 full-short-empty 的首个 65497）只到达 16374 字节精确前缀，原目标完整性断言立即失败，不能称零送达或等待 4 秒超时。
- 6 个大下行 leaf 的 32 字节探测请求完整到达，目标实际完整发出 32768/60000/65497，但原应用在完整 4 秒窗内未收到回复。没有应用 raw reply，不能称显式拒绝或完整交付。
- D 两个 60000 前置也只到达 16374 字节精确前缀；2 leaf 启动、2 个恢复未执行，两个生命周期窗口均未执行。没有取得 2s idle 释放或同关联恢复资格。
- 本轮 C/D 资源清理及所有角色 owner 缺省独立核验通过，容量保持红。全 stock → candidate 同时改变两个服务器，不能据此推出每角色必需补丁集合，也不能把此 Linux Mihomo SOCKS 入口结果概括为手机/TUN 限制。

### B：只把海外换回官方

设备与国内候选身份保持，只将海外换为固定官方 sing-box。1 case / 4 leaf 全实际执行：8192 与最后 7 字节完整；32768、60000 都收到 16114 字节精确前缀，目标 host/port 正确。

四叶保持同一应用关联 id、control/UDP 对象、relay 和海外 source tuple，逐叶一次 send/recv、无重放，所有进程/FD/目标/线程清理通过。B 容量仍失败；这是本轮重新取得的证据，不继承旧 B。此对照隔离海外角色变化，不证明其他角色的大包入口已解决。

### 首轮 candidate-auth 归档阻断与最小修复

[candidate job](https://github.com/ForceMind/V-UI/actions/runs/39167829699/job/117794411243) 在 21:38:21 UTC 记录认证子进程退出 0、无超时/信号、owned group 已消失；21:38:22 的严格归档阶段报 `Secret-bearing text in evidence`，没有上传 candidate-auth 原始归档。因此本轮不授该组认证/恢复资格。

日志没有指出失败成员。源码与合成控制复现表明：旧敏感赋值 guard 也会拒绝已脱敏的 `unknown uuid: <fixture>`，这是可复现的根因推断，不能写成已读到失败字段。完整 CI 日志 132066 UTF-8 bytes，SHA256 `6142f2be922a8c7139733acfdc137a2f1f285941c17c882c3bf0f05ccaa4f558`。

后续准确源码 `fa96996b8fa1f92819c4fb0fd97ce047e8a8521d` / tree `569cbfbcb222426ff03747440c6469aa072fefb7` 只改 3 个研究文件：对完整已脱敏的固定 UUID 拒绝词规范化；保留 raw 日志大小写，按冻结 `fresh_lines()` 的小写、完整换行记录视图绑定拒绝行；排除 checkpoint 前的残行及末尾未完成行。旧敏感 guard 未改，未知 uuid/token/password 与已知假 UUID 后拼未知后缀仍拒绝。准确同 tree 独审 31/31、0 skip、9.856 秒；[新 run 39169374994 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39169374994)重新执行所有分组，结果单列，不能回写首轮失败。

## 修复后准确 fa96996b 的完整重验

[run 39169374994 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39169374994) 已终态 failure：产品 job success，stock 与 candidate job 因严格容量门槛 failure。六个允许列表内文本归档均成功保存；未再通过重跑消除容量红灯。

| 分组 | 本提交实际结果 | 未执行/资格边界 |
| --- | --- | --- |
| 产品 P4 | 10/10、0 skip，55.694 秒 | relay UDP 实际拒绝；不开放研究配置 |
| stock C | 26 case，27/66 leaf 启动；26 业务失败 + 1 本地 EMSGSIZE | 39 leaf 未执行；21 错端口回复中 3 个另为 16114 前缀 |
| stock D | 2 case，两个 60000 前置目标零交付 | 2 恢复 leaf、两个窗口未执行 |
| candidate C | 26 case，51/66 leaf 启动；37 完整 + 1 本地 EMSGSIZE + 13 失败 | 15 leaf 未执行；7 上行 16374 前缀、6 下行完整目标发送后应用超时 |
| candidate D | 两个 60000 前置均为 16374 目标前缀 | 2 恢复 leaf、两个窗口未执行 |
| B 官方海外 | 4/4 leaf 启动；8192/7 完整，32768/60000 为 16114 前缀 | tuple 正确但容量失败 |
| stock auth/TCP | 1/3 方法通过，TCP raw 验证通过 | 7 故障场景均在 positive 前置阻断，未注入故障 |
| candidate auth/TCP | 3/3 方法、7/7 故障子场景、8/8 fixture 通过，22.055 秒 | 只授本组认证/拒绝/恢复与 TCP sanity，不授大包或 idle |

新 candidate-auth 原始证据独立核验：第一跳错误 UUID/CA/SNI，以及第二跳错误 UUID/CA/SNI/停机分别实际注入；每个场景有故障前完整往返、完整 2 秒拒绝窗内的新实际理由、目标零误送达/无 DIRECT 和授权恢复。目标 ledger 每个负控 fixture 只有一次直接可达性控制及两次海外来源业务，故障 payload 零送达。共 52 个角色对象/53 次启动（停机组海外重启一次），每次准确 binary/config/PID/birth 绑定、owner 缺省与最终进程退出、目标套接字关闭及线程回收核验；TCP 独立验证 TLS、200、固定正文及唯一海外目标来源。

新构建实际 binary 仍为 `1e369833a0863889d8738922461229daa4bf3819cbecf9060bfefb045bb303c1`。新 C/D/B 与旧运行的计数和现象一致，但结论分别来自各自完整原始归档与准确源码 verifier，不拼接两个 head 的通过叶。新 auth 归档包含完整 419 个构建证明文本成员，无法用自洽但未知的 candidate hash 代替来源证明。首轮没有 candidate-auth 归档的失败仍保留。

## 固定门槛与剩余验证

C 保留零包、1/8192/32768/60000/65497、完整后短包/零包及本地超限；D 保留 60000 前置、国内 udp_timeout=2s、末完整业务回复后整个 6 秒释放窗与新 32768 恢复。idle 必须保留同一应用 SOCKS control、UDP socket 和 relay tuple，单次发送、不重放、不保活。close 才允许另建应用关联。

本轮结果不修改原完整门槛，不把失败改成预期成功。后续若做小包生命周期诊断，必须另列为非资格观察，不能替代 D。具体 App/平台的实际普通节点导入保真、手机 TUN/VPN、公网网络、UDP/53/443、域名多答案/重绑定和移动网络恢复仍未验证。

下一步先确定产品真正需要的固定客户端入口与实际 MTU 内 UDP 负载测试点、方向和服务端最小改动；测试现成 App 导入普通节点及其实际 TUN 路径。不得只因 60000/65497 研究门槛失败就要求更换手机核心或自研 App，也不得反过来用较小测试点把本轮完整容量/生命周期红灯改绿。当前可用产品能力仍是固定官方核心的每入口 TCP direct/relay，以及既有普通 direct 能力；研究候选的有限小包成功尚不构成产品 relay UDP 支持。

固定源码支持两个后续归因假设，均尚未通过边界定点或 syscall 动态观测：

- 上行：Mihomo v1.19.32 的 [SOCKS listener](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/listener/socks/udp.go)进入 [POSIX packet reader](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/net/packet/packet_posix.go)，使用[标准 16384 字节 buffer](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/pool/buffer_standard.go)，且丢弃 Recvmsg flags；[SOCKS 解码](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/transport/socks5/socks5.go)去掉此 IPv4 包的 10 字节头，16384−10=16374，与目标前缀一致。
- 下行：[固定 go.mod](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/go.mod)选择 metacubex/sing-vmess v0.2.5 与 sing v0.5.8；[XUDP WaitReadPacket](https://github.com/MetaCubeX/sing-vmess/blob/v0.2.5/xudp.go)使用 [NewPacketBuffer](https://github.com/MetaCubeX/sing/blob/v0.5.8/common/network/direct.go)，[ReadFullFrom](https://github.com/MetaCubeX/sing/blob/v0.5.8/common/buf/buffer.go)在 length 超 capacity 时返回短缓冲区错误；[连接处理](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/tunnel/connection.go)在读错时结束，符合本次大下行应用超时的观察。

这只是与实际现象一致的固定源码路径推断，不等于锁定所有大包故障，也不等于 TUN 与 SOCKS 使用相同入口 buffer。

已知工具后续改进：auth 归档目前在三方法整体通过时自动核全部 fixture 语义；本轮 stock 的单项 TCP 成功由独审直接核 raw observation/ledger。未来若以部分通过计数自动授单项资格，应改为逐方法独立核验并补 mutation 控制。

有界接续顺序：

1. 先补上述逐方法 auth 语义重算，使用本轮真实红/绿档案与单字段 mutation；不由缓存 passed 字段授资格。
2. 如需定位固定 Mihomo SOCKS 大包入口，另立有限边界组，保留本 C/D/B 红色原记录；上行 16374/16375、下行 16384/16385 可作为源码推断的待验证临界点。必须重新绑定应用实际发送/接收、目标 ledger 与进程来源，不把源码推断当 syscall 动态证明。
3. 为实际产品需求单列 MTU 内小包与同应用关联 idle 诊断，仍核原 control/UDP/relay、2s 配置、完整 6s 窗、一次发送、零重放和清理。它只能说明该小包条件，不能补签本 D 的 60000 前置。
4. 选择具体 stock App/平台和普通导入路径后，独立验证手机 TUN 与实际网络；先做服务端角色/路径最小改动对照，再决定是否有可进入产品的受限 UDP 能力。不得迁入完整八补丁或开放产品 UDP 来绕过未决门槛。

## 文本归档收据

[机器可读收据](STANDARD_CLIENT_UDP_ARTIFACTS_20261018.json)保留两次 run 的 11 个 artifact ID、ZIP bytes/SHA256、文本文件数、准确 head/attempt、直接链接与到期时间。首轮依次为产品3、stock193、stock-auth26、candidate599、B436文件；第二轮相同五组另有candidate-auth445文件。所有 ZIP 摘要和严格 manifest 成员 size/hash 均独立核验；三个 candidate 类归档各含完整419个构建证明成员。收据不是原始证据替代品，不包含二进制。

## 复核方式和保留失败

准确 8089 checkout 与冻结 afb checkout 必须分开；下载的只是允许列表内 UTF-8 文本，禁止核心二进制、实际配置、证书或私钥。归档 verifier 返回 0 仅证明红档案内部一致，不意味着容量通过；必须读取 capacity_gate_passed/passed。

离线复核时，运行与档案相同准确提交的 `experiments/standard_client_udp/evidence.py`，传独立冻结 afb checkout、已解压文本归档的绝对目录和准确 arm。认证归档使用同提交 `prepare_controls.verify()`。主 source identity 是完整 tracked-tree 的前后逐文件摘要；不要从较新的文档提交运行旧档案 verifier 后放宽 identity 以求通过。完整执行命令以对应准确工作流为准。GitHub 文本 artifact 保留期为 7 天，过期后依据已保存的源码、计划和摘要重新收集，不把旧档案缺失说成新结果已验。

云恢复前后的本地 netlink EPERM 均发生于首个核心启动、零业务 UDP，保留为环境阻断；不通过提升系统权限解决，不归因协议。恢复文本 checkpoint 40d704e 不声称与失联 dirty 工作树逐字节相同。

[设计与固定分组](https://github.com/ForceMind/V-UI/blob/8089b3f94e5fa599943bd9d9456b0c92cf28ddfe/experiments/standard_client_udp/DESIGN.md)、[恢复历史](https://github.com/ForceMind/V-UI/blob/8089b3f94e5fa599943bd9d9456b0c92cf28ddfe/experiments/standard_client_udp/RECOVERY.md)、[准确工作流](https://github.com/ForceMind/V-UI/blob/8089b3f94e5fa599943bd9d9456b0c92cf28ddfe/.github/workflows/standard-client-udp-experiment.yml)保留可重建命令。候选二进制只存在于临时 runner，不发布、不安装到手机或生产服务器。

# 国内 UDP 候选精简：有限依赖分析与对照计划

状态：准确 93d 的有限构建与两组运行已独核收口，见[结果](DOMESTIC_UDP_REDUCTION_RESULTS_20261018.md)。下文保留原设计与范围；不授产品 UDP 资格。本任务接续[已完成的四点与 1200 字节诊断](STANDARD_CLIENT_UDP_DIAGNOSTIC_RESULTS_20261018.md)，从研究提交 `119fbfe76a83c4f1b4ff4ab0f6bf203ce8bdaa25` 独立工作树推进。仅研究国内服务器代码范围，不改变产品 pin、普通导出、relay UDP 拒绝、发布或部署边界。

## 已有依据与本次问题

准确 `a37a3bf92d9fcb3f811febeb8d975efad4a88039` / [run 39173177705](https://github.com/ForceMind/V-UI/actions/runs/39173177705) 已证明：固定 stock Mihomo 1.19.32 + 完整候选国内 + 官方 sing-box 1.14.2 海外，owner 全缺省，在原应用 SOCKS 关联上完成两个不同 1200 字节包、完整六秒静默窗、约两秒 server leg 释放和单次新包恢复。它没有证明完整八补丁都需要。

本次只问：保留国内 address/lifecycle/有界 mux 与独立 Count 同步修复，移除大包接收扩容和实验 SOCKS owner/private-lease 后，能否保持这一个已通过的有限能力？通过只说明精简集合在本门槛足够，不称逐补丁数学最小，也不授大包、手机 TUN、公网或长期业务资格。

## 八补丁静态职责与依赖

来源冻结为 `afbdba100c5f0e3dc6888684fdbab31980720df3`，不得改原 patch 或其历史。相对路径均在 `experiments/`；机器可读文件记录准确顺序、字节数及 SHA256。

| 原顺序 / 补丁 | 实际职责与调用条件 | 本轮处理 |
| --- | --- | --- |
| 0 `xudp_address/remoteaddr.patch` | `XUDPConn.RemoteAddr()` 返回逻辑 UDP 目标；国内 `UDPConnect` → `NewUnbindPacketConn(remoteConn)` 会捕获它，不能把海外 TCP listener 当回复目标 | 保留完整原补丁 |
| 1 `xudp_address/lifecycle.patch` | 仅在显式 UDPConnect/UDPTimeout 创建新 TimerPacketConn 时，ctx 到期关闭自有 outbound，onClose 在可能阻塞的 inbound End 前先关闭 outbound | 保留完整原补丁 |
| 2 `xudp_mux/mux.patch` | 共享父连接的有界 writer；64 stream、每 stream 单 pending、独立拒绝槽、tombstone/End 顺序和不截断 in-flight frame；保留 OptionData 的零 datagram 语义，子关联关闭不关闭健康父连接 | 保留完整原补丁；不能只为小包绿灯退回无界/可能阻塞共享写路径 |
| 3 `xudp_mux/route_cancel.patch` | 定时取消时再关闭该关联 conn，使有界 mux 的 pending write 可取消 | 保留；语义依赖第 2 项的 bounded Close，不孤立套到旧 mux |
| 4 `xudp_packet/receive.patch` | sing 的完整 datagram 容量、MSG_TRUNC/Windows超限、batch/read-waiter 和 SOCKS接收等；也改变 UDP net.Conn wrapper，不能仅按名字假设零副作用 | 移除；1200 路径须由新实际对照验证，不借旧大包资格 |
| 5 `socks_idle_recovery/sing_owner.patch` | 显式 SOCKS association owner、generation/lifecycle接口、ChunkReader私有lease结束后的cache回收；只有 owner选择的路径使用 | 移除全部；设备是 stock Mihomo，国内是 VLESS 入口，owner缺省 |
| 6 `socks_idle_recovery/vmess_session.patch` | 只有显式 `SetPacketSessionEndReporting()` 才启用的私有 XUDP session-End parser；VMess cache-after-join接口 | 移除全部；普通 XUDP parser 不启用 reportEnd，国内本轮无 VMess 私有lease |
| 7 `socks_idle_recovery/box_owner.patch` | mixed显式选项、owner context policy、受限dialer/privatelease与route分支；另混有与owner无关的 Count() 同步 | 除 Count 的两个锁语句外全部移除；Count作为单独来源绑定的最小hunk保留 |

Count() 保留 `m.access.Lock()` / `defer m.access.Unlock()`，与 TrackConn/TrackPacketConn/CloseAll 对列表的同步相配。它不是 1200 转发所需新功能，也不能因 owner 关闭就丢掉并发读计数修复。Count 无需 PacketSession 类型；抽取原并发 Count 回归的独立类型/测试体，在 race 与 race+debug 下核同步与 Close 回调重入。

静态选择名为 **reduced**，不是已证明的 minimum：四个完整原补丁 + 单独 Count hunk。预计生产源码变化只有 `sing-box/route/conn.go`、`sing-vmess/xudp.go`、`sing-vmess/mux.go`、新增 `sing-vmess/mux_writer.go`；唯一额外构建变更是 `sing-box/go.mod` 的准确本地 sing-vmess replace。sing 保持官方固定模块，不应用 receive/owner、不加 sing replace、不注入新产品选项。测试源码另列，不能混入生产差异统计。

## 唯一有限运行矩阵

先由独审确认依赖设计及源码，再启用独立分支限定 workflow。旧四点/idle-repair 工作流改为 manual，防止后续文件改动重复运行。

| arm | 设备 | 国内 | 海外 | case / leaf |
| --- | --- | --- | --- | --- |
| full-reference | 原样普通导出 stock Mihomo 1.19.32 | 新 runner 重建固定八补丁完整候选，所有 owner 缺省 | 固定官方 sing-box 1.14.2 | 同一个 I 合同：1 / 2 |
| reduced | 同上 | 四个原补丁 + Count hunk 独立构建/版本/hash | 同上 | 同一个 I 合同：1 / 2 |

合计 **2 case / 4 leaf**。只有这两个 arm，只有 1200 字节 first/recover；不跑其他协议、P4/auth、C/D/B、E边界，不尝试更多补丁组合。full-reference 是本 head 运行参照，旧 a37 只作历史背景。前一 arm 仅在原始资料独立重算所有身份/清理/owned-group安全后，才允许后一 arm 启动；容量或释放红不改成预期成功。只允许修复可恢复的夹具/归档错误，不以协议失败无限试组合。

复用准确 a37 的原应用双FD/inode/control/UDP/local/relay、普通导出→device config绑定、单次发送/单次接收、确定性不同hash、完整目标ledger和source验证。国内udp_timeout=2s，窗从最后完整first回复起完整六秒；窗中零发送、不保活、不重放、不重连。release与next-packet独立，未释放也只在身份安全时于完整窗口后发送一次不同1200新包；首包payload/tuple失败则窗口和recover明确未执行。原60000门槛及所有历史红灯不变。

## 构建、回归与可复核证据

- 完整参照继续使用冻结八补丁 builder/verifier 与419份文本证明。二进制仅临时 runner使用，不上传。复用同一次工作流下载的固定源、机制证据和Go模块/编译cache，不能重复下载同份证据或把旧binary摘要当新编译结果。
- reduced 从准确干净官方sing-box/sing-vmess源复制构建；记录原始树完整manifest、按顺序 patch --check/apply、准确Count来源、gofmt、限定生产差异、测试注入清单、编译器hash/version/flags、规范module graph、前后源码及go.sum不变、实际binary hash/版本输出。
- 保留与共享writer安全直接相关的原有 bounded mux、encrypted、encrypted-cancel、empty-datagram、真实route背压回归；按固定原 verifier 核精确test集合及零skip，而不只看go test退出码。Count并发回归独列。race与race+debug分开；有关平台编译只可作为编译资格，不能称运行验证。
- 不把删除的 receive/owner 测试伪装成通过；精简候选不授原大包/owner合同。任何必要构建/模块/来源/安全回归失败都阻止精简核心进入运行。
- 使用新独立kind和明确arm，full/reduced身份校验不能只靠自述candidate hash。共享runtime/归档如需可插拔来源接口，旧默认验证必须原样严格，新增控制证明未知版本、错误patch顺序/成员、被漏的Count/带入owner/receive、额外源码改动、模块替换、二进制/role互换及缺post-identity被拒。
- 归档继续复用 no-follow/canonical allowlist/UTF-8/严格JSON/敏感字段/单件及总预算；完整构建证明与runtime身份绑定，由独立verifier重算而非信cached verdict。安全红结果也要准确保存，缺失与未启动不补造。准确head/tree/run/attempt/artifact receipts及全部原始payload/tuple/window/cleanup经独审后，再更新结果、兼容边界与交接。

## 停止条件与产品边界

两个arm终态、合法文本证据和独审归档之后即收口。若reduced通过，结论是本有限能力可由国内较小维护面实现；若失败，报告实际失败边界，必要模块仍待进一步隔离，不恢复成“三端都得换”的结论。不改产品核心pin、不公开binary、不合入产品UDP、不发布部署。本次不选择或测试具体手机App/TUN，不扩大公网/MTU/并发/长期或包尺寸；这些仍是独立后续门槛。

## 实现中的精确构建控制

生产差异固定四个文件的 before/after 摘要，完整官方 baseline 共 1588 成员；测试注入的八个文件另列。go.mod 只允许官方原文追加唯一 sing-vmess replace，go.sum 不变；完整 411 模块图、固定 Go 1.25.14 实际摘要、编译 flags 与测试前后源码都独立重算。

每个 race / race+debug 模式共有 44 个顶层测试、94 个实际 run/terminal、84 个真正 leaf；两个模式共 188 terminal / 168 leaf。逐项核唯一 run/pass、精确 package、零 skip/额外/重复，再核原 mux、zero、cancel、route 原始事件。

lifecycle 只选原 12 个顶层 + 5 个 guard 子项，明确排除仅旧 lifecycle 阶段的 BlockedInboundWrite（它预期阻塞仍保留）与两个旧 RealMux 负控；不修改旧断言，不称这些三项通过。完整 bounded/route_cancel 语义另由原 TestMuxRouteReplyBackpressure / EndBackpressure 两项与取消/零包组证明。Count 单独抽取旧两 helper 和一个并发测试，不编译整份 owner fixture。

本地 Python 结构/负控不授编译资格；本机没有所需 Go 工具链，实际编译/race/debug 留给固定 CI。完整构建与 runtime 失败均不得作为产品能力，旧证据原样保留。

实现源码独审记录：准确 `6c6fba5`（公开同 tree `32a1a485`）的新增 Python 控制 12/12、零 skip；本地 10.987 秒，独立隔离复核 12.673 秒。既有适配控制 39/39、本地 16.408 秒，属于原控制回归，不代表 Go/core/runtime 已跑。

差异检查口径：从完整设计 checkpoint `23993e1` 起，`git diff --check` 会报告 `count.patch` 原样冻结 hunk 的空 context 行、context 空格+tab 与末尾空行；这是保留源补丁字节带来的已知 patch 文件 whitespace，不改写原始来源来消除提示。排除该一个 `.patch` 后其余完整差异检查通过；`check_docs.py` 通过。Go 与两组真实运行仍须等待准确 CI 及文本归档独核。

## 本轮运行记录

准确 `93d29465` 的两 case / 四 leaf 已在 run 39175898750 attempt 1 完成，见[独立结果页](DOMESTIC_UDP_REDUCTION_RESULTS_20261018.md)。完整构建与运行原档已独核通过，本有限计划收口；不扩矩阵、不将两臂整组对照称逐补丁最小性证明。后续精简 head 的认证/目的地址/DNS 守卫和普通包多轮/真实网络门槛仍待单独计划。

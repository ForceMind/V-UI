# 标准客户端 UDP 定点边界与普通包 idle 诊断

本页接续[原 C/D/B 研究](STANDARD_CLIENT_UDP_RESULTS_20261018.md)和[有界诊断计划](STANDARD_CLIENT_UDP_DIAGNOSTIC_PLAN_20261018.md)。研究分支仍为 `experiment/standard-client-udp-20261018`。这里的 Linux mixed/SOCKS 实测不代替手机 TUN、实际网络或产品中转 UDP 验收。产品仍拒绝有效 relay 入口的 UDP。

## 准确来源与分组

- 首轮：[run 39171985702 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39171985702)，head `1925ddde9bd3d9ce1bee303288eca539843c89f3`，tree `ec6049385ae96cc4492f064d1456af5c34bcff39`，整体 **failure**。
- 有限夹具修复：[run 39173177705 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39173177705)，head `a37a3bf92d9fcb3f811febeb8d975efad4a88039`，tree `826a1bc6567575820e81c4f30bbfae91d05bfe70`。整体 **success**；两 case/四 leaf 全部实际执行。
- 原冻结框架/八补丁来源仍为 `afbdba100c5f0e3dc6888684fdbab31980720df3`；固定 Mihomo 1.19.32、官方 sing-box 1.14.2 及完整构建来源/hash 见[原结果](STANDARD_CLIENT_UDP_RESULTS_20261018.md)。首轮重建 candidate 的实际 SHA256 仍为 `1e369833a0863889d8738922461229daa4bf3819cbecf9060bfefb045bb303c1`。
- 每组设备都是未修改的 stock Mihomo，导入生产普通 VLESS exporter 的原样节点，只在外面加隔离 mixed listener shell。国内/海外临时配置属于研究，owner 选项在所有角色缺省；普通导出节点没有海外凭据。国内 `udp_timeout=2s`。
- E 为候选国内+候选海外的四个独立首包 case/四 leaf。I 原计划三种服务器组合、各 first/recover 两个 1200 字节 leaf，共七 case/十 leaf。首轮只启动六 case/七个 leaf；stock I 的 recover 和海外 I 两个 leaf 未执行。夹具修复另用独立 kind，仅重收 candidate I 与先前未执行的海外官方 I，共两 case/四 leaf。

归档沿用字段名 `capacity_gate_passed`，在本有限诊断中代表 payload/tuple 与资源释放的**总门槛**。应分别读取 first、next-packet、release，不能由总门槛红灯推断某包失败。`original_suite_passed=false` 始终保留；原 C/D/B 的容量红灯和 D 的 60000 字节前置未通过结果不变。

## E：四个定点结果

| 方向/负载 | 原始实际观察 | 严格结果 |
| --- | --- | --- |
| 上行 16374 | 一次 SOCKS wire 16384 字节；海外目标收满 16374，回送完整，应用 payload/hash/目标 tuple 全正确 | 通过该点 |
| 上行 16375 | 一次 SOCKS wire 16385 字节；海外目标只收 16374 字节精确前缀 | 失败；目标完整性前置失败，应用 echo 读取未执行，不称超时 |
| 下行 16384 | 32 字节探针完整到海外目标；目标 `sendto_return=16384`，应用完整收到 16384 且 tuple 正确 | 通过该点 |
| 下行 16385 | 32 字节探针完整到海外目标；目标 `sendto_return=16385`，应用完整等待 4 秒无回复 | 失败；不称显式拒绝 |

四点各一次发送、不重放；每 case 独立原应用对象、准确目标账本、三角色身份与最终双 FD/进程/线程清理均核验。E 的四叶全部执行，整体容量仍红。上行失败时目标 ledger 仍保留实际前缀与目标发送，不以缺少 packet.target 摘要删去实际送达。

这使原先两个固定源码推断有了定点动态支持：IPv4 SOCKS 入口的 16384 字节 buffer 减去 10 字节头对应 16374/16375 的差异；XUDP 下行 16384 buffer 对应 16384/16385 的差异。源码链接保留于[原报告](STANDARD_CLIENT_UDP_RESULTS_20261018.md)。没有 syscall 抓取，不声称锁定全部大包故障；更不能把 mixed/SOCKS 的上限移植成手机 TUN 的上限。

## I：首轮记录及夹具失败

全 stock 的 first 1200 字节在目标收满并回送，应用 payload/hash 也完整，但请求目标端口为 `46021`，解码回复端口为 `60675`，后者是该 case 海外 VLESS listener。严格 tuple 前置失败；仅一次 send/recv，原关联未替换。六秒窗口与 recover 都未执行，不能记作 idle 释放失败或恢复失败。三角色 owner 缺省，实际清理安全。

首轮 candidate I 的原记录中，两包不同 hash 的 1200 字节 payload/tuple 都完整，原 control/UDP/relay 保持；末完整回复后整六秒静默窗实际执行，server leg 首次观察到消失约在 2.063119 秒。可是在线 verifier 错用 candidate 构建来源加载的冻结 `device-singbox` 模块，而观察器正确记录 `device-mihomo`，报 `Wrong sampled device PID/birth/role`。该 stage 未保存 post-identity，在线和独立重算 verdict 不一致；准确原档保留红灯，不能由事后 raw 重分析改判通过。独立安全门槛阻止海外官方 I 启动，无该组原始 artifact。

修复只把当前 observer 与独立别名 verifier 从准确 HERE 路径加载，核两者文件路径及 Mihomo role；保留旧模块对象引用和冻结源码。实际 candidate-provenance loader → current framework → verifier 的导入顺序已加入回归，随后仅补测两个 I case。没有改变传输、payload、tuple、2s 配置、6s 窗口或释放判据。首轮 38 项纯控制、修复源码 39 项纯控制均全过且无 skip；修复本地 15.028 秒，独立隔离复核 14.056 秒。纯控制不代替核心运行。

## 修复补测结果

准确 a37 的两档均由同提交离线 verifier 完整重算，退出 0，有限诊断总门槛为 true，`product_qualified=false`、`original_suite_passed=false`。当前控制 **39/39、17.826 秒**，冻结构建控制 **159/159、9.858 秒**，均无 skip。

| 国内 / 海外 | 首个 1200 | 首次确认 server leg 缺席 | 最后采样开始 / 完成 | 新包发送时刻 | next 1200 / 总门槛 |
| --- | --- | --- | --- | --- | --- |
| candidate / candidate | 完整 payload/hash/tuple | +2.055515s | +6.051189s / +6.091696s | +6.201892s | 完整 / 通过 |
| candidate / 官方 1.14.2 | 完整 payload/hash/tuple | +2.049652s | +6.048564s / +6.088777s | +6.200545s | 完整 / 通过 |

时间均从该组最后完整首包回复算起；不是把配置的 2s 当精确到期瞬间。每组保留同一个原应用 control TCP、UDP socket、descriptor/inode、local 和 relay tuple；窗口中零应用发送，恢复晚于完整窗口及 before_recover 身份采样完成。first/recover 的确定性 hash 不同，每叶恰一次 send、一次 recv，无重放或保活。两次海外 target source 都是 `127.0.0.4`，但 UDP source 端口随合法新 server leg 改变：candidate 53626→52720，官方海外 33072→56265。

每组 60 个 device 样本均无 race；清理前 59 个样本的 control PEEK 均为 would_block，原 device control/relay 两个 inode 均在，未观察到 peer EOF。第 60 个 after_local_cleanup 样本为 local_closed，旧 control inode 已缺席；清理前的观察不等于整个窗口 OS 连续存在证明，归档 `device_whole_window_continuity_proven=false`。server leg 的三组旧 inode 在释放后至最终采样不重现；新业务可创建新的内部 leg，但没有替换应用关联。全部三角色准确 binary/config/PID/birth、owner 缺省、前后源码/二进制身份、目标账本、双 FD/线程/进程及 owned-group 清理均核验。两档各含完整 419 个构建证明成员；实际 candidate binary 摘要与首轮相同。

E、stock I、产品 P4、auth 没有重跑，不把先前 head 的结果授给新 head。首轮 candidate I 的缓存错误、缺 post-identity 和海外组未执行仍保留，不能因新补测通过修改旧档。

## 角色需求与可用能力的边界

在本次 1200 字节、固定 stock Mihomo mixed/SOCKS、两跳 VLESS/TLS/XUDP 路径中，**只将国内角色换成已有 candidate、海外保持官方 sing-box，已经足够完成首包、六秒释放观察与同应用关联新包**。同 head 的 candidate/candidate 与 candidate/官方海外都通过，因此这项普通包能力不要求海外也换 candidate。它没有证明国内八个补丁逐个都必要，也没有证明存在更小可合入产品的补丁集。旧全 stock 的 tuple 错仍提示国内转发路径需要修复；不能因它失败要求修改手机 App。

这只是两次不同 1200 字节业务及一次 idle 间隔，不是长时间持续业务、并发压力或反复恢复。1200 指应用负载，loopback 未经过真实 MTU、封装开销、分片和运营网络，不能把它直接当公网 MTU 合格。原 C/D/B 大包红灯及 D 的 60000 前置未通过完全保留；海外官方 B 大下行的 16114 前缀也没有被本小包通过修复。

产品已验最低能力仍是普通节点的 TCP direct/relay 和原有普通 direct 能力，有效 relay UDP 仍明确拒绝。下一产品决策应围绕现成 App 普通导入、实际 TUN/MTU/网络和国内服务端所需最小修复制定单独门槛；不因极大包研究红灯不必要地改客户端，也不把完整八补丁直接搬入产品。本有限计划到此收口，不继续扩大边界扫描或重跑旧矩阵。

## 产品负控与证据保留

首轮独立 P4 为 **10/10，56.463 秒，0 skip**。三个实核记录分别覆盖 HTTPS 3/15/46、普通 DNS 2/18/40、受保护 DNS 0/10/50，12 个不同 TCP 三态转换、显式 relay UDP reject 与 final drain 均完整。这里只授该产品负控；不能由研究小包结果开放产品 relay UDP。

[机器收据](STANDARD_CLIENT_UDP_ARTIFACTS_20261018.json)按 run/head 分开保存 ZIP bytes/SHA256、成员数、artifact ID 和到期时间。首轮产品/stock/candidate 分别 3/20/469 个文本文件；candidate 含完整 419 个 build-proof 成员。首轮没有海外官方 I artifact；修复后 candidate/官方海外各另有 438 个文本文件，分开绑定新 head，不构造空档案冒充旧执行。只上传通过严格 no-follow/UTF-8/允许列表/敏感值/总预算核验的文本，未上传 binary、配置、证书或私钥。

离线核验必须 checkout 对应准确运行 head，使用该提交的 `diagnostic_evidence.py`；a37 的两个补测归档另加 `--idle-repair`。旧 1925 红档案必须由旧 verifier 重算，不能从新文档或修复提交放宽其 source identity。归档 verifier 返回 0 只表示该红/绿档案内部一致，仍须读取总门槛和各项原始结果。原 C/D/B 结果、首轮夹具失败和未执行叶均保留。

## 后续国内精简进展

本页 a37 的八补丁角色结果保持。另一个准确 head `93d29465` 的[国内-only 精简对照](DOMESTIC_UDP_REDUCTION_RESULTS_20261018.md)已完成：四原补丁加独立 Count 在同样 1200/六秒条件足够，设备和海外均 stock。它是较小充分集，仍非逐补丁最小性或产品 UDP 资格；没有改写本页首轮失败和旧 C/D/B 红灯。

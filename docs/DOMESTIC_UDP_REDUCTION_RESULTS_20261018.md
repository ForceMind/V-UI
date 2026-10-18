# 国内 UDP 候选精简：固定 1200 字节同关联对照

接续[依赖与有限计划](DOMESTIC_UDP_REDUCTION_PLAN_20261018.md)。本页只记录两 case / 四 leaf 的国内角色对照，不扩大到手机 TUN、公网 MTU 或产品 relay UDP。原 [C/D/B 红灯](STANDARD_CLIENT_UDP_RESULTS_20261018.md)、[首轮夹具失败与定点边界](STANDARD_CLIENT_UDP_DIAGNOSTIC_RESULTS_20261018.md)原样保留。

## 准确运行与当前结论

[run 39175898750 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39175898750) 于 2026-10-18 23:30:08 UTC 完成，整体 success。准确 head `93d29465ef328665b8d08b9cd0357a47c0eb6daf`，tree `c50d9a0731299f7c980cd3545451f22fe7ba722a`。没有重跑旧 C/D/B、E、产品 P4 或 auth；旧 head 的认证通过不授本精简候选资格。

本轮完整候选国内与精简国内两组均记录：首个 1200 字节完整、原应用关联静默六秒期间 server leg 释放、同一应用关联发送另一个不同 hash 的 1200 字节新包完整。准确 head 的离线 verifier、独立原档逐成员与语义复核均已完成并通过；以下结论来自原始证据，不只依据 CI success。

两组设备始终是固定 stock Mihomo 1.19.32，海外始终是官方 sing-box 1.14.2；普通 VLESS exporter 节点原样放入独立 mixed listener shell。国内使用研究 guarded 配置，UDPConnect 与显式 `udp_timeout=2s`，所有角色 owner 选项缺省。没有客户端改动、海外候选替换或实验 binary 分发。

## 精简集合与构建证明

精简集保留四个完整冻结补丁：`remoteaddr.patch`、`lifecycle.patch`、`mux.patch`、`route_cancel.patch`，另从 `box_owner.patch` 原样提取 Count 的两个锁语句。移除 `receive.patch` 与 owner/private-lease 三个 overlay 的其余全部内容；sing 保持官方固定模块，无 sing replace。

生产差异只有四条路径：`sing-box/route/conn.go`、`sing-vmess/xudp.go`、`sing-vmess/mux.go`、新增 `sing-vmess/mux_writer.go`。完整官方 baseline 1588 个成员及各 before/after 摘要固定；patched 为 1589 个成员，注入八个测试后为 1597 个成员，tested 与 post 完全相同。唯一模块文件改动是 go.mod 的 sing-vmess 本地 replace，go.sum 不变，411 个模块图、固定 Go 1.25.14 摘要、编译 flags 和测试前后完整源码均归档。

| 角色 | 本轮实际 SHA256 / 版本 |
| --- | --- |
| stock device | `3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8` / Mihomo 1.19.32 |
| 官方 overseas | `07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793` / sing-box 1.14.2 |
| fresh full-reference domestic | `1e369833a0863889d8738922461229daa4bf3819cbecf9060bfefb045bb303c1` / `1.14.2-vui-socks-owner-experiment-afbdba100c5f` |
| reduced domestic | `fd6235aa7614ebc1ce13b848883b1e323d71d5a7750e30523ff57aaa2511f73a` / `1.14.2-vui-domestic-reduced-93d29465ef32` |

完整参考在本轮重新构建并通过冻结八补丁全证明，不从 a37 继承运行资格。精简构建实际完成八组各 race / race+debug：address、bounded mux、encrypted、encrypted-cancel、empty-datagram、mux-route、lifecycle、Count。每模式 44 个 top / 94 个实际 run 及 94 个 terminal / 84 个真正 leaf；两模式合计 188 terminal / 168 leaf，零 skip、失败、缺失、重复或额外测试。原事件 verifier 另核取消、零 datagram、真实 route 背压和生命周期。Count 独立并发测试不引入 owner fixture ABI。

lifecycle 明确不选择只属于旧阶段的 BlockedInboundWrite 和两个 RealMux 负控，不改这些旧断言、不称它们通过；新有界关联背压由两项原 TestMuxRoute 及 mux 取消组证明。Darwin/Windows 的 vmess、route 共四项仅编译通过，不代表这些平台运行。本轮 CI Python 控制为现有 39/39（16.147s）、新 12/12（12.046s）、冻结构建 159/159（7.902s），全部无 skip。

## 两个原应用关联的实际证据

所有时间相对本组最后完整首包回复；首次缺席是完成一次实际采样的时间，不把配置 2s 宣称为精确到期瞬间。

| 国内 / 官方海外 | first 1200 | server leg 首次确认缺席 | 最后采样开始 / 完成 | 新包发送 | next 1200 |
| --- | --- | --- | --- | --- | --- |
| full-reference / official | payload/hash/tuple 完整 | +2.054233s | +6.055079s / +6.068451s | +6.166555s | payload/hash/tuple 完整 |
| reduced / official | payload/hash/tuple 完整 | +2.085385s | +6.054095s / +6.067182s | +6.168906s | payload/hash/tuple 完整 |

每叶一次 send、一次 recv，无重放或窗口保活，first/recover 的确定性 hash 不同。应用 control TCP、UDP socket、descriptor/inode、local 和 relay tuple 保持原对象；恢复发送晚于完整窗口和 before_recover 身份采样完成。海外目标来源均为 `127.0.0.4`，新的内部 leg 合法改变 UDP source port：full-reference 51444→42696，reduced 42627→57573。两组各三角色均单次启动；PID/birth、配置/binary 摘要、前后源码/二进制身份、目标 ledger、双 FD/线程/进程及 owned-group 清理均已逐项独核。server leg 分别有 86/87 个采样，三组旧 inode 在首次缺席后至最终窗口不重现。

full-reference 有 88 个 device 样本，其中第 32 个（从 0 起）release_window 的内部 fd8 在采样中变化，被明确标为 inconclusive；不能写成零 race 或连续存活证明。reduced 有 89 个样本、无 race。每组最后一个 after_local_cleanup 是 local_closed；清理前分别 87/88 个 control PEEK 为 would_block，未观测到 peer EOF，原 control/relay 身份按被动验证器核验。两组都保留 `device_whole_window_continuity_proven=false`。

归档沿用 `capacity_gate_passed` 名称，在本有限 I 中代表 payload/tuple、资源释放、next-packet 的总门槛；first、release、next 均单列。`original_suite_passed=false`、`product_qualified=false` 不变。

## 可用能力与维护边界

这组对照支持一个更小的国内-only充分候选：四原补丁加 Count，在本固定两包/六秒条件下不需要 receive、owner/private lease，也不需要修改设备或海外核心。它不是逐补丁消融，不能证明每个保留补丁均必要或数学最小。receive 是通用 UDP 接收/截断及 buffer 改动，删除它只在本 1200 路径取得证据，不等于所有服务器大包路径都不需要修复。

维护上可把四条生产源码、固定上游/模块图、Count 并发及有界 mux/route 事件回归作为独立国内候选边界。没有迁入产品 pin、选项或正式构建链。普通实际 App 的导入/TUN、MTU/封装/分片、DNS/53/443、长时间并发、多次 idle、域名答案/重绑定及本精简 head 的认证拒绝/恢复仍需独立资格。这里仅两次不同 1200 字节业务和一次 idle，不能称长期持续中转已验收。

后续开放项先围绕精简国内候选设计同 head 的认证/目的地址/DNS 守卫回归，再明确普通包多关联/多轮恢复与真实网络/App 门槛；不继续扩大极大包扫描，不因 SOCKS 大包红灯要求用户自研手机 App。产品当前有效 relay UDP 拒绝、普通 TCP direct/relay、pin/VERSION、发布和部署边界不变。

## 归档与复核

[机器收据](STANDARD_CLIENT_UDP_ARTIFACTS_20261018.json)新增本 run 两项，旧 16 项不覆盖。full-reference 有 438 个文本成员（其中 419 个 build proof），reduced 有 143 个文本成员（其中 124 个 build proof）；ZIP bytes/SHA、artifact ID、run/head/attempt 及七天到期时间分别绑定。实际没有 build 失败，因此失败文本分支未执行、不虚构失败 artifact。未上传二进制、实际配置、证书或私钥。

离线必须 checkout 准确 `93d29465`，分别执行 `experiments/domestic_udp_reduction/archive.py --legacy-repository <afbdba1> --runtime <absolute-archive> --arm full-reference` 或 `--arm reduced`。后续文档提交不能替代该源码身份。目录按固定允许列表、严格 JSON/UTF-8、敏感值、no-follow 和总预算检查，逐成员重算 size/hash，再重放 build/runtime 原记录。

差异检查保留计划中的准确例外：原样 Count patch 上下文会触发完整 diff whitespace 提示；排除这个 patch 后其余完整差异和 check_docs 通过，未通过改写来源字节隐藏提示。

完整 job 日志为 127865 bytes，SHA256 `c93709e65702875e11a19466a3ace516973ecab11ea96bf764ff764587d6819f`；完整参考 419 个及精简 124 个 build 成员均已独核。精简 38 条命令全部返回 0、无 timeout/残留 owned group，两个 runtime child 均返回 0、安全门槛通过。本有限研究收口，后续工作按上节开放项另定范围，不再重跑本矩阵。

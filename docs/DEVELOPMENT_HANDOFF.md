# 当前开发与接手入口

更新：2026-10-18。每入口 TCP 三态已完成 PR49 正常合并及准确主线验收，见[收口证据](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。本文同时保留尚未完成的独立任务；计划记录不等于那些任务已通过。

## 先做什么

1. 阅读 [开发约束](../AGENTS.md) 和 [每入口路由契约](PER_INBOUND_ROUTING_CONTRACT.md)。
2. [PR49](https://github.com/ForceMind/V-UI/pull/49) 已正常合并至签名 `0962f0c4995b3b7606652027f2330285e4dea1f7`，tree `b12d1e40d16060d3adfa1c29fb380ae5949896f5`，准确主线八组与四目标已独立验收。接手先核远端默认分支 **master**、当前工作树及后续提交；从准确最新 master 新建独立任务分支，不回到旧 feature 分支覆盖变更。
3. 已完成：同一 V-UI 实例中，现成客户端导入不同普通 VLESS 节点，分别“跟随全局 / 指定直连 / 指定中转”。标准客户端 UDP 独立研究已实际执行，完整容量与同关联 idle 门槛仍红；先读下表及研究记录，不能把 TCP 收口当作 UDP、手机或发布许可。新提交仍需自己的准确验收。
4. 使用固定官方核心。不要从 UDP 实验分支整批移植补丁，也不要把手机客户端自研/重编译设为产品前提。
5. 每阶段更新本文状态表与证据，提交可恢复的 WIP 文本备份。最终准确候选仍须完整审查及 CI；WIP 不是通过或可合并声明。

## 用户已确认的产品含义

- 直连节点：客户端 → 该入口服务器 → 目标。
- 中转节点：客户端 → 国内入口 → 选定海外上游 → 目标。
- 手机只导入所选入口的标准配置；第二跳由服务端决定，海外 UUID、地址与本机代理口令不进入设备配置。
- 主机本身代理开关与入口默认中转开关独立。每入口策略覆盖默认，所有中转入口共用一个 `selected_upstream_id`，本轮不做每入口多个上游。
- 本轮新增资格仅为 TCP。**有效中转入口的 UDP 继续拒绝**；普通直连节点原有能力和导出语义不被笼统的“UDP 关闭”改变。
- 单元、核心配置检查、真实 Linux loopback、浏览器、手机 VPN/TUN、生产线路分别验收。没有手机真机结果。

## 可追溯基线与当前状态

| 工作 | 准确依据 | 状态及剩余 |
| --- | --- | --- |
| 正式 v0.4.8 | `a9f9b5d9c0dd2aada5e8f5f2270b2ce93440d5ff` | 已发布的安装器修复；后续主线功能不自动包含在旧发布中 |
| M0–M4 双开关 | `9a97ad13aae2fabb5ed969fdc0f930c36fdc55b7`，[PR44](https://github.com/ForceMind/V-UI/pull/44) | 主线收口；[历史失败/四目标证据](DUAL_PROXY_RELAY_PROGRESS.md)保留 |
| D0 UDP/DNS 能力门槛 | `13c884a0539cf9822fca2fe2db58325fa4644048`，[PR46](https://github.com/ForceMind/V-UI/pull/46) | 主线收口；不是开放 UDP 中转 |
| D1 TCP 上游专用导出 | `a98da9afbbf58a54f4f102cccec2f26b6e07d2ee`，[PR47](https://github.com/ForceMind/V-UI/pull/47) | 本阶段之前的产品基线；标准设备导出与“供另一 V-UI 导入的上游导出”用途不同 |
| 每入口三态 TCP | 签名 master `0962f0c4995b3b7606652027f2330285e4dea1f7`，[PR49](https://github.com/ForceMind/V-UI/pull/49) | P0–P5 已收口；准确候选及主线八组、四目标、真实标准客户端和浏览器均独立验收，见[完整记录](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。尚未发布 |
| D2 XUDP/大包/owner 实验 | `afbdba100c5f0e3dc6888684fdbab31980720df3`，tree `996d37d925be8edd18cbf921d3cc580f43c0730d` | 独立实验部分合同通过，整体仍失败；未并入产品，见下节 |
| 标准客户端 UDP 补验 | [兼容边界](STANDARD_CLIENT_COMPATIBILITY.md)、[实际研究与剩余计划](STANDARD_CLIENT_UDP_RESULTS_20261018.md) | 全 stock / 候选两服务器 / 官方海外 B 已分组实跑；小包部分完整，大包容量红，D前置失败使窗口/恢复未执行；产品relay UDP继续拒绝 |
| 有限 UDP 边界 / 1200 同关联诊断 | [固定计划](STANDARD_CLIENT_UDP_DIAGNOSTIC_PLAN_20261018.md)、[独立结果](STANDARD_CLIENT_UDP_DIAGNOSTIC_RESULTS_20261018.md) | 四点已收集；准确 a37 两组 1200 同关联 I 通过，candidate 国内+官方海外已足够本有限条件；八补丁最小集未证，原 C/D 红及夹具失败保留，不授手机/产品 UDP 资格 |
| 国内 UDP 候选精简 | [有限依赖与对照计划](DOMESTIC_UDP_REDUCTION_PLAN_20261018.md) | [准确93d结果](DOMESTIC_UDP_REDUCTION_RESULTS_20261018.md)：四原补丁+独立Count、仅国内精简；2case/4leaf 与完整构建原档独核通过；本1200/6秒条件足够，下一步为精简head认证/目的地址/DNS守卫资格；不授产品或逐补丁最小性 |
| 手机、真实外部线路 | 实机与运营线路独立门槛 | 未验收；Linux core loopback 不代替手机导入/TUN/移动网络 |
| 低资源长期目标 | [资源合同](LOW_RESOURCE_TARGET.md)、[已有软件收口](LOW_RESOURCE_CLOSURE_20261011.md) | 既有软件优化证据保留；真实 1 vCPU/512 MiB 整机和 24 小时稳定性仍待专门环境 |
| 新功能正式发布/部署 | [发布门槛](RELEASING.md) | 未执行；代码合并不等于发布，生产部署及新权限仍另行批准 |
| 其他远期范围 | [路线图“后续”](../ROADMAP.md#后续) | DNS-01/通配符、核心升级/回滚、任意 YAML 合并、专用容器/NixOS 等仍是后续独立版本，不并入本轮 |

### a98 产品主线 CI

2026-10-18 只读复核准确 a98 的八个 run 均 completed/success、attempt 1：
[test 38918235245](https://github.com/ForceMind/V-UI/actions/runs/38918235245)、
[docs 38918235289](https://github.com/ForceMind/V-UI/actions/runs/38918235289)、
[ACME 38918235288](https://github.com/ForceMind/V-UI/actions/runs/38918235288)、
[one-click 38918235306](https://github.com/ForceMind/V-UI/actions/runs/38918235306)、
[ToClash 38918235349](https://github.com/ForceMind/V-UI/actions/runs/38918235349)、
[portable 38918235256](https://github.com/ForceMind/V-UI/actions/runs/38918235256)、
[deployment 38918235367](https://github.com/ForceMind/V-UI/actions/runs/38918235367)、
[loopback 38918235294](https://github.com/ForceMind/V-UI/actions/runs/38918235294)。
这些是旧基线结果，不能替代每入口功能新准确提交验收。

### afb 实验结果与不能继承的资格

[run 39146603341 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39146603341)、
[PR48 研究记录](https://github.com/ForceMind/V-UI/pull/48#issuecomment-6252755306)。PR48 仍 Draft，实验 head 与 PR 自身 head 不同，均不代表产品合并。

- owner 机制 838 叶分别在 race / race+debug 严格通过；base/final 各 23 组与 22 项跨平台编译通过。
- 实验 owner-on 三角色链 C：78 case/198 叶，其中 195 次完整请求-回复与 3 次本地 EMSGSIZE；D：6 case/12 次请求-回复通过。
- 三条 idle 场景各在同一应用 SOCKS 关联上完成一次恢复：60000 字节往返，等待完整六秒观察窗，再以新的 32768 字节包启动第二代。旧私有传输退出先于新代；不是同一真实 owner 连续 200 次恢复，也不授整窗 OS 连续性。
- owner-off 原 D/A/V 仍失败，B/U 只是诊断完成且容量失败；原官方设备矩阵两 arm 各 19/20，四个 VMess 官方设备大包仍 32768 → 精确 15992 前缀，60000 尚未发送。
- 因此整体 run 仍 failure。旧失败不改判；成功的三角色组合只是一组已测条件，**不是所有现成客户端必须替换核心或必须自研 App 的证明**。
- 完整源码补丁、测试和失败账本留在实验分支 `experiment/xudp-mux-lifecycle-20261017` / `experiment/socks-owner-recovery-20261018`。不把实验二进制发布、安装到用户设备或迁入本轮产品。

## 本轮执行顺序与退出门槛

| 阶段 | 交付 | 当前状态 | 退出门槛 |
| --- | --- | --- | --- |
| P0 | 本入口、设计契约、精确调用点与兼容纠正 | 已完成，设计和文档已独审 | 无未决的保护策略降级，独审可据文档接续 |
| P1 | 严格三态字段、旧库迁移、统一有效策略、身份兼容 | 已完成，纳入准确候选/主线全套验收；线程迁移测试不扩称跨进程 | 旧全 inherit 配置/身份保持；非法/null 拒绝；省略更新保留 |
| P2 | apply/rollback/启动/恢复/备份/删除保护 | 已完成，双向 fence、事务/恢复和四目标 installed smoke 通过；模拟与真实层次仍区分 | 双全局 off + explicit relay 仍受保护；任何不一致不退普通直连 |
| P3 | API/UI/导出三用途与 desired/applied 标识 | 已完成，主线 proxy 20 方法、实际 API/core 浏览器 4 方法、D1 九方法及 managed editor 两方法全部通过 | 标准客户端只获入口信息；取消/刷新/失败无假生效/秘密泄露 |
| P4 | stock Mihomo 同实例三个节点和 host 四组合 | 已完成，主线 7 控制 + 3 真实方法；61 次业务 HTTPS + 3 控制，各场景单客户端 PID | 真实目标分别观察本机/海外出口；负向原因、零误送达、无回退；四状态/12 切换 |
| P5 | 最终候选、独审、八组、四目标、正常合并后重验 | 已完成，b5 候选八组 attempt 1；0962 主线七组 attempt 1、deployment attempt 2，所有最新 job/step 通过 | 精确 SHA/tree 与原包绑定，独立验收；历史失败和 skip 单列 |

## 历史 WIP 与失败记录（不改判）

- P1 保留旧全 inherit 编译身份；12 组旧实现逐值对照一致。迁移并发只做了线程/RLock 幂等测试，不能称跨进程迁移已验。
- P1 独立旧 proxy 宽扫曾出现 112 errors、6 failures、50 skips，主要是旧瞬时 fixture 缺新字段和未整合跨层实现；保留原失败，随后必须在完整候选重新运行。
- 前端新增 4 组 Chromium 生命周期用例已接入原 browser gate；本地首次 13 项在 Chromium 启动时 socket EPERM，另一次启动工具遇只读 sandbox 错误。没有取得浏览器运行成功证据；不通过改变系统权限解决。
- P2 首版独审发现双向能力 fence 缺口：`state.schema_version=2` 却缺 DB mode 列、以及 DB 已有非 inherit 行但缺完整 v2 状态（含整个 proxy store 缺失）。已补 create_all 前的文件/表/列检查和持久行运行校验，拒绝 bootstrap 或补写标记掩盖损坏；合法 prospective 保存仍先写 fence 再提交 SQL。局部迁移/恢复控制通过，最终独审与全套结果继续单列。
- 首次完整集成宽扫：1923 tests，6 failures / 28 errors / 200 skips。失败包含独立维护 controller 的 restore 依赖闭包缺失、旧极简备份/异常转换和旧协议 fixture 未显式填写新字段；正在按真实原因修复。该结果不能写成全套通过。
- 第二次完整集成宽扫（`c4b2d367352501f14c84063f8d670ee8e5a69680`）：1965 tests，2 failures / 5 errors / 205 skips。4 个错误为新增导出夹具绕过首次 selected00/fence 流程，1 个错误为小型恢复夹具没有可验证的已选 release；两失败为新增进程尚未登记既有启动审计清单、日志 flush 与 writer Event 的调度竞争。已按真实契约修正：不减生产 fence，登记已审启动路径，拒绝函数返回当下核完整新日志，再有界 join writer。28 项聚焦控制通过，旧失败保留，最终全套仍需重跑。
- 恢复收尾额外覆盖：持久损坏策略校验先于 manager/coordinator 早退，损坏 store 的 sing-box stop 失败也不遮住 Xray stop；首次无文件/无表 v2 数据拒绝后不创建/改写文件。组件 139 项纯模式/迁移/恢复/维护控制与 55 项小型归档/manager/事务控制通过，不能相加冒充不重复的全套计数。
- 固定官方 sing-box 对复制 payload 的 installed policy smoke 完成 8 旧 + 8 混合配置检查及 4 组同配置/不同策略身份断言；这些不启动核心，也不是同提交四目标包或真实双跳资格。
- P4 首次真实尝试暴露夹具错误预期：首次 direct 保存实际正确返回 saved=true/applied=false 的 409，已按契约修正；第二次 check 有效后 apply 返回 PROXY_APPLY_FAILED。独立固定官方 sing-box 空配置启动得到 `create netlink socket: operation not permitted`，不据此判产品转发通过，也不提高主机权限绕过。余下真实方法未跑。
- 新增 8 项真实 HTTP/SQLite/private-store/完整 coordinator 控制通过，核心与 DNS 明确为 fake；覆盖首次 direct 无上游、首次 relay pin、真正 SQL commit 异常、两种过期 token、手动停止后保存和删除回滚。首稿夹具非法幂等键与清理顺序失败已修。删除测试曾错误要求可信完整旧配置也必须停机；按已冻结合同改为锁定旧 revision/config/context 逐值保留、dirty 与未应用响应，并验证下次启动因源不一致拒绝。界面明确提示旧入口可能仍可用，不把已保存删除称为已生效。
- 固定官方 sing-box 1.14.2 对三模式 × 四开关组合 × TLS/REALITY 上游的 8 组配置解析通过，另原 Xray/sing-box 正负配置检查通过；解析不等于转发。
- 各纯测试分别只证明对应组件。第三次完整本地宽扫结束时，真实新增核心/浏览器与同提交四目标尚未全部完成，不能由该次本地结果授予合并或功能收口；后续准确结果见下面的候选记录。
- 首次指定直连可保持两个开关关闭、**不选择上游**，正常 check/apply 建立活动配置；保存期间没有活动配置时显示待应用/不可确认。指定中转才需要先应用合法的已选上游；不能把普通直连变成必须配置海外。

## PR49 首轮准确候选与后续修复

- 准确公开候选 `8b01b4b085ff4520e72a105aac12a8e6a262d7e5`，tree `ed2cada9993f05025e895b7eba2108114ee77021`；当时 PR49 保持 Draft。该 tree 与本地独审 `483d38c1bfff0b282f53cea23404196fa60d25e1` 相同。第三次完整本地运行共 1970 tests，1765 通过、205 skips（不授对应真实资格）、零失败，187.061 秒；skip 不授真实资格。日志 SHA256 `6baa06aab56b96b1ebb055ecef74d707665d44d45d1f7bc21cb78a61e774df4e`。
- [Test attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451801) 的 1970 项基础套件与固定核心配置检查通过；proxy browser 步骤 20 方法（13 个浏览器流程与 7 个静态/路由控制）为 18 通过、1 failure、1 error。两个新 mocked editor 场景只修改已挂载页面的 URL fragment，没有真实切换 Vue 入站菜单，因此找不到预期行。后面的新实际 API/core browser 尚未执行。原日志 SHA256 `338b5f02200a39a28d5eb2f287ea5162839d5f24617d8295cab0c76ffbd7d678`。
- 后续修复仅改变测试导航：实际点击入站菜单并确认列表 GET 200，再执行原断言；不增加生产 hash 导航，不改变时间窗或资格断言。必须在后续准确 head 重跑真实浏览器，静态控制不能把本次失败改绿。修复后完整本地再次运行 1970 tests，1765 通过、205 skips、零失败，181.935 秒；日志 SHA256 `8c4a26493c56fa8fc6c957542574944023e6c1d9b2c6867acb72c25f5070861b`。
- [Portable attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451913) 的 aarch64-glibc 在固定上游 commit API 遇到 GitHub HTTP 403 rate limit，尚未进入 bundle/stage smoke；x86_64-musl、aarch64-musl 与 distro probe jobs 成功。失败日志 SHA256 `2615a3068670d52d9d141897f04529a0347207314bf99c23ceb8bd261104c921`。这不授四目标完整通过；代码已有修复时不通过重复旧 head 掩盖缺口。
- 同 head 的 [ACME](https://github.com/ForceMind/V-UI/actions/runs/39153451847) 实际 4 个证书测试与 1 个浏览器测试通过；[one-click](https://github.com/ForceMind/V-UI/actions/runs/39153451776) 实际非 root/systemd 流程通过。属于旧回归，不代替新增三态转发或实际浏览器资格。
- [真实链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451708) 成功。新增每入口组 10 方法（7 控制 + 3 真实）全部通过，53.497 秒。固定官方 sing-box 1.14.2 与 stock Mihomo 1.19.32 消费实际普通 VLESS YAML/URI；三个场景分别完成 2 / 14 / 45 次业务 HTTPS，共 61 次，另各有一次独立目标可达性控制。三模式 × host/global 四态的 12 条有向切换完整，每场景保留同一客户端 PID。错误 UUID/SNI/CA/上游停止均有实际拒绝原因、目标零误送达和直连保持；普通 direct 小 UDP 成功、relay UDP 实际 reject。每场景最终停止/排空核验通过。这是 Linux loopback，不能扩称手机真机/TUN或中转 UDP 能力。
- 该真实链路归档 `12146705982`：20290 bytes，SHA256 `e3b9f058b5b113d99cc9d6a3553dccb1ac188e645b9b61093ea4fe6ef0045643`；`result.log` SHA256 `b121a4f0707a08e4d58801f0ac635b7c0242474ae1002e7af6d7f5ee1b26168f`。固定 provenance 和完整旧链路组保留，独立复核已确认该有限合同，不能继承到下个 head。
- [部署消费 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451878) 成功：4 方法通过，512.597 秒；实际非 root 安装、HTTPS/原浏览器入口、停止后恢复及代码回滚通过。包内新增混合策略 smoke 为 8 配置检查 / 4 同配置异身份，恢复 smoke 为 4 混合恢复 / 4 旧 reader 拒绝；这些明确不启动代理核心、不代替三态转发。512/384/320 MiB cgroup 与单 CPU quota 回归通过，不代表同容量整机。证据归档 `12146577044`：80207 bytes，SHA256 `eebd88b7c254af3e9e738da6d1b76e9eb37264f2299a38af4585787353f81d54`。首轮八组最终 6 success / 2 failure；下一准确候选不能继承旧 head 结果，须重新核八组、真实新增步骤、四目标及最终独审。

## 第二候选 f2 的真实浏览器与保留失败

- 准确 `f2bee4319b3419a3af109ca9509f79b5c3809070`，tree `02ae09597490d9fd12727743d451d9ed4ce903a8`。相对 8b 仅测试导航与项目账本，生产逻辑、固定核心、工作流门槛未改。
- [Test attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988697) 中 proxy browser 20 方法全部通过，47.097 秒；新增每入口实际浏览器组 4 方法（3 控制 + 1 真实流程）全部通过，6.442 秒。真实流程经过创建 direct、无上游双关、检查/核对/应用、HTTP/SQLite 与实际官方核心 PID/配置/监听校验，无成功响应 mock；不等于手机或浏览器数据转发测试。
- 后续 D1 9 方法中，5 个真实浏览器流程及 3 个其他静态控制通过，唯一 pinned Vue controller 控制失败：其替代 `addEventListener` 的单值槽覆盖同名事件的旧监听，导致 `pagehide-secret` 断言失败。生产 DOM 允许多个独立监听；修复只把测试改为原生 `EventTarget`，同时严格验证 pagehide/popstate 的上游导出与节点编辑器清理、请求取消和迟到结果。秘密清理生产代码和原断言不放宽。更后的 managed-certificate editor 步骤未执行。
- 原失败日志 SHA256 `701ca45b9596f636380ca54526c09c7479ff16db876d4455adb4b30c73bb574a`。静态修复不能回写该 run 为通过；下一准确 head 必须重跑所有浏览器步骤。后续本地完整 discover 显式提供固定 Vue 资产、但不启真实 browser/core opt-in：1970 tests 中 1766 通过、204 skips、零失败，182.199 秒；此前因资产未提供而跳过的 controller 控制本次实际执行。日志 SHA256 `4bc46958b4b4842e665895d4738eef1b7c56700af65d9117891f59e825413d71`。
- [Portable attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988712) 四个 jobs 全部成功，包含此前缺失的 native aarch64-glibc；原 8b 限流失败保留。新准确提交的同源四目标最终归档仍须单独核验。
- [真实链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988764) 再次通过；新增组 7 控制 + 3 真实方法，56.091 秒。业务 2/14/45、12 转移、三单客户端 PID、拒绝/完整排空与原始计数均独立核验。归档 `12147166827` 为 20341 bytes，SHA256 `b9a3e4298e8969072701407466ee2a7f521f6154030659fd18a53659dd567736`，日志 SHA256 `88a950ae87967bea43fc4cb80f5284e3c1333bde7b5f7ce6cffb29c262d3e646`；不替代后续 head 验收。
- [部署消费 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988641) 成功，4 方法（3 夹具 + 1 实际）407.792 秒；512/384/320 MiB cgroup 均完成且无 OOM/kill，实际 source 为 f2。证据归档 `12146528099`：80045 bytes，SHA256 `cb6d0648b4d98ab94c059138dcf042866562c1ce38b29b0c0adff4780e2c7d8a`。本候选八组最终 7 success / 1 test failure，依然不可合并；不得拼接不同 head 的绿灯。
- 用户按实际界面操作的[每入口使用说明](PER_INBOUND_ROUTING.md)与 D1 教程更正已独审；均保持未发布候选、保存与应用分离、标准客户端和已知能力边界。其余准确运行结果继续绑定 PR49，不继承旧候选成功。

## 当前收口与下一步

主线 0962 的准确结果、八组链接、四目标包摘要、首次 403 和单次重跑见[收口记录](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。以上 WIP/8b/f2 段落是各次历史结果，不能当成当前尚待验收，也不能把旧失败擦除。

1. 已完成的 TCP 三态保持固定官方核心和单 selected upstream；不再重新实现 P0–P5。
2. 在独立实验分支按 [stock 客户端 UDP 最小计划](STANDARD_CLIENT_COMPATIBILITY.md)核普通标准配置、容量和同应用关联恢复。保留原 sing-device RED，不提前开放产品 relay UDP。
3. 手机 VPN/TUN、公网线路、整机 24 小时低资源与正式发布分别待相应环境/授权，不能从 Linux loopback 或 cgroup 推断完成。
4. 新功能/修复/最终文档继续准确提交独审与 CI；没有 Release、生产部署或敏感权限变更授权。

## 接手命令与环境

使用独立非生产 Linux 云环境、Python 3.12、Node 语法检查、Git；真实测试另需固定官方核心及 Chromium。仅测试临时目录、假凭据、临时 CA，不修改系统信任/防火墙。实际安装/systemd 测试只在明确空白 runner。

```sh
git status --short
git rev-parse HEAD HEAD^{tree}
git log -5 --oneline
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
export VUI_DATA_DIR="$(mktemp -d)"
export VUI_FRONTEND_ASSETS="$(mktemp -d)/assets"
python scripts/vendor_frontend.py "$VUI_FRONTEND_ASSETS"
python scripts/check_docs.py
python -m compileall -q app tests scripts main.py
node --check web/js/app.js
node --check web/js/proxy.js
python -m unittest discover -s tests -p 'test_proxy*.py' -v
python -m unittest discover -s tests -p 'test_inbound*.py' -v
python -m unittest discover -s tests -v
```

真实核心/浏览器的可执行命令以 `.github/workflows/loopback.yml`、`test.yml` 和 [贡献指南](../CONTRIBUTING.md)为准：`scripts/fetch_test_cores.py` / `fetch_mihomo.py` 校验固定来源，`VUI_PROXY_LOOPBACK=1`、`VUI_TEST_CORES`、`VUI_TEST_MIHOMO` 激活真实 proxy 测试；`VUI_PROXY_BROWSER=1` 与固定前端资产激活浏览器。新增每入口真实步骤使用 `VUI_INBOUND_ROUTING_LOOPBACK=1` 执行 `test_inbound_routing_real.py`；配置解析用 `VUI_TEST_CORES` 执行 `test_inbound_routing_parser.py`。新增实际浏览器流程以 `VUI_INBOUND_ROUTING_BROWSER=1` 执行 `test_inbound_routing_browser.py`，准确 0962 已实际完成 3 项控制与 1 个真实流程；新提交仍须显式执行，不能仅因默认 discover skip 便计通过。

没有 Python/核心/浏览器时记录精确未跑条件；不要把 mock、语法检查、skip 或旧 run 当成新功能绿灯。最终报告至少包含准确 head/tree、命令/run/attempt、实际通过/失败/skip、二进制来源、资源清理、独审结论与未完成项。新代码提交后及时更新本表，不抹去先前失败。

## 标准客户端 UDP 历史恢复点（2026-10-18）

已从准确 master 1489bf5218fced9b42c57e06715478dc323dc782 新建独立 experiment/standard-client-udp-20261018；[有界设计](../experiments/standard_client_udp/DESIGN.md)已独审，适配器仍是未验收 WIP。当前没有新增 UDP 通过结论。云执行器在首轮纯控制测试启动时断联；此前本地官方核心启动遭 netlink EPERM，零业务 UDP，不能解释为协议失败。恢复文本、未执行验证和下一步见[恢复记录](../experiments/standard_client_udp/RECOVERY.md)。

继续顺序：先对恢复后的准确源码做语法/18项计划控制与独审，补严格证据归档和第一/第二跳负控；空白 CI runner 分开执行产品 relay UDP 拒绝、全 stock 两跳、候选服务器 owner-off C/D 和官方海外 B 组。C 为26 case/66 leaf，D为2/4，B为1/4；容量失败不改成预期通过，60000前置失败时释放窗/恢复未执行。保持2s配置及完整6s观察窗、原应用对象/tuple、一次发送无重放、清理与普通导出字段。实验仍不进入产品、不上传实验二进制、不要求自研手机 App、不发布或部署。

## 标准客户端 UDP 当前结果与接续

上段为云断联时的历史 WIP，不再是当前执行进度。研究源码、严格文本归档、单次发送与原始 tuple/payload 验证、source/binary 身份、完整 FD/进程清理及有界 coordinator 已实现并独审。准确 `8089b3f94e5fa599943bd9d9456b0c92cf28ddfe` 的 [run39167829699 attempt1](https://github.com/ForceMind/V-UI/actions/runs/39167829699)已实跑产品负控、全 stock C/D、候选服务器 C/D、官方海外 B；详细计数、原始证据摘要与未执行叶见[研究记录](STANDARD_CLIENT_UDP_RESULTS_20261018.md)。

首轮产品 10/10通过；stock C/D红；candidate C有37次完整往返、1次本地EMSGSIZE及13叶容量失败；B两大下行各16114前缀。所有 D 的60000前置失败，生命周期窗/恢复均未执行。candidate-auth 子进程退出0但敏感文本归档拒绝，未上传材料，不授负控资格。后续准确 `fa96996b8fa1f92819c4fb0fd97ce047e8a8521d` 仅修研究日志脱敏/完整行绑定，31项控制独审通过，并以[run39169374994 attempt1](https://github.com/ForceMind/V-UI/actions/runs/39169374994)重新运行并已终态：产品10/10通过，stock/candidate/B容量仍红；candidate-auth新归档完整可核，3方法/7故障子场景全部通过，22.055秒。六个新文本归档均已保存，旧失败保留，结果分提交核验。

接续仍在隔离实验分支：先明确普通现成客户端实际入口、MTU内负载和服务端最小必要改动，再独立验证手机导入/TUN/公网与生命周期。不能把固定 Linux Mihomo SOCKS 入口的大包失败外推所有手机，不能为了极大包门槛直接要求自研 App，也不能以小包替代原完整容量/2s释放门槛。当前产品每入口 TCP 与既有普通 direct 能力保持；relay UDP继续拒绝，八补丁仍不进产品，暂无发布/部署或二进制分发。

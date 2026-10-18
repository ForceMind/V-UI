# 当前开发与接手入口

更新：2026-10-18。本文是项目计划和交接入口；“已记录计划”不表示功能已实现或验收通过。

## 先做什么

1. 阅读 [开发约束](../AGENTS.md) 和 [每入口路由契约](PER_INBOUND_ROUTING_CONTRACT.md)。
2. 从准确产品主线 `a98da9afbbf58a54f4f102cccec2f26b6e07d2ee` 的独立 `feature/per-inbound-routing-20261018` 分支继续。先核远端默认分支 **master** 与工作树，不覆盖其他人的变更。
3. 本轮目标：同一 V-UI 实例中，现成客户端导入不同普通 VLESS 节点；节点分别“跟随全局 / 指定直连 / 指定中转”。先完成方案独审，再实现数据、事务、API、UI、导出和 TCP 验收。
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
| D1 TCP 上游专用导出 | `a98da9afbbf58a54f4f102cccec2f26b6e07d2ee`，[PR47](https://github.com/ForceMind/V-UI/pull/47) | 当前产品基线；标准设备导出与“供另一 V-UI 导入的上游导出”用途不同 |
| 每入口三态 TCP | `feature/per-inbound-routing-20261018`，基于 a98 | 当前优先工作。P0 已独审并公开备份为 `6a89a20652beffbfe32cf43deb2aa16cd20bf9ec`；P1–P3 已有集成 WIP，真实链路和完整验收仍待完成 |
| D2 XUDP/大包/owner 实验 | `afbdba100c5f0e3dc6888684fdbab31980720df3`，tree `996d37d925be8edd18cbf921d3cc580f43c0730d` | 独立实验部分合同通过，整体仍失败；未并入产品，见下节 |
| 标准客户端 UDP 补验 | [兼容边界及最小计划](STANDARD_CLIENT_COMPATIBILITY.md) | 尚未执行新的 stock Mihomo 大包/同应用关联恢复组；不阻塞本轮 TCP 产品功能 |
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
| P0 | 本入口、设计契约、精确调用点与兼容纠正 | 已独审并公开记录；不是功能通过 | 无未决的保护策略降级，独审可据文档接续 |
| P1 | 严格三态字段、旧库迁移、统一有效策略、身份兼容 | 已集成首版，23 新增 + 26 原 compiler 控制通过；待跨层全量验收 | 旧全 inherit 配置/身份保持；非法/null 拒绝；省略更新保留 |
| P2 | apply/rollback/启动/恢复/备份/删除保护 | 双向 fence 与离线依赖闭包修复已集成；宽 proxy 403 项中 353 通过/50 skip，最终全量待验；installed policy/recovery 组件门槛通过 | 双全局 off + explicit relay 仍受保护；任何不一致不退普通直连 |
| P3 | API/UI/导出三用途与 desired/applied 标识 | 已集成；29 项 API/export、8 项完整 coordinator API 控制、17 项前端控制通过；真实浏览器待 CI | 标准客户端只获入口信息；取消/刷新/失败无假生效/秘密泄露 |
| P4 | stock Mihomo 同实例两个节点和 host 四组合 | 已实现 7 项夹具控制 + 3 个真实方法；7 项控制通过，本地真实首启受 netlink EPERM 阻断，待准确 CI | 真实目标分别观察本机/海外出口；负向原因、零误送达、无回退；四状态/12 切换 |
| P5 | 最终候选、独审、八组、四目标、正常合并后重验 | 待执行 | 准确 SHA/tree、所有必需 job/step 终态，历史失败和 skip 单列 |

## 本阶段 WIP 与失败记录

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
- 当前纯测试分别只证明对应组件。全套、真实核心、浏览器、恢复/installed smoke 与同提交四目标未完成，不能合并或声明功能收口。
- 首次指定直连可保持两个开关关闭、**不选择上游**，正常 check/apply 建立活动配置；保存期间没有活动配置时显示待应用/不可确认。指定中转才需要先应用合法的已选上游；不能把普通直连变成必须配置海外。

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
python scripts/check_docs.py
python -m compileall -q app tests scripts main.py
node --check web/js/app.js
node --check web/js/proxy.js
python -m unittest discover -s tests -p 'test_proxy*.py' -v
python -m unittest discover -s tests -p 'test_inbound*.py' -v
python -m unittest discover -s tests -v
```

真实核心/浏览器的可执行命令以 `.github/workflows/loopback.yml`、`test.yml` 和 [贡献指南](../CONTRIBUTING.md)为准：`scripts/fetch_test_cores.py` / `fetch_mihomo.py` 校验固定来源，`VUI_PROXY_LOOPBACK=1`、`VUI_TEST_CORES`、`VUI_TEST_MIHOMO` 激活真实 proxy 测试；`VUI_PROXY_BROWSER=1` 与固定前端资产激活浏览器。新增每入口真实步骤使用 `VUI_INBOUND_ROUTING_LOOPBACK=1` 执行 `test_inbound_routing_real.py`；配置解析用 `VUI_TEST_CORES` 执行 `test_inbound_routing_parser.py`。新增实际浏览器流程以 `VUI_INBOUND_ROUTING_BROWSER=1` 执行 `test_inbound_routing_browser.py`，已实现并接入 CI，3 项纯控制通过、1 个真实流程待 CI，不能仅因默认 discover skip 便计通过。

没有 Python/核心/浏览器时记录精确未跑条件；不要把 mock、语法检查、skip 或旧 run 当成新功能绿灯。最终报告至少包含准确 head/tree、命令/run/attempt、实际通过/失败/skip、二进制来源、资源清理、独审结论与未完成项。新代码提交后及时更新本表，不抹去先前失败。

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
| 每入口三态 TCP | `feature/per-inbound-routing-20261018`，基于 a98 | 当前优先工作。设计审查中；数据/API/UI/事务/真实链路尚未实现或验收 |
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
| P0 | 本入口、设计契约、精确调用点与兼容纠正 | 文档编写/独审中 | 无未决的保护策略降级，独审可据文档接续 |
| P1 | 严格三态字段、旧库迁移、统一有效策略、身份兼容 | 待实现 | 旧全 inherit 配置/身份保持；非法/null 拒绝；省略更新保留 |
| P2 | apply/rollback/启动/恢复/备份/删除保护 | 待实现 | 双全局 off + explicit relay 仍受保护；任何不一致不退普通直连 |
| P3 | API/UI/导出三用途与 desired/applied 标识 | 待实现 | 标准客户端只获入口信息；取消/刷新/失败无假生效/秘密泄露 |
| P4 | stock Mihomo 同实例两个节点和 host 四组合 | 待实现 | 真实目标分别观察本机/海外出口；负向原因、零误送达、无回退；四状态/12 切换 |
| P5 | 最终候选、独审、八组、四目标、正常合并后重验 | 待执行 | 准确 SHA/tree、所有必需 job/step 终态，历史失败和 skip 单列 |

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

真实核心/浏览器的可执行命令以 `.github/workflows/loopback.yml`、`test.yml` 和 [贡献指南](../CONTRIBUTING.md)为准：`scripts/fetch_test_cores.py` / `fetch_mihomo.py` 校验固定来源，`VUI_PROXY_LOOPBACK=1`、`VUI_TEST_CORES`、`VUI_TEST_MIHOMO` 激活真实 proxy 测试；`VUI_PROXY_BROWSER=1` 与固定前端资产激活浏览器。新增每入口测试必须接入相应真实步骤，而非仅被默认 discover 跳过。

没有 Python/核心/浏览器时记录精确未跑条件；不要把 mock、语法检查、skip 或旧 run 当成新功能绿灯。最终报告至少包含准确 head/tree、命令/run/attempt、实际通过/失败/skip、二进制来源、资源清理、独审结论与未完成项。新代码提交后及时更新本表，不抹去先前失败。

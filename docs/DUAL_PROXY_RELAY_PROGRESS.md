# 双开关代理与中转进度及证据账本

更新日期：2026-10-15。契约：[DUAL_PROXY_RELAY_CONTRACT.md](DUAL_PROXY_RELAY_CONTRACT.md)。计划基线：`a9f9b5d9c0dd2aada5e8f5f2270b2ce93440d5ff`。

## 当前结论

M0 已正常合并 [PR #40](https://github.com/ForceMind/V-UI/pull/40)：候选 `2165eb71ae3de95ad177ba759c77cbe9095dfca4`，最终 master `a8a5b5890f283ac1c968b4b0a6048b03c170498b`，tree 同为 `0b4592a188cf6d8dc761782065c22e026ec71aab`。候选与主线各八组 / 11 jobs / 最新全部步骤 attempt 1 成功，实际 checkout 证据见下方补充。

M1 [Draft PR #41](https://github.com/ForceMind/V-UI/pull/41) 最新候选 `a7b0d22e9d7f606595af75a4d0418200abd61a5a`，tree `3530a98308d2e22484cf6e1d6d925bf6e37d0beb`。先前候选 `0e26555` 八组成功，但发现重复物理 If-Match 请求头未拒绝，已修复并重新验收，不能沿用旧成功。新 tree 本地完整 1,377 项，1,250 pass / 127 skip（129.837 秒）；截至 18:12 UTC 新 CI 七组成功，deployment 尚在运行，不提前登记合并。

M2 来源路由、运行事务和安全修订已实现，准备独立准确候选；见[实现说明](DUAL_PROXY_RELAY_M2.md)。M3 面板/诊断并行开发，M2–M4 均未取得整体资格。M1 保存草稿不会启用代理；M2 候选的应用接口只有通过真实门槛后才可随该阶段合并交付。

已批准按双开关方案推进，M0–M4 各自独立 PR。下表仅按已取得证据更新，目标不等于完成功能。

本轮不改变产品版本或固定核心，不创建 tag/Release、不替换历史资产、不部署真实服务器。已有发布/协议/安装器历史保持原记录；它们不是新功能的准确提交验收。

## 阶段状态

| 阶段 | 当前状态 | 退出门槛 / 下一证据 |
| --- | --- | --- |
| M0 合同冻结 | 已合并；候选/主线各八组完成 | 字段/API/四状态/拒绝边界审查；文档检查；独立 PR、准确候选与主线证据 |
| M1 导入与数据层 | PR #41 修订候选验收中 | 三格式/两类上游、隔离解析限额、秘密、迁移、版本冲突；不开放应用 |
| M2 双开关运行与事务 | 本地实现与独立审查，真实 CI 未完成 | 四状态/12 切换、已冻结 DNS/禁止地址目录的真实检查、入口全覆盖门禁、安全回滚、真实核心 |
| M3 面板诊断与导出 | 未验收 | 完整浏览器生命周期、诊断 SSRF/限额、无秘密、普通导出与严格中转并列 |
| M4 整体可发布候选 | 未验收 | 独立审查、准确候选八组、正常合并、准确主线八组、同提交四目标新资产 |

“待完成”不代表尚无工作树实验；只有下面登记的准确证据才能改变相应资格。中间阶段不得先开放尚无证据的运行接口。

## 准确提交证据索引

| 项目 | 当前记录 |
| --- | --- |
| 规划基线 | `a9f9b5d9c0dd2aada5e8f5f2270b2ce93440d5ff`，只作起点 |
| M0 独立 PR / 审查 | [#40](https://github.com/ForceMind/V-UI/pull/40)，无剩余审查阻断 |
| M0 最终候选 SHA / tree | `2165eb71ae3de95ad177ba759c77cbe9095dfca4` / `0b4592a188cf6d8dc761782065c22e026ec71aab` |
| M0 本地检查 | 2026-10-15 未提交工作树：`python scripts/check_docs.py`、`git diff --check` 通过；不是准确提交 CI |
| M0 候选八组 CI | 2026-10-15 17:33 UTC：7 success，deployment 运行中；不是八组完成 |
| M0 正常 merge / master SHA / tree | 未登记 |
| M0 主线八组 CI | 未登记 |
| M1–M4 后续 PR/证据 | 按阶段追加，不覆盖先前失败 |
| 新功能同提交四目标制品 | 未登记 |
| 新功能真实用户线路 / 外部客户端 | 未测试，不能用 CI loopback 代替 |
| 真实 1 vCPU / 512 MiB / 24 小时 | 未验收，不由新增单测补发资格 |
| 新功能发布 / 生产部署 | 未执行；独立于代码合并 |

## 记录规则

每次检查保留：阶段与场景编号、准确 SHA/tree（未提交工作树须明确）、命令或 workflow/run/job 链接、attempt、开始/结束时间、实际 pass/fail/skip/未运行、脱敏证据位置、失败原因和下一步。配置语法、真实二进制、真实链路、浏览器、root/systemd、四目标资产和真实部署各列各证据，不用一个“全绿”合并。

A01 的 12 条切换必须逐条有实际监听/两个出口/活动 revision 证据；A04/A05 必须能关联入口、上游和目标，同时保留错误认证/TLS/REALITY 原因、目标零送达和无 direct。无外部观察者时外部完整链路继续未测试。超时、listener ready、TCP connect 或配置解析不能替代认证及业务证据。

准确候选通过后任何代码/测试/文档变更都产生新候选；正常合并后检查新的 master，记录 tree 是否相同。最新 job/step、重跑及其原失败均保留。四目标包必须来自最终同一提交，检查实际组件消费，不复用旧资产。公开制品不得含测试 CA、凭据、数据库或未脱敏日志。

## 失败与缺口登记

本账本初始化时尚无新功能运行结果。规划基线 `a9f9b5d9` 的既有套件预检由开发协调方记录为 1,245 项、1,118 通过 / 127 明确 skip；固定 Xray 26.3.27 与 sing-box 1.14.2 的一个既有正/负配置检查通过。这些是基线预检，不是 M0 准确候选证据，也不是双跳功能证据。后续失败按时间追加原判，不删除旧失败、不把 unchanged-code 重跑写成永久修复；环境权限/网络/平台阻断、测试未启用与产品缺陷分别说明。

DNS resolver、禁止地址 CIDR、bootstrap 地址固定策略及诊断服务目录已在 M0 契约中具体冻结；M2/M3 仍须分别验证这些常量在固定核心中的实际行为，以及四目标解析资源限制。在必需真实门槛完成之前，不开放相应代理应用/诊断；不能以系统 DNS/direct 兜底消除阻断。


## 2026-10-15 18:12 UTC 证据追加

上方 17:33 的旧快照保留为历史，不代表当前状态。

- M0 候选八组：one-click [38776661667](https://github.com/ForceMind/V-UI/actions/runs/38776661667)、ACME [38776661747](https://github.com/ForceMind/V-UI/actions/runs/38776661747)、portable [38776661724](https://github.com/ForceMind/V-UI/actions/runs/38776661724)、docs [38776661611](https://github.com/ForceMind/V-UI/actions/runs/38776661611)、ToClash [38776661760](https://github.com/ForceMind/V-UI/actions/runs/38776661760)、test [38776661619](https://github.com/ForceMind/V-UI/actions/runs/38776661619)、loopback [38776661664](https://github.com/ForceMind/V-UI/actions/runs/38776661664)、deployment [38776661718](https://github.com/ForceMind/V-UI/actions/runs/38776661718)。八组 / 11 jobs / 步骤 attempt 1 全成功。
- M0 实际 checkout：原部分 PR jobs checkout `3b4bd54dea7d640516f0da9579bb0d4eeb501211`，其 tree 与候选完全相同，均为 `0b4592a188cf6d8dc761782065c22e026ec71aab`；其余包/安装 jobs 直接 checkout 候选。已逐 job 核日志中的 `git log -1 --format=%H`。ToClash 第二个 checkout 是其另一个固定参考仓库，不是 V-UI 提交。
- M0 master 八组：loopback [38778990100](https://github.com/ForceMind/V-UI/actions/runs/38778990100)、one-click [38778989917](https://github.com/ForceMind/V-UI/actions/runs/38778989917)、deployment [38778989970](https://github.com/ForceMind/V-UI/actions/runs/38778989970)、ACME [38778990027](https://github.com/ForceMind/V-UI/actions/runs/38778990027)、test [38778990033](https://github.com/ForceMind/V-UI/actions/runs/38778990033)、ToClash [38778990000](https://github.com/ForceMind/V-UI/actions/runs/38778990000)、docs [38778990063](https://github.com/ForceMind/V-UI/actions/runs/38778990063)、portable [38778989920](https://github.com/ForceMind/V-UI/actions/runs/38778989920)。准确主线八组 / 11 jobs / 步骤 attempt 1 全成功，deployment 最后于 17:59 UTC 完成。
- M1 新候选原默认 checkout 的实际 SHA 为 `6161326d45ad7787b49d3a035714fc29e0bca004`，tree `3530a98308d2e22484cf6e1d6d925bf6e37d0beb` 与新候选相同。六个已完成组的 job checkout 日志已核，包/安装组直接使用候选 head；最终仍须等 deployment 及全部 job/step 终态。M2 起将主 checkout 明确固定 head，合并后继续核 master。
- M2 首次本地冻结 `8b4b793` 未推送：独立审查发现并修复底层提前回滚、运行文件路径安全、容量耗尽语义、重复物理请求头和负向测试晚到请求记账问题；这些修复会产生新候选。容量 API 初版专项遗漏草稿 catch 的一项失败保留，修复后 draft/runtime API 21 项通过；额外真实私有 store + SQLite 变更后的容量耗尽测试确认旧运行仍 ON、状态字节未变。
- 本地真实核心启动仍因 netlink 权限失败，Chromium 启动受 socket 权限失败；原结果保留。固定 parser、模拟事务和静态/UI route 测试通过均不替代真实链路/浏览器 CI。Xray 首次 fd 路径缺少扩展名的 parser 失败由显式 JSON 格式修复；相对 TLS 路径按原 Xray binary-dir 语义验证，未改变生产解析行为。

## 2026-10-15 18:28 UTC 首次 M2 准确 CI

- M2 [Draft PR #42](https://github.com/ForceMind/V-UI/pull/42) 首候选 `02e7f5ba9945a8fba5c6dfa0dc12cee1c9ba387c`，tree `a5babce883fc435bd4f1c6330a4035a878b9a0d8`，与本地 `b705899` 相同。准确源码完整本地 1,575 项：1,427 pass / 148 明确 skip，135.657 秒。固定 parser 两项独立通过，包含四状态/两类上游和两核心的正向与明确 parser 拒绝原因。
- one-click [38784477152](https://github.com/ForceMind/V-UI/actions/runs/38784477152) / job `116592815486` 失败：升级时已有代理状态触发精简控制器缺少 `app.services`；根因属于 M1 备份依赖闭包。M1 虽八组已成功仍暂停合并，必须先修复并重新验收。不是防火墙修改请求，也不以清除代理状态解决。
- loopback [38784477040](https://github.com/ForceMind/V-UI/actions/runs/38784477040) / job `116592814917`：既有 101 项、独立 HY2 8 / TUIC 8 通过；M2 policy real 4 项通过。double-hop 23 方法中 12 个真实方法和 10 个 fixture 方法通过，一个 DNS 故障方法造成 4 次 failure；整组失败。真实事务 4 项因前一步失败未运行。
- 该 DNS 故障首因是固定核心实际原因 `servfail` 与测试期待 `server failure` 不同。旧 helper 在等待匹配原因时重复发请求，造成数百查询；失败阶段停核心后，subTest 又继续制造无新日志的 timeout 失败。修订为每次负例只发一次，随后仅轮询新日志；必须匹配准确业务名字、DNS lookup 和真实两族 SERVFAIL/deadline 语义，不接受泛超时。三个新增 fixture 回归验证一次请求、旧日志不可用、认证/业务超时不能代替 DNS 或认证原因；13 fixture 通过，真实重跑仍待新候选。保留原 4 次失败与计数证据。
- 此次通过的 TLS/REALITY 四状态/12 切换及两客户端方法只属于上述首候选的局部结果，不能在整组失败时宣布 M2 完成。新源码需全部重跑，实际用户海外/外部设备仍未测。
## M1 精简控制器依赖修复（2026-10-15）

M1 原候选 `a7b0d22e9d7f606595af75a4d0418200abd61a5a` 的八组 CI 已成功，但 M2 首次实际安装升级暴露了覆盖缺口：只提取 `deploy.py`/`release_tools.py` 的离线控制器，在已有代理状态时备份因缺失 `app.services.proxy_store` 失败。保留 [one-click run 38784477152](https://github.com/ForceMind/V-UI/actions/runs/38784477152)；旧绿灯不能证明新修复合格。

修复从同一完整校验的 bundle/source ZIP 提取 `proxy_store.py` 与 `upstream_profile.py`，不读可变已安装 payload，不新增公开顶层资产。受管 worker 在 `-I -S` 下只绑定固定可信兄弟文件；控制 receipt 必须覆盖新依赖。旧精确 receipt 只允许显式 refresh 并校验旧文件完整性，不接纳未登记的额外依赖。安装器四文件闭包同样用隔离 Python，提取和校验发生在停止服务之前。

新增真实非 root、隔离子进程备份/恢复检查已提交 ON/ON 假代理状态、上游与本机密码一致性、旧节点及撤销旧会话/订阅；保留旧两文件缺陷的预期 RED 再现。缺失/篡改依赖、恶意 cwd/PYTHONPATH/site、受管 worker 缺失兄弟模块均必须拒绝且不改状态。实际 systemd 升级门槛增加保存两个开关 ON 草稿后再升级并逐字比较安全快照，不启用代理或修改测试主机网络。精简包/控制器/安装和恢复分别验证，不以完整源码单测代替。

此追加为修复候选说明，准确修复 SHA、本地全套、独立审查与新八组 CI 结果以后续候选/PR 记录为准；未提前声称通过。

修复本地首冻 `1e0c2c9` 全套 1,386 项中出现 6 个旧合成归档夹具错误（127 明确 skip）：5 个流式归档、1 个发布 kit 构造夹具缺少新闭包；实际控制器专项 172 通过 / 1 真实 systemd skip。同步这几个夹具及独立归档内存 benchmark 的依赖集合，不放宽生产成员校验。原失败日志保留；新候选须重跑。

首次 M2 八组现已全部终态：6 success / 2 failure，全部 attempt 1；deployment 的末项实际 bundle 低资源 cgroup 门槛成功不改变上述两个失败。M1 新闭包修复和 M2 DNS 单次真实请求证据修复均须随新候选重跑。

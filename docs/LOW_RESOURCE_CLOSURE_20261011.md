# 低资源软件阶段收口（2026-10-11）

[PR #28](https://github.com/ForceMind/V-UI/pull/28) 的本轮软件修复、受限回归及证据补全已收口。
**真实 1 vCPU / 512 MiB 整机与连续 24 小时完整负载仍未验收**；大数据用例已触及
512 MiB 上限，不能宣称小型 VPS 已达标。当前结论及原始证据入口见
[公开核验记录](https://github.com/ForceMind/V-UI/pull/28#issuecomment-6109281431)。

## 已完成与仍待验证

- [x] 流式完整读取、私有文件缓存生命周期及恢复保护的软件实现与回归；不减少摘要、成员、路径、权限或数据保全检查。
- [x] `0f6c3e60fc51296bb279838443e03ba656d7e725` / tree
  `b74910c1110861f0fae121cb165aeab67adea037` 的普通八组、11 jobs、119 steps
  全部 attempt 1 成功；原始源码包 310 个文件逐 blob 核验，四个实际目标包各
  180 个 manifest 文件、37 个锁定 wheels 均核验。全量发现 908 项，783 通过、
  125 环境 skip；skip 不算相应真实场景通过。
- [x] 准确 `a373d4770263a0d464a662362081999c2ff4d5a1` 的受限 root 安装、
  同产品新目录升级及恢复通过，见下。
- [x] 准确 `0f6c3e6` 的大数据完整 45 阶段、原三处备份边界及新增九处生命周期边界通过，见下。
- [ ] 真实 1 vCPU / 512 MiB 整机验收需有获明确授权且为空的非生产测试目标：记录 OS 基线、完整进程树、
  可用余量及连续 24 小时完整工作负载。它是剩余外部资格验收，尚未完成。
- [ ] 160 MiB 完整空载与 320 MiB 安装/升级工程预算仍未达证；这些是
  [初始工程目标](LOW_RESOURCE_TARGET.md)，不是已兑现承诺或新加的硬门槛。

没有要求新租机器、提供凭据或使用生产机。PR 保持 Draft，产品 `VERSION=0.4.7`；
合成 release_id 仅用于同产品新目录验收，不构成新版本发布。人工合并、tag、Release、
附件晋升与部署边界不变，本收口不执行这些动作。

## Root：准确 a373 的成功与覆盖边界

源提交 `a373d4770263a0d464a662362081999c2ff4d5a1` / tree
`1bdd283168d22b644d6b234b41365160d10e2027`，
[run 38138976883 / job 114468681304](https://github.com/ForceMind/V-UI/actions/runs/38138976883/job/114468681304)
attempt 1 成功；[原始 artifact 11702023918](https://github.com/ForceMind/V-UI/actions/runs/38138976883/artifacts/11702023918)
ZIP SHA-256 `a37b63e4ccef2de28af148ecafebc43982e0b87511438df5a9736db63ea56f5a`。
准确源的完整 root/trace validators 及独立原件审计均通过。

父组 1 CPU、512 MiB、零 swap；峰值 **494,104,576 bytes = 471.21484375 MiB**，
名义余量 40.78515625 MiB，max/oom/oom_kill/oom_group_kill 全零。
九阶段、四份 root 入口收据、46 份 schema-3 样本及完整清理通过。
构建、协调器、PID1/socket 与宿主 OS 开销在父组外，不能把名义余量当整机余量。

本次实际没有 managed-root launch 样本，也没有 `service_transitions` 记录；
它没有现场复现 `886832f` 的停启竞态。停止方向修正另由严格反例和保留的原始观察
回放证明；原 `afce914`、`886832f` 完整验收仍为失败，不被改判。
[Root 契约与失败原文](LOW_RESOURCE_ROOT_INSTALL.md)、
[a373 核验记录](https://github.com/ForceMind/V-UI/pull/28#issuecomment-6105096999)保留。
本次通过亦不证明私有归档冷读单独导致峰值下降或形成稳定改善。

## 大数据：准确 0f6c 的功能通过与零余量

[run 38142196153 / job 114478172329](https://github.com/ForceMind/V-UI/actions/runs/38142196153/job/114478172329)
在上述准确 `0f6c3e6` attempt 1 成功；
[原始 artifact 11703287845](https://github.com/ForceMind/V-UI/actions/runs/38142196153/artifacts/11703287845)
ZIP SHA-256 `c3e4fc95f7cf6523abb028b636bced11bf7ed7a3da78503b7084a8e5471dd8f5`。
完整 strict validator 和独立原件审计通过，无前缀验收替代。
1000 个合成节点、256 MiB 非稀疏日志及 64 个小文件的原 45 阶段、恢复/保旧/撤权/
完整哈希与清理均保留，新增九处观察没有改变产品操作或减少验证。

服务组 1 CPU、512 MiB、零 swap；终态累计峰值 **536,870,912 bytes = 512 MiB**，
`memory.events.max=883`，三类 OOM 全零。功能与恢复通过，但名义 512 MiB 余量为零，
存在限额压力，不能称低压力、产品优化成功或整机达标。

新增端点的 max 增量分别为：原未分阶段的快照 +8、已安装程序坏摘要拒绝 +158、
随后保全全量哈希 +0、已安装程序恢复 +348、整体恢复验证 +291。
这是包含同组观察/检查点的累计端点差，不是单个读操作的独占因果或阶段私有峰值；
整体恢复验证同时读恢复后数据与保留旧数据，不能再拆分归因。
九行紧凑 JSON 列表 19,429 bytes，最大行 2,191 bytes；仅快照自身 CPU
4.045711 ms、wall 4.084463 ms，不含验证、序列化与 checkpoint，后者仍全部同组计费。
据此不扩大对任意调用者归档或用户数据的缓存提示权限，不新增无证据的产品修复；
原有 1 MiB 有界完整读取及全部安全/恢复检查继续保留。

历史 [run 37956945661](https://github.com/ForceMind/V-UI/actions/runs/37956945661)
属于 `5f44a2f`：attempt 1 因上游 API HTTP 403 未运行工作负载；attempt 2 完整通过，
峰值 512 MiB、max 798、OOM 0。不同运行、测量补全和中间 runtime 提取修改，
不能组成配对优化对照，也不抹去首次失败。

## 证据继承边界

从 a373 到 0f6c，仅两份大数据测量脚本及两份测试文件变化；产品、root collector、
root validator 与 root workflow 未变。`app/release_tools.py` 保持 blob
`d3cb78d1045dc70ac070e126de27b73eda612e52`。这允许明确绑定同一产品字节的证据，
**不把 a373 root 运行改称 0f6c root 重验**。本文后续的纯文档提交同样不代表在新 head
重新执行过 root 或大数据；普通 CI 与实际包仍须按各自准确提交单独核验。

历史[四平台/90 分钟基线](LOW_RESOURCE_071_BASELINE.md)、
[UI/证书等逐提交记录](LOW_RESOURCE_TARGET.md)、[双核心](LOW_RESOURCE_DUAL_CORE.md)与
[CPU 压力恢复](LOW_RESOURCE_CPU_PRESSURE.md)仍各属自己的 head。
不把它们合并成 0f6c 或本文提交的完整同提交矩阵，也不以 CI 容器或多次短测拼成整机/24 小时验收。

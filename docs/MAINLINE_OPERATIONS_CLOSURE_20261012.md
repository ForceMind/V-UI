# 0.4.7 集成产品代码主线收口（2026-10-12）

## 固定代码基线与文档边界

[PR #35](https://github.com/ForceMind/V-UI/pull/35) 已按普通 merge 合入签名主线 **`02d8038d5c06301cd81fb5ee1c776ce53b8bd4b7`**，父提交为原主线 `a6aa84dc5f66b86ac82213dd0b7323d1b6ccfc60` 与候选 `bd0a122685113dc6f35dd32e5c773e957d8740e2`。没有 squash、rebase 或强推。代码树 **`4f0d20027bdbda02c73f03c4d76d7425a7c650ea`** 与已验候选相同，但以下主线结果来自重新运行的 push CI 和主线新制品，并非继承候选成功。

这里的 `02d8038` 是固定的**产品代码集成基线**，不表示它永久等于 default HEAD。后续这轮收口只改文档；承载文档的候选、正常合并后的准确主线 CI 和同提交附件，以对应 PR 验收账本为准。本文不自填自身未来 SHA，不因记录最终结果再追加一轮源码提交。

PR #28–34 的原头均为该基线的祖先。合并后的检查中，GitHub 自动将目标为 master 的 PR #28/#29/#30/#34 标为 merged；PR #31/#32/#33 的目标仍是各自 stack 分支，保持 open。**代码历史已进入主线，不等于所有旧 PR 自身都显示 merged**，不需要重复合并它们。

`VERSION` 仍为 **0.4.7**。本次只完成代码整合和验收，没有创建 tag、Draft Release 或公开 Release，没有向 Release 上传/晋升资产，也没有在用户主机安装。正式发布仍遵循[发布流程](RELEASING.md)。

## 本次八个软件工作包

下列八项是本轮指定的软件目标，不能据此声称整个 ROADMAP 已完成。

| 工作包 | 实现与消费证据 | 保留的资格边界 |
| --- | --- | --- |
| 网页后台 | 认证、节点、路由、证书及安全中心只读说明；源码与安装包 Chromium 分别通过 | 不支持的封禁动作不调用 501，不新增防火墙/SSH 控制 |
| 包内服务用户菜单 | 状态、安装记录、显式完整校验、可信本地候选准备；真实包内 PTY/worker 消费 | 不自动提权；准备/READY 不等于升级完成 |
| 全局 `v-ui` 入口 | 完整可信 kit 的 root-owned 控制源码、固定 OS Python 与降权 worker | 需管理员[显式安装](MANAGED_OPERATIONS.md)，默认 install.sh 不添加；实际 root 资格只属下述 f509 |
| systemd 服务控制 | 状态、启停、重启、socket 停止及 cgroup 静止检查；软件反例与包内组件测试 | 普通包消费的 root/init 状态是模拟；OpenRC 写动作禁用 |
| 候选切换/代码回退 | 共享操作锁、停服备份、完整校验、健康检查及 CURRENT 选择 | 失败留停/状态未确认；同代码 A→B→A 不证明跨版本/schema 迁移 |
| 备份/恢复/中断恢复 | 实际归档、SQLite、两代 writer 锁、撤权和恢复 journal 消费 | 构造中断不证明真实断电；未知状态不能自动恢复启动 |
| 低资源软件修复 | 流式完整校验、私有缓存生命周期、GNU allocator 与状态轮询；本主线短 cgroup 三档通过 | 大数据 512 MiB 零余量历史保留；整机/24h 与 160/320 MiB 工程预算未获资格 |
| 来源、质量与交付材料 | VMess 新日志原因、HTTPUpgrade 有界记录/排空、准确源码/四目标包/完整 kit | 超时不能代替拒绝原因；候选包不能代替最终主线包；公开发布未发生 |

原 f509 阶段的八行矩阵及失败保护细节保留在[完成矩阵](INTEGRATED_OPERATIONS_CANDIDATE.md)，本次准确普通 CI/包证据如下。

## 准确主线普通 CI

八组 push 工作流、11 个 jobs、119 个 steps 均在 **原始 attempt 1 成功**，没有 CI 重跑。

| 工作流 | 准确主线 run |
| --- | --- |
| Test / 源码与浏览器 | [38234112651](https://github.com/ForceMind/V-UI/actions/runs/38234112651) |
| 真实链路 | [38234112640](https://github.com/ForceMind/V-UI/actions/runs/38234112640) |
| 一键安装 | [38234112586](https://github.com/ForceMind/V-UI/actions/runs/38234112586) |
| 四目标 Linux | [38234112631](https://github.com/ForceMind/V-UI/actions/runs/38234112631) |
| 安装包部署门槛 | [38234112670](https://github.com/ForceMind/V-UI/actions/runs/38234112670) |
| ACME | [38234112827](https://github.com/ForceMind/V-UI/actions/runs/38234112827) |
| ToClash | [38234112677](https://github.com/ForceMind/V-UI/actions/runs/38234112677) |
| 文档与发布合同 | [38234112721](https://github.com/ForceMind/V-UI/actions/runs/38234112721) |

- 默认源码共 1199 项：1072 通过、127 明确环境 skip；32 项新 recorder 回归实际执行。86 项聚焦证据合同回归通过，包含 VMess/通用 TLS 的 27 项方法。
- 真实链路 101 项，另行 HY2 8 项、TUIC 8 项，均无 skip；四个 VMess CA/SNI 路径取得真实 x509 与具体原因。
- 两次 HTTPUpgrade recorder 调用分别观察原生 GET Upgrade 与原样 URI 的 raw VLESS；四次观察均 attempts=1、accepted=1、active=0、sealed/drained，应用送达为零。真实固定服务端的同字节验证 TLS replay 得到 HTTP/1.1 400 Bad Request 及指定正文；canonical 转发、原样 URI 零送达/无 DIRECT、普通 TCP 错误传输对照分别通过。
- 原件保留执行结果和来源；没有把它说成逐份原始载荷抓包文件。真实 bytes 与同字节 replay 由已核验的准确源码在运行中断言。一轮成功也不保证未来永不出现时序问题。
- ToClash 100 个参考案例/40 个预设通过；源码 Chromium 与安装包 HTTPS/Vue/恢复的 Chromium 分别通过。

## 主线新制品与实际消费者

12 个原始 Actions artifact 均按官方 SHA-256、字节数、准确 master/run/attempt 核验。原始 oneclick kit 有 16 个成员；kit 与 test 的源码包都指向本准确主线，并重建 337 个 Git blobs/模式及上述 tree。五个新构建包覆盖四个目标（含一份独立 deployment 构建），每包 188 个清单成员/模式、37 个 wheels、运行时 pin 和官方核心来源/二进制摘要均核过。

与 bd0a 候选相比，源码 337 个 blobs/模式相同；各包只有生成的 MANIFEST.json 中 source_commit 与 release_id 改变，188 个 payload 成员/模式同字节；7 个受信控制源码/入口同字节，CODE_ASSETS 中的 RELEASE.json 随来源提交更新。这一比较不替代主线自己的运行验收。

五个新包消费者通过。其控制器/worker、Python、SQLite、flock 和 PTY 为真实非 root 执行，root 信任配置及服务/cgroup 状态为模拟；普通 oneclick 在临时 CI 上的实际安装另有上述 run，不等于新受管控制层的 root 资格。

主线新 kit 加三个 portable 原件经过现有 finalize_assets 的本地组合：**19 项发布资产、18 行 SHA256SUMS、四个目标**。另以 UID 1000 调用 verify_kit 接受完整组合，文件校验前后未变；没有调用网络、发布、安装或 probe。19 项资产、16 个原始 kit 成员和 12 个 Actions artifact 是不同计数。最终发布必须重新核准当时准确 default HEAD 及其最新八组/同提交附件，不能晋升旧基线包冒充最终主线。

关键原件：

- [主线 kit / 11740111797](https://github.com/ForceMind/V-UI/actions/runs/38234112586/artifacts/11740111797)，SHA-256 `dbda4a54f1e298d758cb029827aafb9fcf425cb0e33fa49eccbf83ebbb60b629`
- [链路 / 11740182380](https://github.com/ForceMind/V-UI/actions/runs/38234112640/artifacts/11740182380)，SHA-256 `cb65fedf57d162df3493900730b653ac32f5cb7c6573c9ceffe2d0d3e6e93fa4`
- [部署证据 / 11740349096](https://github.com/ForceMind/V-UI/actions/runs/38234112670/artifacts/11740349096)，SHA-256 `587e736b431fbe65beef6f70b6ce688e007ce03a15e48f9c1fe76efef7140b3f`

本地审计首次导入产生额外 `__pycache__`，被固定 kit 成员门禁正确拒绝；该生成缓存移出并保留，随后禁止写字节码的校验通过。原始制品字节没有修改；这不是主线 CI 失败，也没有放宽门禁。

## 资源、root 与尚未获资格的事项

| 标准单 CPU 短 cgroup 限额 | 峰值 MiB | 结果 |
| --- | --- | --- |
| 512 MiB | 279.34375 | 29 阶段通过；memory.events 全 0 |
| 384 MiB | 269.33984375 | 29 阶段通过；memory.events 全 0 |
| 320 MiB | 270.26953125 | 29 阶段通过；memory.events 全 0 |

这些是短 cgroup 门槛，不是整机或持续运行资格，不能替换[低资源历史](LOW_RESOURCE_CLOSURE_20261011.md)中大数据用例已达到 512 MiB/零余量、max 事件 883/OOM 0 的事实。160 MiB 完整空载/320 MiB 安装升级是未达证的工程预算，不是本次新设的硬发布 gate。

真实受管 root/systemd 资格仅属于 `f50942f1b399c7c397df532d04f9143e2527aaa8` 的 [run 38203515936](https://github.com/ForceMind/V-UI/actions/runs/38203515936)：空白临时 Ubuntu 24.04 x86_64/systemd/cgroup v2，11 个必需阶段含 cleanup，自有资源清理已确认。没有为 02d8 或后续文档头重跑它，也不把普通包消费改称新的 root 验收。[操作合同](MANAGED_OPERATIONS.md)中的“候选”指未公开发布的有界组件合同；软件已入主线与 f509 资格、Release 晋升分别归属。

真实 1 vCPU/512 MiB 整机、连续 24 小时完整负载、其他平台真实 root/init、跨版本/schema 迁移及真实断电恢复仍未获资格；未使用用户的 Oracle 或其他主机。OpenRC 受管写动作继续明确拒绝，不因为四目标包消费成功而宣称 init 全覆盖。

## 历史与后续路线

保留 f509 普通链路首次 VMess 原因缺失及 unchanged-code attempt 2、旧 root harness 失败与当时清理未证、abbe 链路首次 recorder 空观察及其重跑成功、各次本地 netlink EPERM 原结果。VMess/HTTPUpgrade 的合成 RED 证明观察/清理合同缺口，不唯一归因原 CI 网络时序；当前通过没有抹掉这些失败。详见[原受管候选记录](MANAGED_OPERATIONS_CLOSURE_20261012.md)、[HTTPUpgrade 质量修复](https://github.com/ForceMind/V-UI/pull/35)及[贡献测试说明](../CONTRIBUTING.md)。

[ROADMAP](../ROADMAP.md)仍有六个后续工作主题：UDP/DNS、DNS-01/通配符、核心自动升级/回滚、任意 YAML 导入/合并、容器部署、NixOS 等声明式模块。它们不是本轮八项已完成软件目标；XHTTP/HTTPUpgrade 的公共支持缺口也没有被测试修复解锁。先完成 0.4.7 交付决定，不据此自动开新协议或获取实际主机权限。

代码整合、准确验收、资产技术准备、公开发布和真实主机资格分别记录。`publish=false` 仍会创建 Draft Release 并上传，绝非 dry-run；本收口没有执行它。

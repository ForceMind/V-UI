# 完整运维软件完成矩阵

## 当前产品代码归属

本轮八项软件目标已通过 [PR #35](https://github.com/ForceMind/V-UI/pull/35) 正常进入主线，固定产品代码基线为 `02d8038d5c06301cd81fb5ee1c776ce53b8bd4b7`。准确主线普通 CI、四目标新包/消费者、浏览器和19项资产技术组合见[主线收口](MAINLINE_OPERATIONS_CLOSURE_20261012.md)。这八项不代表整个 ROADMAP 完成，亦不把普通包消费替代真实 root/init 或整机资格。

PR #28–34 的代码历史已包含；旧 stack PR 是否仍 open 与代码祖先关系分别记录。未创建 Release、晋升附件或操作用户主机。实际受管 root/systemd 资格准确属于下方 f509 原 run；后续文档头的准确普通 CI/包验收留在承载 PR，不回填自身未来 SHA。

## f509 原候选矩阵（2026-10-12，历史）

以下“本候选/仍为Draft/不合并master/后续待验”指原阶段及原 PR 状态。保留原实现合同、失败边界和资格归属，当前集成情况以上方固定产品代码基线为准。

本候选将低资源软件修复、后台无效安全入口修正、服务用户菜单与全局受管菜单放在同一份源码中。整合保留 [PR #28](https://github.com/ForceMind/V-UI/pull/28)、[PR #29](https://github.com/ForceMind/V-UI/pull/29)、[PR #30](https://github.com/ForceMind/V-UI/pull/30)、[PR #31](https://github.com/ForceMind/V-UI/pull/31) 和 [PR #32](https://github.com/ForceMind/V-UI/pull/32) 的历史，不修改其头或主线。旧 PR 分别通过不等于本候选已经整体通过。

2026-10-12，整合 [PR #33](https://github.com/ForceMind/V-UI/pull/33) 的准确软件提交 `f50942f1b399c7c397df532d04f9143e2527aaa8` / tree `16e3bcad057b113052475a861a1f9911c2859eff` 已完成以下有界验证。普通八组 CI 与一次真实受管系统资格分别通过；历史失败、原件及未验收项见[收口记录](MANAGED_OPERATIONS_CLOSURE_20261012.md)。PR 仍为 Draft，未合并或发布。

## 本版完成条件与实际证据

| 目标 | 实现与实际消费 | 必须保留的失败边界 | 准确候选证据 |
| --- | --- | --- | --- |
| 网页后台 | 既有认证、节点、路由、证书及安全中心只读说明 | 不支持的封禁不调用 501 接口；重复点击、键盘、导航、会话过期 | [Test/Chromium](https://github.com/ForceMind/V-UI/actions/runs/38193879559)、[实际包部署](https://github.com/ForceMind/V-UI/actions/runs/38193879555)、[证书浏览器](https://github.com/ForceMind/V-UI/actions/runs/38193879594)通过 |
| 包内服务用户菜单 | 只读状态、安装记录、明确选择后完整校验、可信本地候选准备 | 不自动提权；非 TTY、EOF、取消、坏摘要、跨平台、健康失败、超时保旧 | [四目标 PTY/worker 与旧 HTTPS 连续探测](MANAGED_OPERATIONS_CLOSURE_20261012.md#普通-ci-与包内消费)通过；root/init 模拟另列 |
| 全局 `v-ui` | 固定 OS Python、root-owned 控制源码及完整可信 kit 消费者 | 不执行用户可写解释器或 payload；链接、权限、内容漂移和缺失依赖拒绝 | 软件反例通过；[真实安装及 OS Python 降权 worker](MANAGED_OPERATIONS_CLOSURE_20261012.md#真实受管系统资格)通过 |
| systemd 服务控制 | 状态、启动、停止、重启；先发 socket 停止请求、核验 cgroup 静止；依赖关系可让 responder 先完成停止 | 未知不冒充 inactive；停止未确认不操作数据；故障不擅自恢复启动 | 假服务反例与[真实 systemd/socket 生命周期](MANAGED_OPERATIONS_CLOSURE_20261012.md#真实受管系统资格)通过 |
| 候选切换和代码回退 | 同一操作锁内停服备份、完整校验、健康检查、选择 CURRENT | 保留旧 release 和停机备份；已提交 CURRENT 不自动回滚，失败留停或状态未确认；准备或 READY 不是升级成功 | [真实 worker 的同代码合成 A→B→A](MANAGED_OPERATIONS_CLOSURE_20261012.md#真实受管系统资格)通过；不证明跨版本/schema 迁移 |
| 备份、恢复及中断恢复 | 私有备份、两代 writer 锁、撤销旧授权、明确 recover-restore | 坏摘要、争锁、重命名失败、journal 异常、超时、目录缺失 | 软件反例与[真实菜单的归档/SQLite/撤权、坏归档留停及构造中断恢复](MANAGED_OPERATIONS_CLOSURE_20261012.md#真实受管系统资格)通过；不证明真实断电 |
| 低资源修复保留 | 完整流式读取、私有文件缓存生命周期、GNU allocator、状态轮询 | 不扩大到用户原件或数据库缓存驱逐；不跳过完整校验 | [本候选标准 512/384/320 MiB 短 smoke](MANAGED_OPERATIONS_CLOSURE_20261012.md#资源与未验收边界)通过；整机/24 小时仍未验收 |
| 来源、文档、发布边界 | 同一准确 head/tree 的源码、四目标包及完整 kit | 不混用不同 PR 的 bundle/控制文件；不把 skip 当通过 | [f509 八组/11 jobs/119 steps、10 个原始 artifact 与完整 kit](MANAGED_OPERATIONS_CLOSURE_20261012.md#普通-ci-与包内消费)已核验；后续纯文档 head 待各自核验 |

这些行全部需要实现、实际组件消费、负例和文档证据。单个 PR、一次单测或一组 CI 完成不是整版完成条件。软件验证、真实 root/init 资格、低资源整机资格以及公开发布分别记录，不互相替代。

## 已实现范围与限制

- 全局菜单的使用和完整系统组件升级顺序见[受管控制契约](MANAGED_OPERATIONS.md)。只有应用 CURRENT 切换不会更新 root-owned 控制源码、launcher 或安装记录。
- OpenRC 当前只支持状态查询与纯候选准备；服务启停和依赖停服的数据动作明确拒绝。四目标包通过不表示四种实际 init 生命周期均已验证。
- root 控制及降权 worker 使用合作式操作锁，不隔离恶意同 UID 代码。SHA-256 证明完整性，不证明未知来源可信，也不构成候选代码沙箱。
- 公开 XHTTP/HTTPUpgrade 导出仍阻断；UDP/DNS、DNS-01、通配符和其他未来阶段不属于本版完成条件。

## 验证与权限边界

标准八组 CI 自然运行。其中部署 workflow 包含 512/384/320 MiB 的短 cgroup smoke；这些不是旧低资源 opt-in。真实浏览器、实际核心链路、旧普通安装器与新菜单包消费者分别取证。

旧低资源 root、双核心、CPU 压力和安装追踪的精确产品门禁保持原样；整合产品已超出其锁定范围，不自动触发、不放宽门禁或套用历史成功。历史 root `a373d47` 的 471.215 MiB 峰值及大数据 `0f6c3e6` 的 512 MiB/零余量仍仅属于各自准确版本，详见[低资源收口](LOW_RESOURCE_CLOSURE_20261011.md)。160/320 MiB 工程预算和真实整机连续 24 小时完整负载未获资格证明。

新全局入口已在明确授权且为空的临时 Ubuntu 24.04 x86_64 runner 完成一次真实安装及系统层测试。非 root 假安装、假 systemd、真实包内 worker 测试和既有 oneclick 成功均不代替它。没有使用用户服务器或 Oracle 主机，也不因这次通过自动取得其他主机的安全关键安装权限。其他平台真实 root/init、跨版本迁移与整机/长时资格仍未完成。

本候选不合并 master，不创建 tag/Release，不晋升附件或部署。上述资格准确归属于 `f50942f`；承载本次收口的后续纯文档提交须在 PR 验收记录另列 exact-head 普通 CI 与同提交附件，不把软件字节未变改称新 head 已重跑 root，也不为纯文档重复触发 root opt-in。

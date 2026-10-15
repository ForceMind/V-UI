# 双开关 M4：有界离线恢复

日期：2026-10-15。承接[双开关契约](DUAL_PROXY_RELAY_CONTRACT.md)、[M2 事务](DUAL_PROXY_RELAY_M2.md)与[M3 面板](DUAL_PROXY_RELAY_M3.md)。本文描述新的候选离线实现，不表示 M4 全部完成、准确候选 CI、四目标制品、生产部署或公开发布通过。

## 两种明确操作

普通 `scripts/deploy.py restore` 继续拒绝当前代理元数据损坏、未知或 pending，也拒绝把已提交的任一 ON 开关静默降为 OFF。原来的 `recover-restore` 只回滚完整数据目录交换，不将代理 pending 当作成功。

新增完整运行包中的 `python -m app.services.proxy_recovery`，只处理本地文件，不联网、不解析域名、不调用核心配置检查、不启动核心或服务：

- `restore`：显式选择 SHA-256 已知且与原数据路径一致的备份。即使当前代理元数据损坏，也只能把管理员明确审阅的完整备份作为目标。备份必须包含有效代理初始化标记、全部历史修订、匹配秘密、无 pending，以及能从备份入口数据库、保存的上游 IP/自身地址快照重建的准确活动核心配置。未提交过的草稿保留为未提交；不将 `active=null` 冒充已提交 OFF。恢复撤销旧会话和订阅授权。
- `reconcile`：只接受当前有效的 `prepared` 事务，将 sing-box 持久状态退回事务记录的完整旧基线，并将该事务记录为 aborted。当前核心记录必须是该事务的旧版或候选版，旧配置、活动修订、秘密及来源必须可验证。保留新草稿、不可变配置及历史。首次应用前的 `active=null` 仍然是未提交状态。未知 write-intent、未知 schema、缺失秘密、缺少来源上下文或不相关核心版本均拒绝，须选择已验证备份；不猜测 previous/candidate，不初始化双关默认值。

已提交 ON 策略可以处于人工停止状态：`runtime.enabled=false` 不改写活动开关，恢复保留该意图；反之空入口配置不能被标记为启用。目标验证使用保存的来源快照，不能证明这些地址现在仍可用。以后显式启动时，现有启动保护仍重新核对真实自身地址和当前入口；离线恢复成功不等于线路可用、核心已运行或 DNS 已通过。

## 预览及准确计划

必须先停止面板、源码 Uvicorn、独立核心管理和证书/数据库写入进程，以服务用户运行。使用目标安装的准确运行包 Python 和完整 payload 工作目录；不要用只有精简安装控制器的目录执行新模块。

```sh
python -m app.services.proxy_recovery --root /private/install restore \
  --archive /private/backup.zip --archive-sha256 <archive-sha256>
python -m app.services.proxy_recovery --root /private/install restore \
  --archive /private/backup.zip --archive-sha256 <archive-sha256> \
  --execute-plan-sha256 <preview-plan-sha256>

python -m app.services.proxy_recovery --root /private/install reconcile
python -m app.services.proxy_recovery --root /private/install reconcile \
  --execute-plan-sha256 <preview-plan-sha256>
```

没有执行摘要参数时只预览。预览仅输出操作、准确源数据树/备份/目标树摘要、草稿及活动修订、四状态开关、核心版本/保存的启用意图和计划摘要，不输出 UUID、密码、配置原文、证书材料或 write-intent 内容。管理员应确认目标开关，尤其从未知当前策略恢复到 OFF 的备份时。执行重新生成整个计划并匹配准确摘要；源文件、数据库、秘密、备份或目标任一变化都使旧计划失效。计划不是启动、公开发布或部署授权。

树扫描最多 32,768 条目、总文件 bytes 不超过现有归档展开上限 1,100,000,000；文件按块读取，核心配置最多 16 MiB，入口 JSON 合计最多 16 MiB。所有数据目录/文件须服务用户拥有，目录私有、常规数据文件私有；符号链接、硬链接、设备/FIFO、未知不安全权限和所有权拒绝。仅排除明确的零字节瞬态锁，不忽略未知隐藏数据。源坏 JSON 的原始 bytes 可被保留和摘要，但不能被当作有效代理状态。

## 停机证明及完整交换

`.<data-directory-name>.owner.lease` 位于数据目录的父目录，目录改名后仍是同一租约。受支持的面板生命周期、源码 SQLite 连接、代理草稿/事务、路由、独立核心和证书工作都持有共享租约；离线操作持有非阻塞排他租约，并核对既有核心/证书 owner locks。即使没有任何 core state，活着的源码面板也会阻止恢复。独立线程/进程不能借用排他证明，失败不会等待死锁或升级共享锁。同线程内部验证可借用排他租约；借用者持有重复描述符，SQLite 连接即使活得比外层操作更久也继续阻止新写入者，直至真正关闭。普通共享持有者取得 flock 后、任何初始化前检查持久恢复 journal；崩溃释放 flock 不会使源码启动重建空库。外部编辑器或绕过这些接口的自写 SQLite 程序不在这项协作租约保证内，必须另行停止。

新数据只在私有 staging 内准备。执行先 fsync 全部目标常规文件和自底向上的目录，并重核目标摘要，然后持久化 `RESTORE_PENDING.json`（schema 2，包含准确源/目标/计划摘要），保留原目录于 `recovery/before-<id>`，再原子改名完整目录。重新校验安装后的数据库、全部代理引用/秘密、配置来源以及原目录摘要成功后才清除 journal。不逐文件混合新旧数据，不清理旧历史或原目录，不自动启动服务。

journal 清除前的交换或验后故障保留 journal 和可用的原/中断目录，正常启动仍阻止。最后清除 journal 后的父目录 fsync 若失败，命令仍失败，但已验证的新目录和原目录保留，内存可见 journal 已删除；不能将此点的失败误报为仍有 journal 或自动回滚成功。`recover-restore` 对新 journal 先验证原目录准确源摘要，再恢复它，保留被替换的中断目录，复核源摘要后清除 journal。若原数据本来损坏，回滚只恢复原损坏 bytes，不声称修好；代理的正常失败关闭检查仍有效。原目录被修改、摘要不匹配、journal 非法或所需数据缺失时不自动清 journal。

## 候选验证边界

新增测试以临时私有目录、假凭据和纯编译器配置覆盖四态、秘密一致、准确摘要、准备事务回滚、未知状态拒绝、活跃写入者与交换故障。现有备份、发布控制、运行时和数据库回归分别执行。精简安装器从同一已验证归档提取五个固定源码成员：`scripts/deploy.py`、`app/release_tools.py`、`app/data_owner.py`、`app/services/proxy_store.py` 和 `app/services/upstream_profile.py`。受管 worker 的固定兄弟文件及 receipt 同步覆盖新的标准库租约 helper；显式 refresh 只接纳完整原版或 M1 版旧 receipt，旧文件必须逐一未变，未登记 helper 不被接纳。公开 kit 仍为 19 个顶层资产。完整新离线 CLI 不隐式塞入精简引导流程。

`scripts/proxy_recovery_smoke.py` 已接入 portable staging 和 deployment 的既有四目标门槛，使用实际包内 Python 与 payload、隔离私有假数据，要求四态保存/备份/恢复、秘密与历史一致、八次计划不符拒绝、八次活跃写入者拒绝以及准确模块来源。socket/DNS/子进程与核心操作的任何调用都使检查失败；输出的每一项、类型与计数严格校验。checkout 中的复制 payload 回归只验证测试驱动，不能替代同提交四目标实际消费；后者仍待准确候选 CI。

固定核心仍为 sing-box 1.14.2，VERSION、发布/生产边界不变。历史失败与未完成门槛继续记录，不将局部单测写成整个 M4 验收。

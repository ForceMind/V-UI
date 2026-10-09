# 1 vCPU / 512 MiB 低资源目标

状态：开发与验收目标，尚未完成真实小型 VPS 验收。2026-10-07 用户要求安装和运行均能在 1 CPU、512M VPS 上正常低功耗运行；本项目以 **1 vCPU、512 MiB 总内存** 建立明确测试口径。此目标不等于当前 v0.4.7 已达标。

## 范围和交付边界

- 低资源优化使用独立分支/PR；不替换已冻结的 0.4.7 制品，不改变其源提交、摘要或公开支持范围。
- 保持 FastAPI + SQLite、固定官方核心与离线 wheels，不引入 Redis、常驻 Node、额外数据库或目标机源码编译。
- 正式发布仍人工 gated；真实 VPS、云安全组、防火墙、真实证书及付费操作继续遵循原确认边界。
- 不通过增加 swap、关闭鉴权/TLS/完整性校验、删除数据库或减少负向测试来“达标”。

## 初始源码发现（a6aa84dc5f66b86ac82213dd0b7323d1b6ccfc60）

1. `scripts/install_system.py::verify_archive` 把 ZIP 一次读入 `raw`，以 `BytesIO` 打开，逐成员完整解压到内存；父进程安装期间持有 `raw`，再启动 stage 子进程。允许的压缩包上限为 850,000,000 字节。这是可直接确认的非恒定内存路径，不是已发生 OOM 的实测结论。
2. `app/release_tools.py::unpack_verified` 再次整包读取，逐成员 `archive.read`；`files_in`、`create_archive` 与 runtime pin 校验亦有完整文件读取。安装、升级、备份/恢复峰值须分开检查，不能只测空载面板。
3. `web/js/app.js` 登录后以 3 秒/10 秒固定间隔请求系统/核心状态；系统接口每次枚举分区并查询使用量。应先度量后台标签页、多标签、并发和请求重叠，再选择可见性暂停、退避或缓存，不能把推测写成已证实 CPU 瓶颈。
4. systemd 的 HTTP-01 服务按 socket 激活，OpenRC responder 为常驻服务。证书任务空闲等待 60 秒；不能把不同 init 后端的资源结果混在一起。

## 实施顺序

1. 安装内存：文件流 SHA-256、有限块成员校验/解包、安全快照生命周期、确定性错误清理。保留包/成员总量上限、路径/重复/链接/加密拒绝、摘要/权限校验、非 root 包内执行、原子切换和回滚。
2. 建立峰值回归：同一合成输入比较旧/新路径，记录完整命令、源码 SHA、环境、压缩/展开大小、时间和进程峰值 RSS；用增大输入证明内存不随整个包线性增长。合成包不作为产品安装验收。
3. 运行基线：面板空载、面板加单 sing-box 核心、双核心分别测；UI 隐藏/可见，多标签，节点编辑与订阅导出，证书续期和日志/备份任务分别测。只按证据优化。
4. 在已授权测试环境验证以下完整资源矩阵，准确候选 CI/四平台兼容结果单独记录；不得拿旧提交的绿色 CI 当新提交通过。

## 安装候选的临时空间要求

流式候选使用私有文件快照，不能把“文件”自动等同于物理磁盘。`/tmp`、`/var/tmp` 或安装数据目录都可能挂载为 tmpfs；测量前必须用 `df -T`/挂载信息确认。在线安装脚本的下载/引导目录由 `TMPDIR` 控制，Python 安装器快照由 `--archive-temp-dir` 控制（默认 `/var/tmp`）；解包快照优先放在目标父目录。只配置其中一项不足以证明整个安装过程没有使用 tmpfs。

选择已存在、可信、可写、有足够空间的磁盘目录；不要改变全局临时目录权限或安全设置。安装时原下载包、父快照、交接 ZIP、子快照可能同时存在，应为压缩包多份副本、展开运行时、旧版本与备份预留磁盘空间。磁盘不足必须失败清理，不能退回整包读内存。具体参数仅适用于本性能候选，不能假定冻结 0.4.7 已支持。

ZIP 的中心目录、最多 10,000 个文件条目及至多 4 MB MANIFEST 仍有元数据开销；Python ZIP 中心目录解析发生在条目数量校验之前。因此本轮目标是消除正常发布包的完整压缩数据和完整成员驻留，不声称任意恶意 ZIP 的解析具有常量内存上界。

## 资源验收合同

### 环境与记账

- 真实 1 vCPU / 512 MiB VPS（记录发行版、内核、架构、libc、init、磁盘空间、swap 配置）作为最终安装/运行证据。
- 1 CPU quota / 512 MiB cgroup 只能作为受限回归；不等于整台 512 MiB VPS。记录进程树和 cgroup `memory.peak`、`memory.events`/OOM、swap、CPU 时间、墙钟时间；RSS 不含全部页缓存和内核内存，不能单独证明宿主余量。
- 测试客户端、流量发生器、测量工具与服务端分开计账；记录 OS 基线与完整服务进程树（面板、核心、证书及安装子进程）。
- 不要求用户租新服务器或提供生产机；没有已授权目标时将实机项明确标为待验证。

### 必测场景

- 从正式目标包下载/校验/解压/离线 wheels 安装，到首次启动、登录、创建节点；冷启动及重启恢复。
- 已有数据升级、停机备份、坏包拒绝、候选失败回滚、恢复；同时确认原数据与服务状态未损坏。
- 面板空载和单核心空载各 30 分钟；1/10/50 并发连接阶梯各 10 分钟，固定协议、负载大小/速率和数据集；过载需可恢复，不承诺所有协议相同吞吐。
- 管理页面可见/隐藏、多标签、登录失效；连续订阅请求、规则导出、大日志/数据集；证书任务运行时与代理流量并存。
- 至少 24 小时稳定性测试，记录内存增长、重启/OOM、错误率及 CPU；x86_64/ARM64 与 glibc/musl 不互相替代。

### 判定

硬门槛：全部安装/运行路径在限定内存内完成；无 OOM/意外重启、无持续 swap 抖动、保留可用系统余量；功能、安全、数据恢复回归均通过。安装、升级、空载、活跃流量、证书峰值分别列明，失败保留原始记录。

初始工程预算（待基线验证，可按公开证据修订，不是用户承诺或当前测量）：安装/升级完整服务进程树峰值内存争取不超过 320 MiB，面板加单核心空载争取不超过 160 MiB，空载 30 分钟平均 CPU 争取不超过单核 1%。最终仍以整机实测可用余量和稳定性判定，不能用预算掩盖超标。

“低功耗”在普通 VPS 上只能以空载 CPU、唤醒/轮询、I/O 和内存等代理指标衡量；没有硬件功率计，不报告瓦数或节电比例。吞吐上限、延迟及适用并发以具体协议和宿主实测为准。

## 证据状态

- 已确认：上述基线源码中的全包/全成员读取与固定 UI 轮询。
- 本轮候选：流式安装/解包/文件哈希、私有快照与错误清理已实现；本地回归与合成峰值结果见下。准确候选CI、实际包短门槛、部分长负载与原生UI诊断已有后文逐提交证据；新改动须自己的准确head验收，真实512MiB整机与24小时稳定性仍待验收。
- 未执行：新 Release/tag、生产部署、防火墙或安全组修改、购买/启动新服务器。


## 流式候选本地证据（2026-10-07）

测试源码基线为 `a6aa84dc5f66b86ac82213dd0b7323d1b6ccfc60`；候选修改位于本性能 PR，不是冻结 0.4.7 制品。改动包括在线脚本摘要、安装父进程验证/交接、成员解包、release 文件摘要/打包与 runtime pin 校验；私有快照保证校验后替换原路径不会改变后续使用字节。独立审查未发现阻断项。

使用 Python 3.12.14，精确 `requirements-test.txt`，本地针对安装/归档的 69 项回归通过。新增测试覆盖有界读取、源文件替换、快照权限/关闭、大小边界、坏摘要/危险 ZIP、磁盘失败清理、原目标保护、原子 launcher 写入、在线脚本摘要及临时目录选择。全量测试与准确候选 CI 以 PR 最终检查记录为准；条件跳过不算真实核心/浏览器/systemd通过。

可重复合成命令：`python tests/benchmark_archive_memory.py --source-root CHECKOUT --work-dir DISK_BACKED_DIR --member-mib 128`，以及 `--member-mib 256`。基线 CHECKOUT 使用上述准确提交，候选使用当前 PR 源码；须先确认工作目录不在 tmpfs。该脚本用相同 stored ZIP、假控制器、实际验证/复制/解包代码，父进程持有验证结果时启动子进程，每 5 ms 采样两进程 RSS 之和。

- 128 MiB 成员，ZIP 134,219,014 字节：基线并行采样 RSS 425,088 KiB；候选 34,052 KiB。父进程 `ru_maxrss` 278,528 / 18,520 KiB。
- 256 MiB 成员，ZIP 268,436,742 字节：基线并行采样 RSS 818,312 KiB；候选 34,060 KiB。父进程 `ru_maxrss` 540,672 / 18,520 KiB。

工作目录在 overlay 文件系统，快照显式写在该工作目录，未使用主机 tmpfs。两种输入下候选的进程 RSS 未随完整归档大小线性增长；这是单次本地合成观察，非严格数学常量内存保证或整机内存证明。采样从子进程创建后开始，并非连续捕获的精确最高峰；父校验峰值单列。没有计入全部页缓存/cgroup/OS，也没有执行真实 wheels 安装、真实核心、应用启动或整机 1 CPU/512 MiB 配额。时间单次测量不用于宣称 CPU/吞吐/功耗提升。仍须完成前述实际包和整机验收。

## 面板运行时诊断（2026-10-08）

准确候选 `0838656ac5a841ba75d3fee35e1c00189fdafc90` / tree `b22807e4da994b843a2e387b6bd27a871ff3af6b`，Python 3.12.14、Linux x86_64、单 CPU 亲和、假 10 节点数据库，循环回本机 HTTP；没有代理核心或证书任务运行。负载发生器独立于服务进程，10 ms 采样只统计服务进程。当前 sandbox 不暴露 cgroup。

- 60 秒空载：RSS 71.42 MiB，服务 CPU 0.11 秒，约单核 0.18%。这是短时基线，不替代 30 分钟或 24 小时目标，也未覆盖浏览器后台轮询。
- `RLIMIT_AS=512 MiB` 下启动、登录与 300 次串行管理员订阅导出通过；10 并发阶段失败，日志为线程创建失败，采样 VMS 508.18 MiB、RSS 96.38 MiB。
- 仅移除虚拟地址限制、保持单 CPU 的对照中，100 次 10 并发导出全部成功，采样 RSS 95.12 MiB、VMS 1113.48 MiB；匿名 401、登录成功、退出后重放旧会话 401 均通过。

`RLIMIT_AS` 限制地址空间，线程栈/分配器预留也计入；上述失败保留，不能改写为物理 512 MiB OOM，也不能把无此限制的对照写成 512 MiB 验收。本轮不降低密码哈希、线程安全或鉴权/TLS 强度来消除该诊断限制。下一步在空 CI runner 上对真实离线包做 1 CPU 配额、512 MiB cgroup、禁用该 cgroup swap 的独立回归，并记录实际控制器数值与包含全部子进程/页缓存的内存峰值。仍不等于整台 VPS 或完整负载稳定性资格。

### 实际包 cgroup 自动门槛

`Selected release deployment gates` 增加 `scripts/low_resource_acceptance.py`，仅在明确的 GitHub-hosted Linux 空 runner 上运行；不在开发者电脑或生产服务器执行。构建在限额之外，准确源码对应的实际离线包 stage、固定 wheels 安装、候选健康检查、激活、验证 TLS 的假证书 HTTPS 面板、登录/退出/旧会话拒绝、100 次并发度 10 的认证读取、60 秒空载、停机备份/恢复/重启在同一非 root transient-unit cgroup 内运行。

执行前从 worker 自身 cgroup 读取并核对 `memory.max=536870912`、`memory.swap.max=0`、`cpu.max` 恰为一核配额；报告必须包含 `memory.peak`、`memory.current`、`memory.events` 和 `cpu.stat`。OOM、缺失/不完整报告、错误来源、非零退出、超过文末固定基础页容差的峰值都失败（名义预算是否满足另列，不合并为同一结论），不以 skip 当通过。同一 worker JSON 在初始/分阶段/终态原子更新，保留限额和各阶段结果行，服务日志另存；阶段 `memory_peak_bytes` 是截至该阶段的 cgroup 生命周期累计峰值，未逐阶段重置，退出后只清理本次随机命名的 transient unit 和临时假数据。

此门槛包含工作进程、pip、面板、测试客户端及子进程的 cgroup 记账；不包含宿主 OS、构建下载与预先由其他 cgroup 持有的页缓存。使用准确安装包，但不是 root 安装器/系统服务全链路受限安装；原 systemd 一键安装验收继续单独保留。首版 HTTPS 面板带空闲证书管理器，没有代理核心或实际续期负载；下述新版单核心代理夹具单独验收，不倒推首版已经覆盖。`/api/auth/me` 并发读取也不同于前述管理员订阅导出。实际 cgroup 通过与否以准确最终 CI 日志/JSON 为准，源码存在和本地结构测试通过不代表 cgroup 已执行。

仍未被此短测覆盖的目标：真实 512 MiB 整机余量、受限 root 安装/升级全进程树、运行代理核心和真实流量、证书续期叠加峰值、30 分钟空载/10 分钟阶梯与24小时稳定性、四目标平台各自的资源验收。没有这些证据时保持“候选资源回归”，不写“512 MiB VPS 已达标”。

### 首次真实512 MiB结果与余量复核

候选 `e4f7aef9f18765416cba0116c11d071f3c15a2a1` 的[真实 cgroup 步骤](https://github.com/ForceMind/V-UI/actions/runs/37718390504/job/113120111956)首次执行成功。已下载并核对[证据附件](https://github.com/ForceMind/V-UI/actions/runs/37718390504/artifacts/11524757892)的 SHA-256 `25a33171c1bbac9e29b360ecd6fff88a412138eeef07e379c6fa6507356f05c2`：限额为512MiB/零swap/一核配额，所有阶段通过，OOM/oom_kill均0。

但累计 `memory.peak` 达到536,870,912字节（恰为512MiB），`memory.events.max=81`；面板空载阶段末 `memory.current=535,617,536`。这不是有整机余量的证明，也不能仅凭最终清理后内存下降就断定全部是可回收缓存。60秒空载消耗86,689微秒CPU（约单核0.14%），仅限本短测和所列进程，不报告功率。

为核对余量，本候选保留512MiB原回归，新增384MiB及320MiB两档相同工作负载、独立临时目录/JSON/面板日志。320MiB对应前述安装工程预算，384MiB用于观察保留128MiB宿主余量的可行性；这些是cgroup上限而非真实宿主配置，也不保证剩余内存足够OS和代理核心。每阶段与终态增加 `memory.stat`（必须有anon/file）及OOM/限额事件，区分阶段结束时匿名/文件内存；分项不是生命周期峰值瞬间的同步构成。每档必须自证实际加载的对应上限、零swap及一核配额，任一档失败保留证据、不放宽限额。新增档位结果仍以最终准确提交CI为准，不继承上述512MiB首次通过结论。

### 384 MiB首次失败与固定一页检查口径

`ee5b127ce570c47303436b13daa1ccdec7b5c152` 的[资源工作流](https://github.com/ForceMind/V-UI/actions/runs/37719281693)在384MiB档失败，320MiB因fail-fast未执行，不能补写通过。已核验[原附件](https://github.com/ForceMind/V-UI/actions/runs/37719281693/artifacts/11524893963) SHA-256 `92d90839947d42014595249d1c09a60ff8be6241d13d9791f5d5846bbb7e1200`：worker的22个面板阶段均成功、OOM/oom_kill/oom_group_kill为0，memory.max=402,653,184，但memory.peak=402,657,280，超额4,096字节；memory.events.max=9,747。原协调器的严格peak≤quota断言失败，历史结论保持，不追改旧运行。

[Linux cgroup官方文档](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#memory-interface-files)说明使用量可能短暂超过memory.max。它没有保证最多一页，也不证明本次是“记账误差”；这里承认实际报告的瞬时超额。新版明确采用**项目定义的固定一个主机基础页容差**，不是内核保证：配置的memory.max、零swap、一核配额和全部OOM拒绝要求不变；读取并交叉核对实际基础页大小，保留原始peak、全部memory.events、超额字节及“名义预算满足=false”。最多一页时只能报告按此披露口径通过，不能改称“峰值≤名义限额”；再超一页仍失败，不能逐次扩大容差换绿。此前的严格峰值契约因此有此明确修订，整机资格与名义工程预算不自动转为通过。

该384MiB档的面板空载阶段末anon=158,662,656字节、file=226,811,904、kernel=14,516,224；512MiB档同阶段anon=158,793,728、file=352,780,288、kernel=23,777,280。分项证明这些时刻有大量文件页，并非峰值瞬间的构成；不能据此直接推算整机可用余量、断言缓存随时全部可回收或把名义预算改判成功。完整JSON保留所有事件与分项。

### 单核心TLS代理受限回归（候选，尚待准确CI）

在每档原面板-only 60秒基线之外，增加面板加单个官方固定sing-box服务端的60秒空载；随后同一个已校验安装包提供sing-box测试客户端，V-UI adapter生成VLESS/TCP/TLS服务端配置，只把监听限定到127.0.0.1，使用假UUID/假CA/本机HTTP目标。客户端为显式单一VLESS出口、最终路由proxy的合成配置，不冒充实际订阅导出；不对外联网、不关闭TLS验证、不改变核心pin或公开协议能力。

短测发送10次串行和100次并发度10的HTTP请求，核对正文与应用送达计数；错误UUID必须有新的真实协议原因，错误CA必须有真实x509原因，并同时保证应用零送达及没有DIRECT。目标、服务器、客户端均在嵌套清理范围内，退出/失败清理后才进入备份路径。活动阶段额外计入测试客户端与目标进程，单服务器空载时没有测试客户端；不把这几秒负载当作10分钟吞吐、所有协议、双核心或24小时稳定性验收。

本地官方pin及配置parser检查成功，实际启动遇到sandbox netlink EPERM，原失败保留，没有提权或改安全设置；真实链路和资源结果由空CI runner的准确候选验证，单元mock通过不替代它。

### 固定容差仍失败后的实际缓存优化

`f10509aaa0a98e55e24e9ef2164be788426ba222` 的[资源运行](https://github.com/ForceMind/V-UI/actions/runs/37720250164)中，512MiB含真实TLS代理的全部工作成功；384MiB的功能阶段和代理正负向亦成功、OOM为0，但peak=402,685,952，比384MiB名义上限多32,768字节（8个4KiB基础页），超出固定一页政策而失败，320MiB仍未执行。[原附件](https://github.com/ForceMind/V-UI/actions/runs/37720250164/artifacts/11525616359) SHA-256 `5e73c06ca946ceaee95bf3e47797936fdb63dc5accf8b785043bac25c99beecf`已核验。没有扩大容差、提高quota、修改swap或重跑赌绿。

当时的 `2ccf05f` 产品候选降低实际一次性文件缓存占用：

- 私有ZIP快照与交接副本每8MiB刷写/fsync，然后对已消费范围使用逐文件POSIX_FADV_DONTNEED提示；完整读取仍按原有1MiB块进行。
- release包中wheels与压缩Python运行时的解包写入也分批写回；完整性校验对这些文件及精确的 `cores/<x86_64|aarch64>/{sing-box,xray}`路径使用冷读取提示，避免只为SHA校验而留下完整巨大文件的缓存。
- 所有文件、字节、摘要、权限、ownership、路径/大小拒绝、原子切换及回滚保持。提示不修改核心或其进程映射；Python已安装运行时文件、应用代码、数据库、配置及用户数据不在该提示允许列表中。不调用全局drop_caches、不改sysctl/cgroup门槛。
- root独立安装器与非root release controller各自内置相同的小helper，以AST一致性测试保护；root不导入包内app代码，没有新增发布helper资产。

[POSIX_FADV_DONTNEED说明](https://man7.org/linux/man-pages/man2/posix_fadvise.2.html)指出脏页和非整页范围可能保留；因此先写回再提示，读取时批量重访已消费前缀。提示是best-effort，内核/文件系统可以保留页面；提示不支持时保留正确性而失去优化，真实写回错误仍失败。额外顺序写回和后续冷读取可能增加I/O，不能只用内存下降宣称CPU/功耗提升。

本地合成单inode观察：128/256MiB文件，普通写读的采样驻留峰值128/256MiB，候选24/28MiB，读后驻留0；不是cgroup总内存。对已核验e4f安装包做仅文件清单/缓存/parser验证：40个不可变安装文件共179,116,176字节，逐文件即时驻留页数之和从179,200,000降到11,100,160字节，所有摘要与MANIFEST一致，官方sing-box与Xray配置parser仍成功。e4f旧包不作为新源码安装验收；新代码仍须准确新包三档CI。前述固定一页政策保持不变。

### 运行时完整性扫描缓存修复（2026-10-08 恢复候选）

`2ccf05f` 的[首次资源运行](https://github.com/ForceMind/V-UI/actions/runs/37721996354/job/113131532507)中，512MiB峰值485,617,664字节；384MiB峰值402,657,280字节，超过名义上限一页，仅按已披露固定一页口径通过，两档OOM均0。320MiB工作进程退出0，但峰值335,560,704字节，比335,544,320字节名义上限多16,384字节（四页），协调器按固定一页门槛正确失败。该工作流后续浏览器/HTTPS部署验收和候选包保存均跳过，不记通过；不重跑赌绿。

`2ccf05f` 后续本地候选在公开推送前丢失了工作区；本轮从该远端提交重新实现最小修复并重新验收，不声称字节或提交与丢失的 `86c007b` 相同，也不继承其测试结果。

完整安装后的 `runtime_tree_digest` 原先每次健康检查/激活/恢复校验都会重新读取整个已安装 Python 树。本轮仍按原顺序把路径、文件模式、符号链接目标和每个文件的全部字节送入相同 SHA-256，但对本次 release 自有运行时普通文件先逐文件 fsync，再用有界读取和 POSIX_FADV_DONTNEED 提示释放已消费页面；不跟随树内符号链接读取其目标。运行时压缩包的独立 pin 校验也使用冷读取。仅改变缓存提示，不改变文件内容、权限、摘要格式、原子恢复或已映射进程的正确性；提示可能导致后续冷读，不能把它宣称为免费优化或保证回收。

此范围明确扩展了上节原先排除的“已安装 Python 运行时文件”，仅限传给运行时完整性校验器的 release 自有树。数据库、用户配置、备份与任意应用数据不纳入。fsync 的真实错误仍使操作失败，提示缺失/失败只失去优化；零字节 read 请求不再误判为 EOF。没有修改全局缓存、sysctl、cgroup 上限、固定一页容差或 OOM 拒绝要求。

本地回归检查旧摘要兼容、内容/模式/链接篡改、空文件、1MiB读取上界、文件关闭、提示不支持及写回失败。真实三档资源与新 head 八组 CI 仍以准确提交结果为准；这段实现说明不提前声明 512/384/320MiB 通过，更不等于整机512MiB VPS或24小时验收。

### 恢复候选准确短门槛结果

准确候选 `e57d737490b67bfbcecfff7d73569407f72fabd8` / tree `2d57d4dc56f8d0204d67b2295720cd0bd4dfedf0` 的八组/11 jobs/全部步骤 attempt 1 成功。[资源运行](https://github.com/ForceMind/V-UI/actions/runs/37820211158)的[原始附件](https://github.com/ForceMind/V-UI/actions/runs/37820211158/artifacts/11569955517)已下载核对，外层证据ZIP SHA-256 `638acb382f035d5fbaeb9ac5befb8c4706ea7c4fc71022864fba93cc6b1e6b25`。三档 worker 均为该准确源码、complete=true、29阶段 passed、一核配额、swap=0；内层候选包 SHA-256 `21db4197c26b9f40e6c9227848371598f158887e23fd6131cc4e3d7eca61bf82`。

512/384/320 MiB 的 peak 分别为 475,471,872 / 402,653,184 / 335,544,320 字节，名义超额均0；memory.events.max 分别0/69/138，三类 OOM 计数均0。384和320档恰触名义上限，不能写成有余量。每档VLESS/TCP/TLS的10次串行及100次并发度10请求均成功且送达数对应；错误UUID取得unknown uuid，错误CA取得同条x509/unknown authority，应用零送达、无DIRECT、清理完成。后续独立部署浏览器/HTTPS/恢复门槛通过。以上仍为60秒空载与短请求回归，不是以下长时profile、真实512MiB整机或完整性能卡的通过证据。

### 独立长时资源 profile（实现，准确执行结果待验）

新增 `--duration-profile sustained`，只在显式 GitHub-hosted Linux 空runner执行，512MiB/零swap/一核配额及固定一个基础页容差不变；默认smoke和现有八组中的512/384/320短门槛保持。独立 `Opt-in sustained low-resource acceptance` 工作流只在PR增加 `run-low-resource-sustained` 标签事件触发，不在普通PR同步、每段文档或master推送自动消耗90分钟。重验不同准确head须显式移除再添加该标签；不把旧head成功继承给新head。构建、工作进程、协调器、job的时间上限分别合理分配，worker6600秒、协调器6660秒、job120分钟，不以放宽内存或漏报超时换绿。

- 面板空载1800秒，随后面板加单个固定官方sing-box服务端空载1800秒；每30秒保留cgroup原始计数、CPU、memory.stat和进程PID/PPID/名称/线程快照。阶段峰值仍是生命周期累计值。空载采样本身的工作进程开销计入服务组，不假装零测量开销。
- 1、10、50条持久HTTP CONNECT/TCP隧道经同一VLESS/TCP/TLS服务端，各保持600秒；每条每秒一次64KiB确定性正文，共每条600个GET，不重连、不重试、不跳过错过的速率槽。验证正文、目标请求数和目标连接数，记录实际时长、请求数、最大响应时间与错误。固定目标仅127.0.0.1，验证假CA/SNI，无公网流量。这是指定速率/正文的阶梯，不是最大吞吐测试或任意协议承诺。
- 长负载的sing-box客户端、本机HTTP目标及发生器由协调器在服务cgroup之外启动；验证客户端与发生器实际cgroup，单列各自CPU与Linux getrusage峰值RSS。RSS不是整个测试组cgroup峰值，不能相加声称完整物理内存；宿主OS与构建仍不计入服务限额。客户端/目标虽分组，仍与服务共享同一宿主CPU、磁盘及内核，不声称无争用。服务cgroup包含worker、面板、代理服务端及原安装/恢复子进程，故与短profile中客户端也计入同组的数字不直接同口径比较。
- 每档之后另发一个新连接/单请求并验证面板恢复，恢复计数不混入600×N固定负载；未刻意制造过载就不宣称已经证明过载容量。保留原短正向、实际UUID/CA拒绝原因、零送达、无DIRECT、备份恢复与注销重放拒绝。独立客户端采用单次受限生命周期并清理其私有进程组，失败/超时保留JSON及日志。协调器拒绝来源、profile、时间或阶梯不完整的报告。

此批首先补齐持续时间与分开记账的负载基础设施，不提前登记真实通过。UI可见/隐藏/多标签与登录失效的资源刻画、连续订阅/规则导出、大日志/数据集和证书任务叠加仍须各自实际测量，未因普通功能测试通过而自动完成。24小时、真实512MiB整机余量、受限root完整升级/回滚、双核心与四目标平台资源证据亦继续待验；无需购买机器或接触生产服务器。VERSION仍0.4.7，Draft PR #28不合并、不发布、不部署。

本批本地完整测试及资源专项的实际通过/环境skip数量以PR本批记录为准，环境skip不计通过。准确e57包提供的固定sing-box对新服务端/客户端配置parser成功，本地实际启动仍报netlink EPERM；不改安全权限、不记链路通过。独立长时CI先运行同包1/10/50连接各2秒的真实CONNECT preflight，明确不替代600秒资源验收。

独立审查识别并修正了长测跨过3600秒会话生命周期的问题：只在首个空载结束后和每个负载阶段开始前显式重新登录，登录耗时单列、不计入空载或固定负载；生产会话TTL、过期/注销后拒绝保持。真实应用/临时SQLite回归通过模拟越过一小时的时钟，验证原cookie失效及边界重登后的健康检查。另强化helper已退出但子进程仍存活的整组TERM/KILL/消失验证，只有清理完成才发布passing response；缺少CPU/RSS/延迟或非有限数字的报告拒绝接受。

### 972da2c 长时受限回归的原始结果

准确候选 `972da2c98e6fbbf95e85e907b142e5bab4755ecb` / tree `1ace8a8e544173c502dc82fc0c8681ee6a199090` 的原八组、11 jobs、全部步骤 attempt 1成功；[独立长测](https://github.com/ForceMind/V-UI/actions/runs/37827446081)于2026-10-08 18:51–20:22 UTC完成，亦为attempt 1全部步骤成功。[原始ZIP](https://github.com/ForceMind/V-UI/actions/runs/37827446081/artifacts/11577056125)下载摘要 `8742bd71d273b4f7b3f3e08c8afdcd60187db60b6814a005eac1f05113f3f459` 已独立复核，39阶段全通过。

- 实际限额一核、512MiB、零swap；worker运行中读取的累计peak478,109,696字节（455.961MiB），memory.events.max和三种OOM均0，没有名义超额、未用一页容差。安装阶段已达到此累计峰值，不能把它写成320MiB安装工程预算通过。
- 面板空载1800.001秒，平均单核CPU0.0462%，末端current218,533,888字节；加单sing-box空载1800.010秒，CPU0.0563%，末端current316,780,544字节（302.105MiB）。均有60份采样。包含worker、文件页和内核，不能证明160MiB空载预算或真实整机余量，也不报告瓦数。
- 1/10/50持久连接各600.000/600.002/600.007秒，600/6000/30000个64KiB请求，准确目标连接数1/10/50、零错误；最大响应6.22/11.28/35.11毫秒，服务组平均CPU0.087/0.218/0.890%。仅固定本机协议/速率，不是最大吞吐或公网延迟指标。
- 每档新单连接恢复、面板恢复、UUID/CA真实负向、零送达/无客户端DIRECT、注销重放、备份恢复与清理通过。原始服务端目标连接2/11/51/110与持续连接+恢复及110条短控制交叉吻合；采样服务PID不变。
- 客户端/发生器实际位于服务组之外。发生器/目标峰值RSS37,632/38,032/43,952 KiB，客户端61,048/63,884/74,988 KiB，CPU记录完整；getrusage峰值不可相加解释为完整物理内存。长测独立构建内层包SHA-256 `a93718abaebacc2e1763d5ff488e4f388e7acb1a68395f0462ea5d348bc9d32d`，与普通短工作流的包分别记账。

退出后的systemd-run摘要另显示“Memory peak: 3.0M”，unit.log已not-found且展示默认infinity；这些退役值不替代worker在运行中读取的原始cgroup限额和峰值，差异原样保留。此结果不完成UI、证书叠加、双核心、24小时、整机或四平台资源验收。

### UI与导出资源诊断 profile（初版实现范围，后续结果见下）

新增独立 `interactions` profile 和显式PR标签 `run-low-resource-interactions` 工作流；普通八组、smoke三档和sustained固定合同不变。构建与浏览器安装在限额之外，服务仍512MiB/一核/零swap/原固定一页容差；worker1800秒、协调器1860秒、job45分钟。新head不得继承上节972da2c的长测结果。

使用本批准确离线包、假管理员、临时验证TLS证书以及100条明确的合成数据库节点。直接seed仅建立测试数据集，不伪称正常节点编辑API或100个真实监听器；产品功能编辑与实际运行继续由原部署门槛分别验证。100节点是本次数据量，不泛化任意大数据库或大日志。

外部headed Chromium/Xvfb分别观察单标签可见、实际隐藏、同登录上下文双标签，各60秒；逐秒读取真实document.visibilityState，不篡改它、不使用模拟隐藏冒充浏览器状态。记录两类状态轮询的请求数、状态码、失败数和重叠峰值；当前源码仍可能在隐藏页轮询，先按原样测量，不能预先宣称隐藏零请求。会话注销后两页均须跳转登录且停止轮询。

关闭面板标签、停止其轮询后依次测三个管理员订阅导出及保存规则预览，每个端点60秒、每秒10个请求（并发10），校验600个响应和稳定正文摘要；raw及sing-box还核对100节点确实导出。此为固定数据/速率，不新增公开协议支持，也不将管理员退出等同于撤销专用订阅授权。专用订阅rotate/revoke等功能仍由已有专门测试验证。

浏览器及导出客户端在独立临时systemd cgroup中，位于服务组之外；整个外部组的CPU、memory.peak和OOM计数包括Xvfb及脱离父进程组的Chromium子进程，getrusage另列且不相加。external_metrics采样截止helper退出前，未计入随后Xvfb包装器最后收尾。清理针对整个外部cgroup并验证无存活成员，同宿主争用依旧存在。服务每10秒及观察到driver阶段变化时保存cgroup计数与进程快照；阶段标签采集最多约1秒对齐延迟，不能把这些近似窗口称作逐阶段精确CPU峰值。所有失败保留日志/JSON，只有清理成功且完整来源/时长/数据/隔离证据通过才接受。

本地暂无Xvfb，不能以本地无浏览器/结构测试替代准确CI的真实隐藏状态。真实假CA证书签发/续期与流量叠加、大日志及更大规则/节点数据集仍待单独补齐；静态假证书不算实际证书任务，真实Certbot及HTTP-01 responder必须计入服务工作，Pebble/浏览器/发生器单独记账。不购买或访问生产机器，不合并、发布或部署。

#### 首次真实UI诊断失败与原生可见性修正

`4bb27fc53cb747323ff7a79efbc29a17ce42e7e2` 的[首次UI运行](https://github.com/ForceMind/V-UI/actions/runs/37843285811)未通过；[失败附件](https://github.com/ForceMind/V-UI/actions/runs/37843285811/artifacts/11579325032) ZIP SHA-256 `9ab8740d2bf7aa5f74ccf1fcd97d423ec530a69ae8a074111b8c58d48fcc2c5d`已核验。真实可见标签60.001秒取得system20次/core6次、26个200、完整请求最大重叠2；隐藏阶段未观察到实际hidden，严格中止，后续双标签/导出/注销不记通过。服务peak478,121,984字节、外部浏览器组peak435,318,784字节，二者OOM均0，外部整组清理已确认；资源通过本身不能覆盖场景失败。

已核对固定[Playwright 1.57主帧初始化源码](https://github.com/microsoft/playwright/blob/v1.57.0/packages/playwright-core/src/server/chromium/crPage.ts)及[启动参数](https://github.com/microsoft/playwright/blob/v1.57.0/packages/playwright-core/src/server/chromium/chromiumSwitches.ts)：框架默认主动模拟页面始终focused/active，并禁用后台节流/后台渲染调度。因此只调用bring_to_front仍不能把测试解释成原生标签可见性。本次修正仅撤销这些测试框架覆盖：在私有临时目录完整复制固定1.57的automation driver/package并保留许可证，只将crPage.js唯一的原session初始化enabled:true改为false，记录前后SHA和精确替换数，通过当前helper进程的driver入口选择副本，退出后还原入口并删除副本；不改已安装包、Chromium二进制、生产源码或核心pin。启动时移除三项后台调度禁用参数，不设置/伪造visibilityState，不冻结页面，不注入visibility事件。不能仅从新的CDP session发false就假定释放了原session的浏览器capture：固定Chromium的[浏览器端实现](https://github.com/chromium/chromium/blob/143.0.7499.4/content/browser/devtools/protocol/emulation_handler.cc)仍保留原session自己的capture handle，因此从原session初始化就不启用它。依然等待并逐秒核验实际浏览器状态，拿不到hidden就失败，不以模拟值或skip换绿。实际效果待修正head重新执行。

同一4bb候选的portable x86_64-musl源码来源API明确返回HTTP403 rate limit exceeded，该job失败且附件步骤跳过；其余三个portable jobs成功。保留这次上游失败，不改核心pin/凭据，不把未完成的准确四目标套件写成通过。此处修正和文档同批提交，不为单段文档另触发完整验收。

### 4f6c62a 原生UI/导出基线与最小轮询优化

准确 `4f6c62a16d6f057a5e7bde0203e005a8ef622841` 的原八组/11 jobs和[独立UI诊断](https://github.com/ForceMind/V-UI/actions/runs/37845908565)均attempt 1、全部步骤成功；其他标签事件对应的90分钟workflow按设计skipped，不当作该head的长测通过。[UI原始ZIP](https://github.com/ForceMind/V-UI/actions/runs/37845908565/artifacts/11580745031) SHA-256 `0a55648249de2567d40e15fe6a7cc450b18dbdf480d2c05952f8c61e10808477`已双重核验，worker34阶段和driver8阶段全通过。内层包SHA-256 `3809e575e02024abdde4477938723716b3fbc9a0bef72a623f2c9b5987f90b07`。

- 可见/真实隐藏/双标签分别持续60.001/60.004/60.002秒，实际visibility采样60/61/60份。可见页system19+core5=24请求；隐藏页system20+core6=26；双标签隐藏26+可见24=50。全200、失败/待决0；24与26的细差来自窗口边界，不表示隐藏更耗CPU。既有最大重叠2是两类接口合计，不证明同端点已出现重复请求。
- raw、sing-box JSON、Mihomo YAML、保存规则预览均完成600个测量请求另加1个基准响应，各自至少60秒，100节点与摘要稳定、零错。约50秒服务采样窗口CPU分别21.76%/17.79%/60.39%/4.29%；这不是端点最大吞吐、精确全阶段CPU或所有数据量保证。
- 注销后两页各收到一个401并跳登录，随后11秒无新状态轮询；所有清理通过。服务组一核/512MiB/零swap，peak477,908,992字节（455.770MiB）；外部组peak494,587,904字节（471.676MiB）。双方max/OOM均0，互不相加作为服务预算。driver原文件/副本摘要及唯一布尔改动与本地独立比对一致。

这个基线支持减少隐藏页的周期状态请求。本轮最小产品候选（结果待新head验证）将3秒/10秒固定interval改为可见时、上次请求完成后的timeout；隐藏时清除后续定时任务，显示时立即刷新，不叠加重复timer，正在进行的请求可以完成。每个状态端点单飞，5秒请求上界；手工/变更后刷新排在旧请求后取得新结果，避免复用重启前状态。慢请求跨显隐时最终再取一次新状态，不让旧响应冒充恢复后的刷新。

暂停隐藏轮询不能破坏会话失效。支持BroadcastChannel的同源标签仅广播固定字符串session-ended，不广播用户、Cookie或凭据，不使用Web Storage。收到提示或迟到401时，先以有界、单飞的/api/auth/me验证当前HttpOnly Cookie，避免旧会话的迟到消息踢掉已经重新登录的新页面；在途验证期间的新失效通知使旧结果失效并串行补验，只有确认当前401才停止轮询/跳登录。账户页同样忽略失效前发出的旧/me响应，防止陈旧账户面板重新出现，并保留有效账户的未保存用户名草稿；初始化重验超时则显示可操作的登录表单与提示，不留下空白页面。账户主动退出/修改凭据也通知其他标签。频道不可用时保留显示恢复时的服务器鉴权，不承诺这些旧浏览器具有即时跨标签通知；服务器权限校验始终保留。

新版资源夹具明确要求隐藏页周期状态请求为0、每标签每端点最多1个完整未结束请求，原生visible端点仍有活动；注销要求真实401、两页在共用15秒deadline内跳登录、后11秒停止轮询，并由独立worker用原Cookie再取得401。初始页面加载与明确的手工刷新不冒充周期轮询。这些更严格断言需新准确提交通过后才登记，不将4f基线当优化结果，不报告节能百分比。真实证书任务叠加、大日志、24小时、真实整机和四平台资源仍待各自验收。

本批最终本地完整套件578项：455通过、123环境skip；两份JavaScript语法、Python编译、文档合同及diff检查通过。Node回归覆盖显隐反复切换、慢请求、手工变更后新读、迟到401、验证期间新注销通知和账户初始化超时；不以这些确定性测试替代新head原生浏览器/受限cgroup结果。

### 69983e9 可见状态轮询实际结果

准确 `69983e929833cc8f0aea54a8dc7e483ccf706fd0` 的普通八组/11 jobs和[严格UI运行](https://github.com/ForceMind/V-UI/actions/runs/37852024588)另1 job均attempt 1、全部步骤成功。[原始ZIP](https://github.com/ForceMind/V-UI/actions/runs/37852024588/artifacts/11583196490) SHA-256 `e902dfb3796a2b144532bd1cd6e02a01666f54ca63d0cac91346d47dbaa5e180`已下载核验；内层包SHA-256 `f3f3bfec0706900457b7f38f5392187754d20f31859c398f6a1c5f6a7ce4e4d0`。

- 可见/隐藏/双标签各60.003/60.001/60.002秒，真实visibility样本61/60/60。可见仍19次system+5次core；隐藏从4f基线26次降为0，双标签从50降为24（全部来自可见页）。每标签每端点最多一个完整未结束请求，所有正向200、零失败/待决。
- 四导出各600测量请求+1基准响应、固定100节点/60秒，正文稳定且零错误；未把轮询减少算成CPU或功耗下降百分比。
- 注销阶段取得一个真实状态401后，两页在共用15秒期限内退出，后11秒停止轮询；另有原Cookie重放401。worker35阶段全通过，真实UUID/CA拒绝、代理恢复、备份恢复和整组清理继续通过。
- 服务一核/512MiB/零swap，累计peak479,522,816字节、max及三种OOM均0；外部浏览器组peak486,576,128字节、max/OOM0，单列记账且清理确认。此head未重复90分钟，不继承972长时结果。

### 真实假CA证书任务与流量叠加（本批实现，实际执行待验）

独立 `certificates` profile由PR标签 `run-low-resource-certificates`显式触发，普通八组及三档短门槛不变。服务保持一核/512MiB/零swap与原一基础页容差；worker1800秒、协调器1860秒、workflow45分钟。已安装包Python运行独立证书子进程，导入的database/manager/provider实际模块文件须属于该payload，提前设置专用数据目录；普通面板数据库须保持零证书/零job，避免第二manager抢同一队列。

真实ChallengeServer、CertificateManager及未经替换的CertbotProvider/Certbot在服务cgroup内；固定官方Pebble、私有TCP/UDP DNS、sing-box客户端与HTTP目标/发生器在另一临时cgroup完整计账。只将显式loopback test_directory/test_ca注入测试构造器，使用自有.test名称和假CA，不改主机信任，不接真实CA。Pebble禁止always-valid和authz复用；真实GET200挑战仅保存token摘要，不上传账户、私钥、DB或challenge内容。

10条持久CONNECT/VLESS/TCP/TLS连接共同保持600秒，每连接每秒一个64KiB请求，共6000次；本profile将十lane错开100ms，减少同步一秒批次留下的测量空档，原sustained默认调度保持。全部连接且每lane首个响应完成后才签发；置专用行到期后仅调用一次process_once，让实际due scheduler生成第二job，再确认不会生成第三job。两个job都必须在实际流量窗口内完成、各有窗口内真实代理响应及新HTTP-01 GET200；不扩大窗口、不补发请求掩盖无重叠。签发/续期须真实成功、SAN/密钥/链验证、serial/revision变化且旧材料字节不变。

在真实challenge返回前只读/proc核对Certbot的父PID、argv限定的CA/webroot/CSR、进程starttime和实际cgroup；不包装Popen或替换provider。每5秒保存服务CPU/内存/OOM和稳定worker/panel/proxy身份，独立严格验证样本完整性、有序时间和累计计数。新单连接恢复、面板恢复、原负向及清理继续必需；异常保留安全job状态/错误码和partial流量。external_metrics截至外部helper退出前，最后包装器清理另行验证。

这批测试的是实际签发/自动续期资源与固定流量叠加，代理TLS仍使用测试静态材料，不称在线节点证书热切换或失败续期保旧已在本profile验证。大日志、更大数据量、24小时、双核心、真实整机及四平台资源资格仍分别待验；无合并、发布、部署或付费资源。

本批最终本地完整套件588项：465通过、123环境skip；新证书资源10项通过（含反例、实际HTTP-01 handler响应不变、外部CA不另起responder），文档、Python编译、workflow解析和diff检查通过。独立复审所报证据规格/完整记账、PID及时间绑定、非零退出、失败partial、namespace模块来源及service样本缺口均已修复并复审无阻断。真实Certbot/Pebble重叠以新准确head CI为准。

#### 4823c6b 首次证书叠加失败与确定性假账户修正

准确 `4823c6bb10c65ca944d2165ea46a3b67dcb2e88f` 的[首次证书运行](https://github.com/ForceMind/V-UI/actions/runs/37853923682)失败，不能登记整批通过。[失败ZIP](https://github.com/ForceMind/V-UI/actions/runs/37853923682/artifacts/11583468279) SHA-256 `c6ba8323f5c503c41816593fe13cbc7352808d347840b063cf348e57f767e951`已核；同head普通八组另行通过，不覆盖本失败。

原始证据中两条DB job均succeeded、serial/revision不同、旧材料保留；10条连接600.003秒完成6000响应与一次新单连接恢复，零错误。服务peak477,880,320字节、max及三种OOM均0。但只有首次签发的三次HTTP-01 GET200和一个Certbot进程观察，续期没有新challenge/第二PID；严格验证据此报错，后续本profile的负向/备份等阶段未执行。不是因为OOM而失败，也不能只凭两个组件写了passed覆盖缺失证据。

核对固定[官方Pebble v2.10.1源码](https://github.com/letsencrypt/pebble/blob/v2.10.1/wfe/wfe.go)发现：新授权条件使用rand.Intn(100) > authzReusePercent，因此PEBBLE_AUTHZREUSE=0仍可能复用旧授权；负值不被配置解析接受。原始现象与此分支一致，但旧附件没有Pebble内部日志，不能声称捕获了那次随机数或内部选择。

修正仅在私有测试数据内：首次签发后将实际fake ACME账户目录原子移到retained-first-account并完整保留，未经修改的CertbotProvider在原路径为同一证书的自动due续期注册新的fake账户；断言新账户私钥与旧账户不同，不输出钥匙或其内容。这样无既有授权可复用，仍要求两次实际新HTTP-01、对应Certbot PID/cgroup、精确流量重叠和全部原门槛。明确这是新fake账户强制重新验证的续期资源场景，不代表普通同账户续期行为；不修改官方CA二进制、生产代码或重跑碰随机绿。GET观察在请求开始绑定phase，避免迟到响应归入下一job。

本地已用固定官方Pebble与真实Certbot验证三项ACME测试（含失败挑战和原普通签发/续期）。新增测试强制假CA100%授权复用，保留移走旧账户后自动due续期仍取得新的真实HTTP-01并更新材料；本地真实通过不替代修正head的600秒/受限cgroup测试。

修正批最终完整本地套件590项：466通过、124环境skip；真实Pebble/Certbot三项另行实际通过，不把全套中的环境skip改写为通过。独立11项资源测试/4项ACME fixture测试通过，复审无阻断，文档/编译/diff通过。修正后的完整受限证书profile仍须准确新head实际运行。

### b419999 新fake账户证书重验证与流量叠加结果

准确 `b4199992fd250a9466b5f5ee840534272d7031af` 的[证书运行](https://github.com/ForceMind/V-UI/actions/runs/37858552475)与同head普通八组全部attempt 1成功：合计9 runs、12 jobs、130步骤。[原始ZIP](https://github.com/ForceMind/V-UI/actions/runs/37858552475/artifacts/11585941065) SHA-256 `d5ad407158a3f53057dee6ed3b669213b0980bb035c068e41d5eaeb9fd37a85b`及严格validators已独立复核；内层包SHA-256 `92fa97c8d4225b685d62efff27826ab13e23bd1efb720b8a34c81e9c53b3a6a7`。

- 32阶段全passed/complete；服务一核/512MiB/零swap，累计peak477,786,112字节，max及三种OOM均0。峰值在安装阶段已达到，不称证书任务自己的独立峰值；外部CA/DNS/客户端组另计peak56,131,584字节、OOM0。
- 10持久连接600.002425833秒、6000个64KiB响应、零错误；最大响应5.33ms为该本机固定负载样本。之后新单连接/单请求恢复通过。
- 两job约2.664/2.612秒，各3次真实HTTP-01 GET200、不同token摘要、两个独立Certbot PID均在服务组；精确job窗口内27/26个代理响应。新fake账户与原账户三个文件保留、不同key均有证据，证书serial/revision改变且旧材料不变。
- 121份服务采样跨601.54秒，worker/panel/proxy PID及starttime稳定；普通面板DB不含测试证书job。后续UUID/CA真实拒绝、零送达/客户端无DIRECT回退、恢复、注销及整组清理均通过。

4823首次失败保留。此结果只覆盖新fake账户强制重新验证的自动due续期与持续流量叠加，不泛称普通同账户续期或在线证书轮换；不继承972长测或699 UI结果至此head，不称完整512MiB VPS资格。

### 大数据与合成日志停机备份 profile（实施范围）

新增独立 `data-backup` profile及显式 `run-low-resource-data-backup` PR标签。普通八组、既有三档短回归、原一基础页容差及所有OOM拒绝保持；本profile仍一核/512MiB/零swap，worker1800秒、协调器1860秒、job45分钟。

固定1000条直接seed的合成VLESS/TCP/TLS行，不冒充API创建1000监听器；前后断言无托管核心启动。通过真实PUT/If-Match保存512条direct域、512条proxy域及64条内网域（各两个合成DNS地址），并验证缺If-Match 428、旧revision 409、2049项越界422均不改变保存内容/版本；管理员导出匿名401。公共grant只选合法256节点子集，不绕过产品上限。

四管理员端点各一个语义基线、30次串行请求及一批10并发请求，总41次；每端点120秒上界、无重试，不预先承诺大数据持续吞吐。验证三节点格式的1000个唯一假UUID、保存规则DIRECT/FORCE_PROXY及内网DNS语义、稳定正文摘要、无服务端材料字段；raw解码后亦检查。外部客户端置独立临时cgroup计CPU/内存/OOM并整组清理；服务阶段指标将四格式合计，外部逐端点时延不冒充精确逐格式服务CPU。

真实运行中backup必须因已有panel lease拒绝、无成功产物且面板仍健康。停止全部面板/核心后，在服务cgroup内用有界1MiB文本块生成256MiB非稀疏合成日志及64个4096字节小文件；文本块可重复，记录实际压缩比，不使用零填充/稀疏文件。预检至少4GiB磁盘余量，记录生成前余量、data与安装根的allocated file bytes；不清全局缓存或把用户备份数据套用release cold-cache提示。

备份/恢复调用同一已安装Python及已安装app.release_tools；校验来源、真实归档的日志文件集/大小/摘要/0600权限。备份后修改一节点、规则和日志字节；错误SHA必须拒绝且当前数据、CURRENT与recovery列表不变，正确同根恢复后验证SQLite完整性、原业务字段/规则/日志摘要、所有旧会话与grant撤销、恢复前改动数据完整保留于recovery/before-*及无残留staging/journal。重启后旧Cookie401、旧订阅404，新登录/三格式导出/规则正常，token不入面板日志。DB文件整体摘要不要求恢复前后一致，因为撤销授权必须修改DB。

产品没有日志读取/清空/轮转API，核心输出目前为DEVNULL，systemd运维日志由journalctl读取。本批只将普通大日志文件作为真实停机备份负载，不宣称产品日志实时增长或轮转验收。24小时、双核心、受限root升级回滚、四平台资源和真实整机余量仍分别待验，无新产品协议/日志范围、合并、发布、部署或收费资源。

实现存在或本地小尺寸控制流通过不等于256MiB日志/受限资源验收通过；本profile的准确候选执行结果、原始artifact摘要与失败记录以[PR #28当前结论](https://github.com/ForceMind/V-UI/pull/28)逐head登记，不能继承旧head。最终本地完整套件与独立复审结果也在同批PR记录。

### 55712b5 大数据通过与同head UI证据缺口

[大数据运行37863904032](https://github.com/ForceMind/V-UI/actions/runs/37863904032)首跑通过；[原始artifact11587014480](https://github.com/ForceMind/V-UI/actions/runs/37863904032/artifacts/11587014480) ZIP SHA-256 `a15461e06d45c47938e2754d2a592108b00158b17f81a23ec4447404b72c9f08`已独立核验，准确source `55712b54c98a0fc87c888ea10868081f9d81729c`、45阶段完整通过。256MiB非稀疏日志和64小文件摘要、1000节点/大规则、installed备份/拒绝/恢复保旧与撤权均符合合同。

服务peak536,870,912字节恰触512MiB上限，memory.events.max=1971、三种OOM0；第一次触顶在备份阶段，无余量结论。四端点导出合计79.319秒、平均98.012%单核CPU；Mihomo重复请求最大11.838秒涵盖30串行+10并发，未单列并发且不含baseline，不称持续吞吐或低功耗预算达标。归档88,308,837字节、展开269,358,446字节、压缩比0.32785。

同head普通八组/11 jobs均attempt 1逐步成功，三档短峰值512/384/320依次477,589,504/402,653,184/335,544,320字节，max0/70/143、OOM0；后两档触限。证书叠加也在该head成功，原始artifact11587613403 ZIP SHA-256 `c305fe0607849fdb10a04d3c76049aa742702f3983ccf187d50fab585592d3a1`：32阶段、600.001758秒/6000响应、每job26个窗口内响应，服务peak478,642,176/max0/OOM0。独立长测另行保留该准确head证据，完整结果见PR。

但[同head UI汇总37864443916](https://github.com/ForceMind/V-UI/actions/runs/37864443916)未通过；[失败artifact11587896513](https://github.com/ForceMind/V-UI/actions/runs/37864443916/artifacts/11587896513) ZIP SHA-256 `ac7830952ab8f5380096bc85f7da5674efcc8cf934adc18d2a9b7ce2fc897dbe`已核。三个轮询窗口与四导出窗口通过，隐藏0请求、两页实际转登录且随后11秒无轮询；服务peak480,030,720/max0/OOM0。但是logout阶段的两类状态请求与401计数均空，严格validator拒绝；后续worker旧Cookie401、代理与备份等阶段未执行，不以页面跳转替代服务器拒绝证据，不把helper的passed改写为整组成功。

源码确定的夹具缺口是：新建页面只等待静态logo，没有证明真实登录初始化完成；请求计数只接纳阶段开始后发起的system/core请求，不记录auth/me或跨阶段在途请求。旧附件无足够时间线，无法确认那次实际401来源，保持未知，不猜测成某条产品错误或改判通过。

本次修正仅补测试准备与观测：真实resource-admin用户名、八个必要初始化GET完成200、相关在途清空后，重新核实tab1 hidden/tab2 visible，再开始注销。安全timeline从建页起记录允许列表路径/方法、请求ID、标签、文档代次、开始/响应/完成及主框架导航，不记录headers、Cookie、body或query。保留至少一个完整status端点401；另外必须看到两页原dashboard文档的当前/auth/me完整401先于登录导航，不能拿/login加载后产生的401补数。真实logout200、共享15秒导航期限、完整11秒无新状态请求及独立worker旧Cookie401均保留。生产JS、原生visibility driver、资源上限和旧长测不改；修正head仍须自己的实际UI与最终汇总证据。

此夹具修正最终本地完整596项：472通过、124环境skip；独立35项相关回归全过，复审无剩余阻断。新增反例拒绝只有/login后401、错文档代次、缺完成/401后传输失败、初始化仍在途、POST冒充GET、logout实际响应晚于声明完成、超时导航及停止窗发请求；文档/编译/diff通过。本地回归不代替新准确head真实浏览器时间线与资源验收。

### 5d839dc 同一候选回归与未达预算

准确 `5d839dcd2dea9932afd684f8d441f8365f3a1754` 已核普通八组及四个独立profile，共12 runs、15 jobs、162步骤的最新结果全部成功。oneclick首轮固定官方核心下载遇HTTP500，安装验收未执行；仅该job一次重跑后attempt2通过，其余attempt1，首败保留于PR。

- [UI37867200130](https://github.com/ForceMind/V-UI/actions/runs/37867200130)：artifact11589555680 SHA-256 `e7a3a511f5b89a3f91b720446838419270da3a26387ea7d2353f456cb163a770`，35阶段，peak480161792/max0/OOM0。可见24/隐藏0/两页24请求，真实logout200、status401、两页原dashboard的/me401之后导航，11秒停止和旧Cookie401均有完整证据。
- [大数据37868350400](https://github.com/ForceMind/V-UI/actions/runs/37868350400)：artifact11589571727 SHA-256 `00a5429d7b25c39d1d77fae6d4b3a88b6904c477daca7fe94ecefaa3892f6976`，45阶段，256MiB非稀疏日志、1000节点及备份恢复通过。peak536870912恰触512MiB，max3333/OOM0；四导出合计50.259秒、97.654%单核CPU，不能称低功耗或有余量。与557相比源码负载未变，宿主差异不算优化收益。
- [证书37868350280](https://github.com/ForceMind/V-UI/actions/runs/37868350280)：artifact11589423664 SHA-256 `87bcb8916efcf46e35ecbfd887fd29a236369c8fa05fd6c87d61136cb6e59c9b`，32阶段，peak478920704/max0/OOM0。600.003秒/6000响应，两job各3次新HTTP-01及28/26次窗口内代理响应，保留新fake账户重验证限制。
- [长测37868349482](https://github.com/ForceMind/V-UI/actions/runs/37868349482)：artifact11592057558 SHA-256 `ac3c55a05b528eb06806dc4560d487f170649aa4cd07ef4996704ab2aa46acd2`，39阶段，peak478846976/max0/OOM0。面板/单core各1800.001/1800.013秒，CPU0.1284%/0.1491%，阶段末总cgroup206.55/297.99MiB；采样范围205.80–206.84/297.19–298.47MiB。1/10/50各600秒、600/6000/30000响应、零错误与逐档新单连接恢复通过。

160MiB空载目标不能登记通过：总cgroup含worker、文件缓存及内核，以上既不是产品净PSS，也不能仅凭总数将净服务占用判为超标。大数据严格语义通过仍不表示512MiB有运行余量或导出低CPU目标达成。四平台已有CI运行条件，资源验收尚需实现；24小时、双核心、真正过载恢复、完整受限root升级及真实整机OS余量仍分别待验。

### 包含全部进程的空载记账诊断（实施范围）

新增显式标签 `run-low-resource-accounting` 的独立accounting profile；一核/512MiB/零swap、原一基础页容差和所有OOM拒绝不变。worker900秒、协调器960秒、job35分钟。普通三档短门槛及其他长profile无需启动此诊断。

实际离线安装、HTTPS登录及100次并发鉴权读取后，面板和单sing-box分别观察60秒，在共同monotonic起点的0、5……60秒共13个计划点读取。每次包括完整cgroup子树成员、Popen来源的worker/panel/core角色及所有other成员，读前后PID/starttime/成员集合，完整前后memory.current/peak/stat/events/cpu.stat，以及每PID的RSS、PSS及Anon/File/Shmem分项、Private/Shared Clean/Dirty/Hugetlb和Swap。每次整体及逐PID时窗保留；读数非原子。缺失、权限失败、PID复用、移组、阶段身份变化或漏槽均拒绝，不补零、不静默省略。

采样器及JSON写入继续计入原组，不移走产品进程、不清缓存。窗口内只核进程存活，HTTPS健康检查位于观察前后；实际60秒预热诊断不替代30分钟空载。按[Linux proc文档](https://docs.kernel.org/filesystems/proc.html)，PSS按共享映射比例分摊；[cgroup v2文档](https://docs.kernel.org/admin-guide/cgroup-v2.html)的总记账覆盖子树及匿名页、页缓存与内核等。进程PSS与memcg计费不是同一口径，未映射缓存和内核不能由进程映射完整说明。私有页与Hugetlb单列，不强制舍入后的分项逐字节相等；不以RSS之和冒充去重物理内存，不用各进程不同时刻最大值之和称峰值，不从总数扣worker或缓存来宣布160MiB通过。

此批先取得可复现来源分解，再决定产品优化点。实现及本地parser/身份反例通过不等于准确新head的受限记账已经通过；结果另按实际CI登记，5d完整证据保留。

本批最终本地603项：479通过、124环境skip；新记账7项及独立sustained 13项另行通过（1环境skip），复审无阻断。保留一次603项失败：原单测将生产1秒槽缩成30ms导致宿主调度失约，随后subTest外引用未赋值result又报错；修正仅使该单测使用真实1秒槽和两tick，并将断言留在subTest内，生产600秒及漏槽拒绝不改。最终完整套件重新通过，文档/编译/YAML/diff检查通过；准确head的CI诊断仍待执行。

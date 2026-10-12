# 完整受管运维候选收口（2026-10-12）

[PR #33](https://github.com/ForceMind/V-UI/pull/33) 的准确软件提交
`f50942f1b399c7c397df532d04f9143e2527aaa8` / tree
`16e3bcad057b113052475a861a1f9911c2859eff` 已完成普通八组 CI、同提交四目标包核验，
以及一次明确授权的空白临时 Ubuntu 24.04 x86_64/systemd/cgroup v2 受管系统资格。
这是[完成矩阵](INTEGRATED_OPERATIONS_CANDIDATE.md)所列范围的候选收口。
PR 仍为 Draft；PR #28–32 的头及 master 未改，`VERSION=0.4.7`，
未合并、创建 tag/Release、晋升附件或部署到用户服务器。

全局入口须从完整可信 kit [显式安装 root-owned 控制组件](MANAGED_OPERATIONS.md)。
默认 `install.sh` 不自动添加它；服务用户菜单继续使用[已记录的包内命令](OPERATIONS.md#终端运维菜单)。
单独切换 CURRENT 不更新 root-owned 控制组件、launcher、安装记录或服务模板。
OpenRC 仅支持状态查询和纯候选准备，服务启停及依赖停服的数据动作仍明确拒绝。

## 普通 CI 与包内消费

以下均准确属于 `f50942f`：八组、11 个最新 jobs、119 个步骤全部成功。
链路为唯一一次 unchanged-code attempt 2；其余七组 attempt 1，首次失败见下。

| 工作流 | 最新 job / 步骤 | attempt |
| --- | --- | --- |
| [Documents](https://github.com/ForceMind/V-UI/actions/runs/38193879542) | 114632226809 / 7 | 1 |
| [Portable](https://github.com/ForceMind/V-UI/actions/runs/38193879544) | 114632226788 / 6；114632226846 / 6；114632226913 / 8；114632226931 / 5 | 1 |
| [One-command installation](https://github.com/ForceMind/V-UI/actions/runs/38193879546) | 114632226953 / 12 | 1 |
| [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/38193879559) | 114632227025 / 20 | 1 |
| [ACME](https://github.com/ForceMind/V-UI/actions/runs/38193879594) | 114632226950 / 12 | 1 |
| [Real loopback](https://github.com/ForceMind/V-UI/actions/runs/38193879566) | 114633998425 / 10 | 2 |
| [ToClash](https://github.com/ForceMind/V-UI/actions/runs/38193879547) | 114632226919 / 18 | 1 |
| [Deployment](https://github.com/ForceMind/V-UI/actions/runs/38193879555) | 114632226875 / 15 | 1 |

- 全量发现 1147 项：实际通过 1020、明确环境 skip 127；skip 不算真实场景通过。
  实际链路 101 项、独立 HY2 8/TUIC 8、ToClash 100 对照场景/40 项预设分别通过。
- 真实 Chromium 覆盖后台键盘及重复导航、Enter/Space/Escape、无效安全能力说明，
  未请求封禁 API、未调用主机动作程序；节点和证书浏览器也通过。源码浏览器、安装包浏览器
  与本次 root 终端测试是不同记录，不能互相替代。
- 10 个 GitHub 原始 artifact 的官方大小、摘要、准确 head/run 身份已核验。
  完整 kit 16 个固定成员及校验清单、控制依赖闭包已验证；源码 334 个 Git blobs/执行位
  重建上述 tree。测试临时 merge 源码包另存并独立核对，不冒充原始提交包。
- 四 target 加独立 oneclick 构建共五个运行包，各 187 个 manifest 成员、源码/控制文件、
  runtime pin、核心来源/二进制摘要和 37 个锁定 wheels 均核验，未替用旧 PR 包。
- 四目标真实包内 PTY、worker、SQLite/归档/撤权/恢复、健康检查及锁协议通过；
  stage 期间 x86_64-musl、aarch64-musl、x86_64-gnu、aarch64-gnu 分别有
  264、189、134、125 次旧 HTTPS 成功观察。原 oneclick kit 的额外非 root 消费有 82 次。
  这些包消费者的 root 安装信任及 systemd/cgroup 状态仍是模拟，不称真实 init 资格。

真实系统测试使用 [oneclick 原始 kit artifact 11721939584](https://github.com/ForceMind/V-UI/actions/runs/38193879546/artifacts/11721939584)，
外层 ZIP SHA-256 `98e9a54b850175ba32768b706e6fabb9ad12380a753469cf39ccf757ff98f358`；
`SHA256SUMS` 文件 SHA-256 `8225df08ae18e9151cd139f33dfcff0cbad585d83e15c12208dc352f1cafd888`；
x86_64-gnu 包 SHA-256 `3b06142251f300473aca41272b20711c1c602a4884235b0332e5ca4815f4f97d`。
摘要只证明字节身份，不认证未知来源，也不使候选代码成为安全沙箱。

## 真实受管系统资格

[run 38203515936 / job 114661844347](https://github.com/ForceMind/V-UI/actions/runs/38203515936/job/114661844347)
在上述准确 head/tree 的 attempt 1 成功，10 个步骤均成功、无 skip。
[原始 artifact 11726631325](https://github.com/ForceMind/V-UI/actions/runs/38203515936/artifacts/11726631325)
为 5,253 bytes，ZIP SHA-256 `35b5c95294cd979a4f729cdeb9f85ed186b5497332c93e68e51429c2f218f9c5`。
两份原始 allowlist 记录 `admission.json`、`managed-system.json`、官方日志和准确源独立核验通过。
admission 中 `qualification_executed: false` 只描述只读准入步骤；实际执行结果在 managed-system 记录。

29 条 phase 记录中，11 个必需阶段各出现一次；五个固定 checkpoint 各有 begin/passed，
共 10 条，无失败诊断。必需阶段为 baseline、control_installed、menu_cancel、stop_start、
restart、backup_restore、stage_activate_rollback、post_stop_failure、recover_restore、
partial_refresh、cleanup，不能把前缀或 checkpoint 重复算作阶段通过。

- 真实离线安装、拒绝隐式重装、同包升级、私有权限、非 root 面板/responder、HTTPS/认证
  与 HTTP-01 socket activation 由准确源的原 oneclick 夹具完成；新安装器随后实际安装
  root-owned 全局 wrapper/控制源码/receipt。root 和 worker 使用固定隔离 OS Python，
  实际解析为 `/usr/bin/python3.12`，不执行服务用户拥有的解释器作为 root。
- 13 次真实菜单动作包含状态、取消、停/启/重启、备份、恢复、stage、两次 activate、
  预期失败的恢复、recover-restore 和显式启动。11 次写动作各观察到一个稳定 worker：
  四个 UID 均为 999、四个 GID 均为 987、补充组为空、有效 capabilities 为零；
  PID/starttime、父进程、解释器/固定 argv 和 worker 退出均按准确源校验。
- stop 仍要求三个固定 unit 全部 inactive 且后代 cgroup 为空。重启恢复原拓扑。
  本次完成时间为 responder `122019374`、socket `122022061`、panel `122265043`，
  符合依赖关系的停止语义；完成时间不是命令发送顺序，也不能补写历史运行的缺失值。
- 备份/恢复验证合成 marker 还原、SQLite integrity `ok`、旧会话拒绝及重新登录。
  stage 保留 CURRENT 和运行拓扑，随后完成 A→B→A 的激活/代码回退；B 仅改合成 release ID，
  产品成员完全相同，不能据此声称真实跨版本/schema 迁移通过。
- 无效归档触发预期失败后，真实停服证明仍通过，数据/marker/SQLite 保留。
  构造的中断恢复还原原 marker、移除 pending journal 并保留 `interrupted-*` 副本；
  恢复完成仍停机，再明确启动。同 kit 的构造部分发布残留先拒绝普通重装，再显式 refresh 成功。
  这些是明确构造的故障场景，不是断电或磁盘故障实验。
- 清理记录 `owned_resources_removed: true`，三个 stop 返回码均为 0；删除前按准确源核对
  自有路径/权限/unit 字节/账号身份、inactive 与空 cgroup，仅移除本次登记资源并核验结果。
  无用户服务器、真实域名/CA、SSH、防火墙、sudoers、SUID 或新 capability 授权操作。

原件只导出有界脱敏记录，不包含完整交互文本、数据库或主机前后全量快照；部分成功断言
由准确源与阶段推进共同证明，不伪称存在独立原始 dump。此资格不包括新的浏览器验收。

## 首次失败保持原判

1. `f50942f` 链路 [attempt 1 / job 114632226906](https://github.com/ForceMind/V-UI/actions/runs/38193879566/job/114632226906)
   缺少必需 VMess untrusted-CA x509 原因日志，原始 [artifact 11723050987](https://github.com/ForceMind/V-UI/actions/runs/38193879566/artifacts/11723050987)
   已保留。只做一次不改代码/pin/凭据的 attempt 2，101 项及 HY2 8/TUIC 8 通过；
   不以超时替代拒绝原因，也不声称旧轮询不稳定性永久修复。
2. 旧 `1bb43366cb9690499ddecc875686616cbc6eccca` / tree `686f56e9db10166612a7310656dea651d3df623e`
   的 [run 38193111612 / job 114629906412](https://github.com/ForceMind/V-UI/actions/runs/38193111612/job/114629906412)
   attempt 1 失败，最后成功菜单动作是 stop。清理仅确认停服，
   `owned_resources_removed: false` / `privileged_child_cleanup_unconfirmed`，保留临时主机资源，
   不能称已删除，也不证明进程仍运行。[原始 artifact 11721947963](https://github.com/ForceMind/V-UI/actions/runs/38193111612/artifacts/11721947963)
   SHA-256 `a739d12bd1520f00587c990bc482c6812ac4c01847741a4c0539a34cc61fc04e` 完整保留。
   离线反例证明旧 harness 错把命令顺序要求成完成时间全序；历史缺少错误码/时间戳，
   仍不能唯一归因，也不能排除首次 start 在记录前失败。f509 只修正 harness/测试，
   保留真实三 unit 停服/cgroup 证明；产品、准入、root helpers 和 cleanup 未因此修改。

## 资源与未验收边界

准确 f509 [普通 deployment](https://github.com/ForceMind/V-UI/actions/runs/38193879555) 的
512/384/320 MiB 短 cgroup smoke 分别通过，峰值 276.68359375 / 275.29296875 /
265.5234375 MiB；各项 `memory.events` 均为零，CPU quota 为 `100000 100000`。
这不是完整 512 MiB 主机或持续负载证明，也不替代历史 opt-in。

[历史低资源收口](LOW_RESOURCE_CLOSURE_20261011.md)保持原界限：准确 a373 root 父组峰值
471.21484375 MiB；准确 0f6c 的 1000 合成节点/256 MiB 日志用例峰值 512 MiB、
`memory.events.max=883`、OOM 为零但余量为零。不得移植为 f509 的同提交长测成绩。
真实 1 vCPU/512 MiB 整机、24 小时完整负载与 160/320 MiB 工程预算仍未获完整资格；
不要求新租服务器、Oracle 或提供凭据。

其他未验收范围包括 ARM64/musl/其他 OS 的真实受管 root/init、OpenRC 写操作、任意未知包安全、
真实跨版本/schema 升降级、断电/磁盘故障。UDP/DNS、DNS-01、通配符等未来阶段不在本候选内，
XHTTP/HTTPUpgrade 公开导出仍阻断。合作式锁不隔离恶意同 UID 或绕过锁的写入。

## 后续文档 head 的证据边界

承载本文的后续提交仅变更五份 Markdown；产品、protected root helpers、harness、workflow、
VERSION 和 AGENTS 均保持 f509 字节。上述真实系统资格始终记为 f509 的运行，不改称文档 head
重验。文档 head 的独立审查、普通八组 CI 与同提交附件须在 PR #33 的准确提交验收记录中另列；
本文不预先声明未来 head/CI 已通过，也不为纯文档重复触发 root opt-in。合并、发布和主机部署
继续保持各自边界。

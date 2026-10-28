# 正式发布流程

正式Release是一次明确的公开写操作，不等同于PR、版本字符串或Actions候选附件。此流程只晋升经过验收的完整套件，不在发布时重新构建“另一份看似相同的包”。

## 当前分支与短入口

正式发布从默认 `main` 的准确 HEAD 执行；`dev` 用于集成，每版在 `release/vX.Y.Z` 独立开发并依次通过 PR 合入 dev/main。历史记录中的 master 名称和准确提交保持原文。`setup.sh` 当前只内置已公开 v0.4.9 安装器与整套 checksum 文件的可信摘要；本次维护不创建新应用版本、不覆盖旧资产。后续稳定版需先完成自己的发布资格及公开发布，再显式更新短入口的版本和摘要。

构建、四目标制品处理和实际安装验收放在 Actions，本地只保存必要文本证据。新增 `short-install.yml` 通过匿名公网读取确切源码的短入口并安装冻结正式套件；main 上使用 README 中的真实 main URL，核对入口字节与准确 checkout，复用真实 systemd/交互配置/全局菜单/重复拒绝/升级及恢复检查。

浏览器步骤默认保留但不执行，须按 CONTRIBUTING 的显式 opt-in 取得具体授权。本次发现原发布器仅检查 workflow 总结论，已补充准确 run_attempt 的全部 jobs/steps 成功校验、分页和重跑状态复核；任何 skip、缺项或失败都会阻止晋升。跳过浏览器的常规 CI 不替代完整发布资格。本次不会触发 publish.yml。

## 2026-10-27 v0.4.9 已批准发布批次

用户已批准实现、独审、正常合并和公开发布新 v0.4.9，旧标签与资产保持。执行记录集中在[一行安装计划](ONE_LINE_INSTALL_049.md)。
准确候选与正常主线均需自己的既定八组/四目标验收。发布器只晋升同提交受验资产，生成带脚本及完整 kit 摘要的一行命令，上传校验完成后才公开。
公开后派发 `online-release-acceptance.yml`，输入发布的完整 commit 和 `0.4.9`；在空白 Ubuntu runner 使用匿名公网资产、临时提供证书及真实交互安装/全局菜单复验。
该验收不修改生产主机或系统信任库；临时证书的端到端结果与既有 ACME 验收各自记录，不称真实域名签发。


## 2026-10-16 历史状态：0.4.8 已发布

[官方 v0.4.8 Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.8) 于 2026-10-15 10:57:29 UTC 公开，tag 准确指向 `a9f9b5d9c0dd2aada5e8f5f2270b2ce93440d5ff`；2026-10-16 只读复核为非 draft、非 prerelease、19 项资产。该版本仅包含安装器修复；其后双开关准确主线 `9a97ad13` 的 CI/四目标候选已验收，但没有新版本或公开 Release。源码 `VERSION` 仍为 0.4.8 不授权覆盖旧 tag/资产，也不代表这些功能已发布。下一次发布须独立冻结新版本、准确 CI/同源制品及明确发布授权；`publish=false` 仍是发布写操作。当时开发没有触发它。

## 0.4.8 发布前快照（历史保留）

[官方 v0.4.7 Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.7) 于 2026-10-13 01:26:40 UTC 公开，tag 指向主线 `c5d50ee14b08da57bdc13c150b4ed501e9484023`；2026-10-15 只读核对为非 draft、非 prerelease，元数据列有 19 项资产。该只读状态核对不重做包字节验收，也不表示部署到用户主机。已发布 tag 和资产保持不变。

当前 `VERSION`/应用版本为 **0.4.8（UNRELEASED）**，仅[安装入口退出状态、清理和引导失败修复](INSTALLER_EXIT_STATUS_048.md)。本次不执行 tag、Draft Release、Release 附件上传/晋升、公开发布或生产部署；`publish=false` 仍是创建草稿和上传的发布写操作。准确候选、主线 CI 与同提交制品必须重新验收，不能借用 0.4.7 的旧包或旧成功记录。0.4.8 未来若获发布授权，仍须满足下方完整人工门禁。

该历史分支的 0.4.8 安装示例仅供当时正式发布之后使用，**现在不要执行**；必须先核对正式公开状态和完整 19 项资产。需要正式版的用户请使用[固定 v0.4.7 文档](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/INSTALLATION.md)，并先阅读[旧安装器的问题和排错边界](INSTALLER_EXIT_STATUS_048.md)。

## 0.4.7 制品来源与交付边界（历史准备记录）

产品代码集成基线02d8见[主线收口](MAINLINE_OPERATIONS_CLOSURE_20261012.md)，其后93c8的准确主线CI与同源四目标/19资产验收见[PR #36](https://github.com/ForceMind/V-UI/pull/36)。后续README或其他文档提交同样必须完成自己的候选、正常合并、准确主线及新制品验收；不能晋升旧基线包冒充新头。

以下保留 0.4.7 发布前的来源约束及准备背景；当时“待验/未发布”不覆盖上方已核实的公开状态。最终结果记录在承载 PR/Release 账本，不为把自身 SHA 写回文档再创建循环提交。部署到用户主机是单独的操作。

实际受管root/systemd资格只属于f509原run；普通包消费、整机/24h及其他未验边界保持。原始oneclick kit16成员、发布19资产/18校验行、Actions artifact数量是不同计数。下面的准确HEAD/最新八组/同来源/不得覆盖门禁始终适用，不能因已有发布意向跳过。

## 2026-10-12 合并前集成候选状态（历史，未发布）

以下“未合并/master a6aa/新head待验”保留候选形成时的事实，不替代上方当前交付准备和固定已验代码基线。

- 同一候选沿历史继承 PR #28–33 的低资源软件修复、后台安全中心说明、非 root 服务用户菜单、可信候选准备和全局受管菜单，见[完成矩阵](INTEGRATED_OPERATIONS_CANDIDATE.md)。`VERSION` 保持 `0.4.7`；PR #28–33 尚未合并，主线仍为 `a6aa84d`。本次未合并 master 或创建 tag/Draft Release/公开 Release，未向 Release 上传或晋升资产，也未操作用户主机
- 全局 `sudo /usr/local/sbin/v-ui` 需要从完整独立可信 kit [显式安装 root-owned 组件](MANAGED_OPERATIONS.md)，默认 `install.sh` 不添加它。systemd 提供服务控制和依赖停服的数据事务；OpenRC 仅查询与纯候选准备，拒绝服务启停及依赖停服的数据写动作。单独选择 CURRENT 不升级 root-owned 控制源码、launcher、安装记录或服务模板，不能把候选准备当作完整系统升级
- [PR #33](https://github.com/ForceMind/V-UI/pull/33) 的文档基线 `09f2cf4ec39adfbb345d8aa40b94558c64c93936` 已完成普通八组/11 jobs/119 steps 全部 attempt 1 与 9 个原始 artifact 核验。真实 root/systemd 资格只属于 `f50942f` 的 [run 38203515936](https://github.com/ForceMind/V-UI/actions/runs/38203515936)，11 个必需阶段含 cleanup、自有资源清理确认；没有 09f2 root 重验。源码、普通 CI/包消费、root/init 资格与公开发布不得互相替代
- 本次新候选另含 VMess CA/SNI 负向的有界证据收集修复，移除被动 2 秒轮询，保留新日志行上的真实 x509/指定原因、零送达、无 DIRECT、清理及停进程后的送达检查。本地合成 RED 说明契约缺口，不证明历史真实网络失败的唯一原因。新 head 尚待自己的独立审查、普通八组 CI 和同提交原件核验；不预填自身 SHA 或未来成功，也不能晋升 09f2/f509 的旧包冒充它
- [受管候选历史](MANAGED_OPERATIONS_CLOSURE_20261012.md)与[低资源失败记录](LOW_RESOURCE_CLOSURE_20261011.md)保留。真实 1 vCPU/512 MiB 整机、24 小时完整负载、160/320 MiB 工程预算、其他平台真实 root/init、跨版本/schema 迁移与真实断电恢复尚未获资格。既有协议、核心 pin、SNI 与网络范围不扩展，UDP/DNS 等未来阶段不纳入本次，XHTTP/HTTPUpgrade 公开导出仍阻断
- 当前发布脚本的完整聚合是下文所列 19 项资产，与 oneclick 原始 kit 的 16 个成员及 Actions artifact 数量不同。`publish=false` 会创建 Draft Release 并上传，仍是发布写操作，绝非 dry-run；须在下述人工门槛及明确授权下执行。候选通过不解除发布或主机权限边界

## 2026-10-06 发布准备背景（历史）

以下至“前置条件”前保留当时的状态、旧套件核验和后续要求；不把旧记录的待验收项或通过结论移植给上述新候选。

当前 v0.4.7 [REALITY/Vision 已完成主线收口](REALITY_VISION_CLOSURE_047.md)：独立审查、准确候选八组和正常合并后的准确主线八组已验收。首次 REALITY 前置 HEADERS 断言失败、最终 master 继承 VMess/Mihomo CA 被动轮询缺少 x509 原因的首次失败及 unchanged-code attempt 2 通过均保留；重跑不表示旧轮询问题永久修复。[PR #25 XHTTP 刻画](XHTTP_CHARACTERIZATION_CLOSURE_048.md)已正常合并至签名 master `66dbe70cfa86fe140fa1b69d3896244ab17a0e3c`，与候选 `f7996c579418d1ffe5df06fc12677cf22a42e034` 的 tree 相同，独立审查及两端各八组/11 jobs/全部步骤 attempt 1 成功。仅测试/文档，产品 VERSION 仍为 0.4.7，公开 XHTTP/HTTPUpgrade 支持保持阻断。产品范围冻结，先完成本版发布准备和交付决定，再推进独立 UDP/DNS 实现；现有只读审计不构成实现验收。

截至 2026-10-06 本次检查，未晋升附件、创建 Draft Release 或执行新建 tag/公开 Release/生产部署；源码、原始 CI artifact、已核验附件与公开发布分别记录。完成代码、PR、CI 或发布准备不等于获准创建发布草稿或公开发布；任何发布操作仍受下述人工门槛与明确授权边界约束。

## 0.4.7 准备状态（2026-10-06 历史）

- v0.4.2、WS 0.4.3、gRPC 0.4.4、HY2 0.4.5、TUIC 0.4.6、REALITY/Vision 0.4.7 与 PR #25 刻画均有各自准确主线验收；这些历史阶段未因此成为正式 Release。当前准备一个累计产品 0.4.7，不回填旧阶段发布、不将刻画称为产品 0.4.8。
- 本次只读检查的 [Releases API](https://api.github.com/repos/ForceMind/V-UI/releases?per_page=100) 返回空列表；已存在历史 tag `v1.0.0` → `196c9b97ded5ef2391e01cefff408f516fbac508`，保持不动，不能据此推导本轮已发布。未发现 v0.4.x tag；本轮未创建 tag/Draft Release/公开 Release 或晋升附件。
- 此最终状态文档自身的候选审查、准确候选八组、正常合并和准确主线八组必须独立完成。本文不预先宣称自身未来提交通过，也不填入未知 SHA；结果记录在对应 PR/发布准备证据中，不为回写自身验收再滚动产生新提交。
- 文档收口后的最终当前 master 通过八组后，才选择它自己的一键套件和三份 portable artifact，核验完整四目标资产。旧 `6b049262` 的套件 metadata 只证明当时来源/大小/过期时间，未证明归档字节、内部 MANIFEST、许可证或聚合摘要；它不是最终套件验收。PR 候选或 `66dbe70c` 的包也不能冒充后续文档收口 master 的包。
- 最终准备检查须下载同一准确 master 的已验收附件，逐项核对 Actions archive digest、包内源码/版本/target MANIFEST、第三方许可证/来源、安装器与 helper/source/说明资产、RELEASE.json 和最终完整 SHA256SUMS；不得发布时重建替代。四目标 package 验收、附件晋升与公开发布分别记录。
- 到上述核验完成前，只能报告“范围冻结、发布准备中”，不能报告完整套件已核验或已发布。完成准备仍不授权创建 Draft Release、tag、上传/晋升或公开发布；保留下面的人工明确确认与真实发布后核验边界。

## 0.4.7 打包范围说明更正（2026-10-06）

[PR #26](https://github.com/ForceMind/V-UI/pull/26) 的候选 `0171f1f010f63bf73fde54c330309274e3d40f2b` 与正常合并 master `a21e31e1df4fd36a6994da780c29d051374279a9` 已分别完成八组/11 jobs/全部步骤验收。候选各组 attempt 1；主线除链路外七组 attempt 1，链路首次继承 GeoSite 下载连接重置后 unchanged-code [attempt 2](https://github.com/ForceMind/V-UI/actions/runs/37494753598/attempts/2) 成功。首次失败记录保留，不把重跑当作外部下载问题永久修复。

该 master 的 13 项最终资产已做独立字节/来源核验：四个目标 ZIP 合计 376,881,626 字节，Actions archive 摘要、包内源码/版本/target MANIFEST、许可证/来源、源码包、安装器/helper、RELEASE.json 和 SHA256SUMS 一致。此结论仍作为旧套件完整性证据保留；未执行包内软件，也未上传、晋升或发布。

核验同时发现打包 UI 在 `core === 'xray'` 警告中使用了全局“REALITY 未验收”的过时说法，MANIFEST 也仍只描述 VLESS/TCP/TLS。旧套件因此被后续修正候选取代，原因是发布展示质量，不是摘要不符或源码替换。修正仅明确 Xray REALITY 未验收/公开拒绝、保留 sing-box REALITY/Vision 有界验收及成功分支，并把累计导出范围指向 [COMPATIBILITY.md](COMPATIBILITY.md)。协议/编辑/导出行为、版本、核心 pin、应用 UDP 范围及 XHTTP/HTTPUpgrade 阻断均不改变。

本次修正仍须独立审查、准确候选八组、正常合并后的准确主线八组，以及该新主线自己的四目标最终套件核验。新增单元回归读取真实 `prepare()` 结果与合成构建 ZIP 的实际 MANIFEST；deployment 门槛也检查实际安装包清单。合成夹具不等于四目标真实包验收，旧通过不能冒充新通过；结果记录在对应 PR/发布准备证据中，不预填未来 SHA 或递归回写成功声明。上述新验收完成前仍为发布准备中，任何 tag、Draft Release、上传、晋升、公开发布或部署均须另行明确授权。

## 前置条件

1. 按依赖顺序审阅并合并前置PR；目标必须是当前默认分支的**准确40位HEAD提交**，不能给一个旧测试结果配新源码。
2. VERSION与main.py版本一致，CHANGELOG、兼容矩阵、证书和安装文档已更新。
3. 默认分支push会触发全部门槛。相同提交必须完成test、toclash、loopback、release、acme、oneclick、portable和docs八组工作流，**每组最新一次**都为completed/success。失败、取消、缺失或排队一律阻断。
4. One-command 任务生成 x86_64/glibc 发布套件；portable matrix 生成 ARM64/glibc 与两套 musl artifact。发布脚本只聚合同一 exact-head 提交且已通过验收的这些附件，不在发布时重建。
5. 检查原有tag/Release不存在；正式版本不覆盖、不悄悄替换已发布资产。

## 人工触发

在默认分支选择 `Publish verified V-UI release` 工作流，填写commit、version与完全匹配的 `RELEASE <version> <sha>` 确认文字。

`publish=false`为默认值：完成校验和资产上传后保留草稿；只有操作者明确设为true才公开。发布脚本先创建draft，上传全部资产并验证各自服务端digest，全部成功后才允许公开。中途失败保留草稿供审查，不误报已完成。

GitHub权限只在该人工工作流的发布job授予contents:write/actions:read；PR测试没有发布权限。输入经环境变量和参数校验，不拼接为任意shell代码。

## 发布资产

发布脚本 `scripts/publish_release.py` 的 `ASSETS` 精确包含以下 **19 项**；四目标包之外的入口与控制 helper 也必须来自同一准确提交，须完整提供每个固定文件。

- vui-linux-x86_64-gnu.zip：经过同提交 one-click/systemd 验收的固定离线包。
- vui-linux-aarch64-gnu.zip：经过同提交 ARM64 原生 portable 验收的固定离线包。
- vui-linux-x86_64-musl.zip：经过同提交 Alpine/musl 原生 portable 验收的固定离线包。
- vui-linux-aarch64-musl.zip：经过同提交 ARM64 Alpine/musl 原生 portable 验收的固定离线包。
- install.sh、install_system.py、platform_support.py、firewall_support.py、service_support.py、system_launcher.py：6 个同提交安装入口、平台/防火墙/服务 helper 与 canonical launcher。
- install_ops_control.py、system_ops.py、ops_worker.py、release_tools.py、v-ui：5 个同提交显式受管控制安装器、控制器、降权 worker、发布工具与全局入口。
- vui-source.zip：准确提交的源码。
- SHA256SUMS：全部固定资产的整文件摘要。
- RELEASE.json：版本、源码提交、release_id和平台元数据。
- RELEASE_NOTES.md：功能、安装、已知限制和运维说明。

发布脚本也验证包内manifest的源码与版本。远端artifact由签名下载URL取得，下载时不把GitHub授权头带给对象存储；完整artifact的digest必须与Actions元数据一致。

## 公开后检查

核对 Release 公开状态、tag 目标、下载文件摘要和文档链接。本批目标的传统入口为 `install.sh --version v0.4.9`；推荐入口是 Release 中按资产生成的一行受管安装命令。每次仍须确认完整 19 项资产并核验。自动目标选择复核须使用独立临时 Linux 机器；版本字符串本身不证明下载存在，也不赋予后续未发布主线功能资格。

外部CA域名验证、云安全组和真实用户网络需要用户自己的配置，不属于维护者自动取得的授权。不要把发布流程顺手变成登录真实VPS部署。

## 故障与撤回

上传失败时不直接重跑覆盖已有Release；先检查草稿，决定由操作者清理还是继续人工修复。已公开且有问题的版本应公开说明并发布新修订版本，不能偷偷改同一版本二进制。

默认分支变化会使目标提交检查失配，需明确选择新HEAD并等待它自己的全套测试。候选artifact过期则重新运行同提交的一键门槛，并核对其他最新结果；不能从未知备份恢复一个未验证的包。

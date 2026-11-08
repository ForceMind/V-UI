# P3 核心独立升级与回滚契约草案

状态：设计草案，尚未实现或验收。代码事实核对基线为4bd1d9a648a8096fbf2fa05314e35049a2408e7c。它不属于当前DNS候选PR145，不改变当前公开22、23发布门禁、核心pin或服务器。

本阶段来自ROADMAP的已批准P3；Go迁移继续暂缓。先把契约独审完成，正式实现保持独立分支/PR。首测版本已列明，仅核元数据；制品和兼容资格尚未取得，不能把本草案称为核心已可升级。

## 可观察交付

用户在“软件更新”中选择“更新代理核心”，看到sing-box和Xray各自的当前版本、可用已验证版本、服务是否需要短暂停止，以及“更新”“回退到上一版本”。默认推荐保持当前版本；只有存在该应用版本已验收的候选才显示可更新。一次选择、一次说明明确的确认后自动备份、验证、切换并报告结果。不要求用户填路径、URL、SHA或协议配置，不以任意上游latest作候选。

v-ui终端与授权远程维护使用同一任务和状态；面板进程停止期间由独立维护服务继续执行。显示“正在校验/已准备/切换中/验证中/成功/失败待处理”，不能把下载成功或desired选择当成正在运行的新版本。第一次必须同时提供真正不同版本的官方核心升级及回退证据，不能以同版本重装替代独立升级。

## 已核代码事实

- fetch_test_cores.py:13–54按四目标固定官方归档和SHA取指定成员，build_bundle.py:123–138将核心纳入payload manifest；system_launcher.py:90–105、app/serve.py:41–48将VUI_BIN_DIR固定为当前payload。
- app/services/core_manager.py中的路径在模块加载/runtime构建时缓存；CoreRuntime会resolve路径。core_runtime.py:393–413还锁定版本首行与真实配置检查。现CoreRuntime自己没有二进制摘要或独立核心身份字段；不能因此忽略外层启动时已有的整包验证。
- core_runtime.py:353–369的持久state严格为revision+enabled；apply先check、后换进程、最后commit，失败回原配置；stop持久禁用，close仅释放进程，recover尊重已提交停止意图。
- proxy_diagnostic_worker也固定sing-box版本和路径，不能漏改诊断或凭空宣称已有Xray诊断。此基线不存在DiagnosticXrayManager类。
- app/release_tools.py:590–711严格验证payload、READY和Python runtime。改包内核心、provenance、manifest或加文件都会破坏已有完整性；现stage/activate是整应用单位，不是独立核心接口。
- deploy/system_ops.py:204–237的root receipt保护控制代码、配置和OS解释器，不直接证明核心二进制；现封闭RPC拒绝任意core/version/url字段。
- app/release_tools.py:922–972只备份data及配置revision/停止意图，不备份CURRENT、root配置、payload或独立核心选择；现不存在选择记录。不得把新core_id塞进旧state或普通隐藏文件后假称已经备份。

具体类路径以实际仓库为准；上述行号是读取基线的位置，并非未来代码位置。

## 可信制品与兼容

1. 核心升级只接受ForceMind/V-UI明确发布并完成资格的独立组件Release；固定仓库和tag类型，禁止任意URL、fork、上传可执行文件或追随上游latest。组件Release不得抢占V-UI应用的latest；列表/选择必须严格区分应用与核心通道，旧应用升级流程保持。
2. 清单绑定组件ID、core名称、精确版本、四目标、固定官方上游来源/成员、原归档与实际二进制SHA、大小、V-UI构建/审查source40、完整checksum及兼容合同版本。拒绝重复成员、未知字段、路径越界、错架构/libc、缺摘要或未完成资格。
3. 可信锚沿用既有官方仓库Release的HTTPS/API身份及外部资产摘要模型，明确来源后才执行内容校验，不以包内自算hash授信。不新建长期签名密钥或Secret，不扩大Actions token权限。实现前须独审证明组件清单的信任入口；若现有模型不足，先列差距，不能用自签字节或approved布尔值替代。
4. 兼容表为已测的应用source/适配合同与核心制品的有限集合，不把一个成功case推成整个版本范围。除新增核心身份选择外，保留全部节点协议/凭据/高级字段，不能改节点DB来适配候选。首次不同版本须通过现有真实协议、TLS/错误认证、无DIRECT、中转与恢复门槛；版本/核心pin变更只发生在该独立阶段的明确资格批次。

## 选择、执行与事务

- 保留完整应用release及其包内核心为不可变回退基线。新增核心制品存放在受管独立不可变目录，选择描述符只能引用已验身份，不能接受裸路径、环境注入或软链接来绕过身份。具体布局按下方定稿选择执行并独审父目录替换保护；本草案不授权写真实主机。
- 每个core记录current、previous、candidate、精确manifest/binary摘要、目标、兼容合同及事务阶段；记录实际applied配置revision与原enabled/停止意图。desired、已准备及实际运行身份分别表达。旧state仅在bundle基线下兼容；独立核心使用显式schema2并绑定core artifact身份，不向旧exactkeys格式静默塞字段。旧工具的限制按下方入口矩阵说明，不假称新schema可自动挡全部旧activate。
- 实际主核心、proxy协调和独立诊断必须共用同一可信选择，版本检查来自已验descriptor，不移除check。启动时校验binary identity，并将进程代际与制品身份绑定；仅报告version字符串或watchdog PID不够。
- root维护操作复用封闭RPC、固定参数、operation/data_owner/全部writer租约和受管服务身份。新动作必须准确授权；拟沿用upgrade范围仅用于官方代码升级/回退，不因此获得任意命令、DNS写入或用户数据删除能力，旧无upgrade授权不得写。
- 准备阶段先验证完整候选、目标/兼容性及真实当前配置，再停止任何服务；准备失败不改变CURRENT、选择、服务或数据。离线配置校验以服务UID执行。
- 切换顺序必须明确：先取得operation操作租约并建立新写请求准入屏障，捕获并fsync原服务状态及两core已提交停止意图的prepared journal，再自动停止服务并确认旧进程/监听退出，之后才能取得全部writer/data_owner独占租约。独占后复核选择/配置与最后已提交core意图，把停机前已准入并完成的并发请求纳入journal，不能复活最后明确停止的core。接着完成私有备份，成功后才切换选择；备份失败不切换并按已保存意图恢复。释放writer后恢复原意图并核实际binary身份、配置revision、所属监听和既有HTTPS健康。不能在核心仍持owner lease时宣称已取得全部writer租约，也不能在首次自动停服务后才记录原服务是否在运行。路径被缓存，不能只改环境变量或符号链接声称热切换。
- 核心升级不修改节点/用户/订阅/证书数据和停止意图，不重签证书，不改防火墙/SSH。手动停用的core保持停用；原本停止的面板不会因升级被悄悄启动，成功文字要区分已切换但停止与运行健康已核。
- 提交前失败保留原选择；提交后启动/健康失败只在原代制品、配置、意图及journal均可确定时恢复原选择，并报告回退事实。恢复不能确认则停机并保留待恢复事务；不自动反复重启，不回滚数据库，不把部分恢复称成功。中断恢复须先识别最后已确认身份，未解决事务挡新切换。
- rollback是对已验previous核心的代码选择回退，仍须复核当前配置兼容，保留业务数据。整应用回退到不识别此合同的旧版本须在切换前明确拒绝，不默默退回包内核心；禁止手工覆盖来制造兼容。

## 备份与恢复闭包

备份必须明确包含独立选择/事务身份与所需核心制品闭包，不能让data外状态或隐藏文件遗漏。备份带上选择引用的current/previous独立组件原ZIP及完整清单，并在替换任何data前准备和核验恢复后所需制品；具体布局与信任规则如下。备份中的hash或可执行文件不能自授信，必须匹配独立可信组件清单；离线缺可信制品/清单时在变更前拒绝，给出“先准备所需核心”的中文操作。

使用旧23不会误认的新备份kind，并保留DNS备份、恢复后凭据冻结、撤销旧授权、双代data租约和RESTORE_PENDING语义。恢复与激活均检查核心合同兼容性，不能只靠data快照推定已恢复二进制。code-only回退不替代完整备份恢复。

## 本阶段需要的文件与验收

正式实现的预计触点：core artifact/selection的独立验证模块；deploy的封闭维护RPC/worker及installer受信依赖闭包；CoreRuntime/CoreManager、serve/launcher与诊断的共同选择入口；release_tools备份/兼容保护；现有软件更新API/终端/中文界面；独立组件发布与资格workflow及直接相关文档。它们不在本次DNS PR中修改。

Actions非浏览器验收须覆盖：四目标身份/摘要与安全解包；错误来源/不兼容/未知版本/篡改/缺件拒绝且不停止原服务；至少每个core一次不同官方版本升级及原版本回退；两core运行/停止四种组合和原面板停止；真实配置校验、错误启动/监听/TLS失败恢复、SIGKILL/断电阶段重入、重复任务/CAS/并发安装恢复互斥；实际binary身份/旧进程退出；节点全字段/凭据/证书/SNI/订阅字节与停止意图保留；双开关四态/无DIRECT/UDP拒绝；主核心与诊断同身份；旧备份与新闭包恢复、缺可信制品时变更前拒绝；旧应用reader/activate拒绝与旧组件资格不冒继承。

需要按实际触点选择最小充分套件，既定强制检查保持。浏览器、真实VPS、手机、外部账号和新生产部署均不从Actions推导。当前P2的外部下载失败不能靠本草案、合并不同attempt或跳过安装验收解决。

## 定稿选择：组件来源与格式

- 组件tag采用core-{sing-box|xray}-v<三段数字版本>-r<正整数>，不是应用v<版本>。固定ForceMind/V-UI仓库，稳定公开Release，严格匹配其tag/core/manifest/source40。列举有界、只列组件tag；应用更新保持应用tag。组件公开workflow强制--latest=false，并在公开后独立核对应用latest身份未变。本机gh release create --help已核该参数存在，尚未创建任何Release。
- 资产集合固定为CORE-RELEASE.json、SHA256SUMS、四个vui-core-<core>-<version>-<target>.zip。每ZIP只含该目标组件二进制、许可证/既有源码分发材料及闭合manifest，不含安装脚本、root helper、用户数据或凭据。未知/重复/遗漏成员拒绝。builder_source40和精确目标/归档/二进制SHA均在组件manifest绑定。
- 清单合同schema1、kind=core-release-v1，兼容项是有限的精确应用source40列表，每项均须publisher核对应实际Actions资格；不以semver区间或approved=true放行。独立组件升级不要求每次提升应用版本，但未被精确资格覆盖的应用source不得接纳。
- 信任入口为固定官方Release API返回的仓库/Release ID/tag/稳定状态、资产ID与digest；先用API外部digest核CORE-RELEASE.json及完整SHA256SUMS，再交叉核精确全资产集合和每个ZIP。不是拿ZIP内checksum自证可信。复核时tag/ID/asset digest变化须拒绝当前任务；无可信API响应或独立本机可信缓存即拒，不能用备份自带元数据冒充authority。
- 组件公布沿用现有人工gated发布模型：精确应用source与构建source、四目标、原始安装/核心切换证据必须先完成。组件与应用发布均须对应明确授权；不新增签名密钥/Secret/token权限，不覆盖tag/Release/资产。

## 定稿选择：布局、准入与备份

- 托管路径为/etc/v-ui/core-selection.json和/etc/v-ui/CORE_UPDATE_PENDING.json；父目录root所有、不可由组/其他人写，文件root所有0644，仅含非秘密身份/意图。事务的私有详情另以0600存于root控制目录。更新采用O_NOFOLLOW、单link、属主/模式/类型及有界严格JSON检查，逐层核父目录身份，不信任data或环境给出的路径。
- 制品根为/var/lib/v-ui-core/，root所有0755且任何祖先不可由服务UID写。artifacts/<完整manifest SHA256>/不可变，原pack ZIP/metadata为0644，binary为0555；无符号链接/任意路径/覆盖同ID。暂存发布及回退均为root受限worker负责，core和校验进程仍为服务UID。全应用payload/READY/Python校验不变，包内binary不覆盖。
- 准入屏障必须由可信root位置的pending journal与data_owner写入准入共同落实，所有受影响API/CLI/证书/代理writer执行前检查，正在执行的事务在停机后独占租约下复核其最后提交状态。不得借用DNS/proxy schema或伪造RESTORE_PENDING阻止旧程序。status/recover可查待恢复状态，新切换与普通写拒绝。
- CoreRuntime schema2精确绑定revision、enabled及已验core_artifact身份；原schema仅用于bundle选择的显式迁移。主运行时、proxy协调、诊断统一resolver，不允许不同路径各自选择。恢复旧配置必须同时恢复其匹配binary身份，不把配置恢复冒称binary回退。
- 私有backup-components-v1采用最后确认的稳定快照：包含原data、已确认current/previous选择与停止意图，以及这两类引用的原组件ZIP和已验清单闭包。自动升级前备份从prepared journal中的原已确认选择和停机后最终意图生成；候选/pending仅可保留为有界脱敏审计，不属于可恢复执行状态，不携带旧nonce/权限或要求candidate制品。恢复创建自己的新事务，绝不恢复旧CORE_UPDATE_PENDING到live或自动续做原切换；旧pending审计不得触发下载/执行/写DNS/旧授权。普通手动备份遇未恢复的核心事务应拒，不能抓取半提交状态。bundle基线仍由原应用release外部恢复合同管理，不复制整个应用runtime入core备份。只显式纳入这些文件，不放宽普通隐藏文件或整个/etc目录。
- 备份流式复制和有界校验，每组件原ZIP上限100MB、最多两core各current/previous四份，拒绝超限/重名/额外制品；保留既有用户data独立大小保护，不能先整包read进512MiB内存。保留所有既有备份与旧组件，不在本阶段自动清理历史。
- 恢复时，备份中的ZIP/manifest/选择仍是输入，不能自授执行权。须匹配本机独立可信catalog或重新从固定官方API核authority，且与目标应用精确兼容；缺可信清单/制品或离线无法确认时，在停止/替换data前拒绝并提示先准备。暂存好全部闭包后才按租约/journal交换data与选择；失败保留原两者，不能只恢复一半。DNS冻结和旧授权撤销保持。

## 原23工具的明确边界

- P3的短入口、安装器、GUI、终端、远程worker、stage/activate/backup/restore和启动路径全部读取并校验新选择与目标能力；不兼容旧应用在变更前拒绝。旧23受管全局入口面对新增精确root依赖闭包会在receipt/preflight拒绝；原23restore面对backup-components-v1会在替换data前拒绝。这两点必须用原工具做负向CI证明。
- 原23不带--with-global-cli的在线安装、本地--bundle、离线deploy.py activate/rollback/backup，以及旧kind restore，不知道data外选择；只靠新receipt或backup kind不能让这些路径自动拒绝。源码事实已单列，不能把它们记为安全通过。启用独立组件后，这些手工运行旧源码/旧安装器的路径不属于受支持的管理/降级方法，界面/终端/文档明确要求通过P3当前可信入口；受支持的整应用回退先执行新兼容检查。
- 不篡改旧release/READY，不伪造恢复错误，不改DNS/proxy数据来假造旧guard，也不宣称能阻止root手工绕过当前入口执行任意旧程序。测试矩阵分别记录：P3全部入口应拒不兼容旧release；原23受管preflight/新kind restore应拒；原23上述原始入口缺guard为明确未授资格的限制，而不是修改断言把它称为拒绝。

## 首次真实不同版本候选与平台边界

2026-11-08只读官方Release元数据已确认下列稳定候选及所需资产存在，未下载二进制、未改现pin，也未授运行兼容资格：

| core | bundle回退基线 | 首测候选 | 官方Release |
| --- | --- | --- | --- |
| sing-box | 1.14.2 | 1.14.3 | https://github.com/SagerNet/sing-box/releases/tag/v1.14.3 （407496210） |
| Xray | 26.3.27 | 26.10.21 | https://github.com/XTLS/Xray-core/releases/tag/v26.10.21 （419510937） |

选择读取到的较近更高稳定候选，不追逐latest，不声称这是全部历史中的最近版本。sing-box四个glibc/musl、amd64/arm64资产和Xray两个官方架构资产ID、size、digest已读；Xray是否可覆盖两个libc目标仍须逐目标真实验证，不能因同archive直接宣称兼容。旧客户端保持现有固定Mihomo1.19.32与sing-box1.14.2，真实检查/升级/回退/负向与平台资格分开记录，缺任一必须保留未验。

OpenRC明确仅状态/准备；不提供未经资格的自动切换/回退，不把systemd结果泛称所有平台服务管理支持。GNU systemd实际切换、musl原生制品/核心检查及固定客户端真实协议矩阵须准确区分，门禁不得用skip授资格。

## 当前状态与下一步

以上选择须再做设计独审，尤其catalog独立信任、准入竞态、备份与选择双状态恢复及旧工具边界。现只有代码事实/元数据与契约草案，尚无P3实现、实际核心升级、四目标新核心资格或公共组件Release。P2的官方资产下载失败仍独立记录，不能用该草案补齐P2门禁。

受保护分支合并、组件或应用tag/Release公开发行、短入口变更和真实主机部署均须在具体成果可审阅后取得对应明确授权；当前没有这些P3授权，也未执行。新签名密钥、Secrets或额外权限不能从普通开发授权推断。

# P3 核心独立升级与回滚契约草案

状态：设计独审通过；当前完成制品校验、不可变准备/读取、官方组件客户端、纯选择计划与离线受信缓存源码及合成回归，完整核心升级/回退尚未实现或验收。代码事实核对基线为4bd1d9a648a8096fbf2fa05314e35049a2408e7c。它不属于当前DNS候选PR145，不改变当前公开22、23发布门禁、核心pin或服务器。

本阶段来自ROADMAP的已批准P3；Go迁移继续暂缓。契约已完成独审，制品校验作为独立实现首块；后续选择、备份闭包、切换和RPC继续分块实现与审查。首测版本已列明，仅核元数据；制品和兼容资格尚未取得，不能把本草案称为核心已可升级。

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

以上选择已完成设计独审。M0制品校验23项、M1不可变准备/读取25项、M2官方客户端29项、M3纯选择计划17项、M4受信catalog缓存24项合成回归均通过实际Python3.9.6/3.12检查及增量独审；初轮macOS临时目录别名和归档成员卷号漏检均已修正并定点复核。已实现的存储仅准备/回读制品及曾认证的受信上下文，客户端只由虚构传输测试，选择模型只产出计划；root选择持久化、运行时接入、受管切换、备份闭包和RPC仍未实现，真实网络/核心与四目标资格及公共组件Release未取得，不能把这些原语称为完整核心升级。P2的准确提交资格与外部下载失败仍独立记录，不能用P3结果补齐。

受保护分支合并、组件或应用tag/Release公开发行、短入口变更和真实主机部署均须在具体成果可审阅后取得对应明确授权；当前没有这些P3授权，也未执行。新签名密钥、Secrets或额外权限不能从普通开发授权推断。

## schema1 字段定稿：首个只读验证块

本节仅定义 `app/core_updates/artifacts.py` 的输入与验证结果，不表示选择、安装、运行兼容或完整P3已经完成。`decode_authority(raw)`只严格解析调用者从固定官方API或独立可信缓存取得的规范上下文；它不能证明JSON的获取来源。ZIP、备份或上传内容自带的同形JSON不能用作authority。`expected_builder_source`必须由可信组件tag ref解析到完整commit，不使用Release的自由文本`target_commitish`。

调用关系为：

```python
authority = decode_authority(trusted_api_context_bytes)
release = verify_release(manifest_bytes, checksums_bytes,
    authority=authority, expected_tag=trusted_component_tag,
    expected_builder_source=trusted_tag_commit, app_source=exact_running_app_source)
identity = verify_pack(local_archive_path_or_fd, release, target=detected_target)
```

所有JSON对象都拒绝未知/重复key、坏UTF-8/孤立surrogate、浮点数、NaN/Infinity、超界整数与过深结构。下面列出的key集合为精确集合，没有`approved`、URL、版本范围或可选解释器/命令入口。

### 官方API规范上下文

顶层精确字段为`schema=1`、`repository="ForceMind/V-UI"`、`repository_id=1110299182`、正整数`release_id`、`tag`、`draft=false`、`prerelease=false`、非空有效UTC `published_at`（`YYYY-MM-DDTHH:MM:SSZ`）和`assets`。每条asset恰为`{id,name,size,digest}`，ID为正整数，size为正整数字节数，digest为`sha256:<64小写hex>`；资产名称和ID均不重复，集合必须是CORE-RELEASE.json、SHA256SUMS及四目标ZIP。API原始字段由调用者有界读取后明确规范化，不能默认JSON解码后丢失重复字段。调用者在任务复核时必须比较原Release ID、tag、完整asset ID/digest集合和tag-ref source，不得把变化后的authority悄悄替换为原任务资格。

### CORE-RELEASE.json

结构样例如下，数字/摘要/source仅为结构占位，**不是已公开或可信的制品**；实际四个pack必须填满全部字段并匹配外部API。

```json
{
  "schema": 1,
  "kind": "core-release-v1",
  "repository": "ForceMind/V-UI",
  "tag": "core-sing-box-v1.14.3-r1",
  "core": "sing-box",
  "version": "1.14.3",
  "revision": 1,
  "builder_source": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "compatibility": {
    "contract": "core-selection-v1",
    "app_sources": ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
  },
  "packs": [
    {"target":"x86_64-gnu","name":"vui-core-sing-box-1.14.3-x86_64-gnu.zip","size":1,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
    {"target":"x86_64-musl","name":"vui-core-sing-box-1.14.3-x86_64-musl.zip","size":1,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
    {"target":"aarch64-gnu","name":"vui-core-sing-box-1.14.3-aarch64-gnu.zip","size":1,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
    {"target":"aarch64-musl","name":"vui-core-sing-box-1.14.3-aarch64-musl.zip","size":1,"sha256":"0000000000000000000000000000000000000000000000000000000000000000","manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}
  ]
}
```

`app_sources`为1–64个去重、排序后的完整小写SHA40；调用者当前精确应用source必须在其中。core只允许sing-box/Xray的固定官方repository及archive/member模板；version为与可信组件tag完全一致的规范三段数字，revision对应tag的正整数r值。decoder不硬编码首测两个版本，未来同contract候选仍须新的publisher资格与官方组件Release；格式可表达不等于运行兼容。

SHA256SUMS恰有5条`<64小写hex><两个空格><文件名>`：CORE-RELEASE.json及四个ZIP，禁止列自身。先用API的外部digest和size核两份小文件，再让table、Release pack descriptor及API的ZIP摘要/size完全一致。CORE-RELEASE只记录ZIP及其内部CORE-PACK.json的摘要，不记录SHA256SUMS摘要，避免自引用。

### 每目标pack与provenance

每ZIP精确5个regular成员，禁止目录、符号链接、重复/未知/路径截断、加密及未声明成员：

| 成员 | ZIP内Unix mode | 内容 |
| --- | --- | --- |
| `bin/<core>` | 0555 | 对应目标的实际二进制字节 |
| `LICENSE` | 0644 | 官方source内LICENSE原文 |
| `SOURCE.tar.gz` | 0644 | 该上游source commit的原源码分发归档 |
| `PROVENANCE.json` | 0644 | 下述闭合来源记录 |
| `CORE-PACK.json` | 0644 | 内部manifest |

CORE-PACK.json精确复用CORE-RELEASE的`schema,repository,tag,core,version,revision,builder_source,compatibility`，另有`kind="core-pack-v1"`、`target`、`files`。files精确列前四个材料成员，每项恰为`{size,mode,sha256}`；mode使用JSON十进制365或420。它不列自身摘要，也不列外层ZIP或table摘要；自身字节摘要由CORE-RELEASE的`manifest_sha256`约束。

PROVENANCE.json精确为`schema=1,upstream,binary_sha256,license_sha256,source_sha256`。upstream精确为`repository,tag,archive_name,archive_size,archive_sha256,member,source_commit,source_size,source_sha256,license_member`。binary/license/source摘要及source大小必须对应CORE-PACK中实际材料；source_commit为完整SHA40，license_member固定`<repo短名>-<source_commit>/LICENSE`，不得伪造其他许可来源。

- sing-box固定SagerNet/sing-box、tag `v<version>`；archive为`sing-box-<version>-linux-<amd64|arm64>-<glibc|musl>.tar.gz`，member为相同目录下`sing-box`。
- Xray固定XTLS/Xray-core、tag `v<version>`；archive为`Xray-linux-64.zip`或`Xray-linux-arm64-v8a.zip`，member为`xray`。同架构两个libc目标引用同官方归档这一格式关系，不授予跨libc运行资格。

本机consumer不包含原binary上游归档，不重建源码，也不重新证明上游提取。它验证既有官方V-UI组件publisher的外部authority及pack字节/来源声明一致；原官方binary归档的实际下载、SHA、提取成员到binary，以及源码归档与LICENSE原文关系，必须由未来Actions producer实际验证并留下资格证据。若上游许可证文件名/归档模板改变，producer资格应失败，再明确修订合同；不能把元数据声明当作许可证字节证明。

### 读取与稳定性边界

每pack≤100,000,000字节；JSON/table各≤64,000字节，固定5个ZIP成员，中央目录≤16,000字节，全部展开≤256,000,000字节，SOURCE≤128,000,000字节，LICENSE≤1,000,000字节。先检查EOCD/中央目录边界再构造ZipFile；禁止多卷/ZIP64中央目录/额外comment或extra，支持正常local force_zip64和有界streaming data descriptor。local records必须完整覆盖中央目录之前的区域，不接纳前缀或隐藏记录。只允许stored/deflated，不提取到磁盘。

外层摘要和成员摘要均以≤1MiB块流式读取。path入口逐层no-follow打开目录，要求原ZIP是regular单link，验证前后核文件FD/路径与父目录inode身份，并再次核完整外层摘要。FD入口dup自身句柄、使用pread保持调用者offset/FD，不声称能证明原path或父目录权限。所有自有FD关闭均需尝试，清理失败不返回成功身份。root属主、不可变store发布和选择事务属于后续模块，本只读parser不把输入ZIP所在位置冒称受信store。

结果仅为制品身份/摘要，不含“安装通过”“job成功”或可执行命令；实际binary/ELF/目标兼容必须由后续非root preflight和准确Actions资格验证。

## M1不可变制品准备与回读接口

M1仅增加`app/core_updates/store.py`的prepare/read，不增加active选择、安装、进程启动、服务切换、网络、RPC、GC或备份恢复。验证结果仍不表示native/configuration/service就绪。

```python
store = CoreArtifactStore()  # 固定生产Layout，prepare要求实际euid==0
stored = store.prepare(pack_path_or_fd, manifest_bytes, checksums_bytes,
    authority=independently_trusted_authority, expected_tag=trusted_component_tag,
    expected_builder_source=trusted_tag_ref_source, app_source=exact_app_source,
    target=trusted_detected_host_target)
checked = store.read(stored.identity.release_manifest_sha256,
    authority=independently_trusted_authority, expected_tag=trusted_component_tag,
    expected_builder_source=trusted_tag_ref_source, app_source=exact_app_source,
    target=trusted_detected_host_target)
```

`target`由受信本机adapter提供，后续复用既有platform_support；store不猜libc，不读取环境变量，也不把用户输入或pack自述当本机目标。返回`StoredArtifact(identity,binary_path,reused)`，其中identity为M0的PackIdentity；没有“已激活”“已安装”或“服务健康”字段。

### 固定闭包与信任缓存

生产根固定`/var/lib/v-ui-core`。root、`artifacts/`、成品目录及其中`bin/`均为root所有0755；`.staging/`为root所有0700；全局`prepare.lock`为root所有0600、regular单link空文件。生产从`/`逐层以dirfd/no-follow打开并检查祖先root属主且不可由组/其他人写；read允许非root执行，但不能改变这些属主/权限条件。

`artifacts/<CORE-RELEASE.json完整SHA256>/`精确含9个regular单link文件和一个`bin/`目录：原目标pack的规范ZIP文件名、原字节CORE-RELEASE.json、原字节SHA256SUMS、TRUST.json，加上M0固定的5个解出成员。binary为0555，其余文件0644。每机器仅保存该manifest下本机target；相同manifest ID已有其他target的目录时明确拒绝，不加文件、不覆盖。

TRUST.json精确结构为：

```json
{
  "schema": 1,
  "kind": "core-trust-cache-v1",
  "authority": {
    "schema": 1,
    "repository": "ForceMind/V-UI",
    "repository_id": 1110299182,
    "release_id": 31,
    "tag": "core-sing-box-v1.14.3-r1",
    "draft": false,
    "prerelease": false,
    "published_at": "2026-11-08T00:00:00Z",
    "assets": []
  },
  "tag_source": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

该例只展示字段，空assets故意不满足真实M0验证，不能作为可信制品输入。实际authority须为前节完整六资产；写入按asset name排序。读取忽略JSON key/asset列表的纯序列化顺序，但ID、tag、size、digest、published_at、tag_source等任何身份变化均不被同一上下文接受。tag_source来自调用者独立解析的可信tag ref；TRUST记录它而不自授信。read每次仍要求调用者提供独立authority/tag/source/app-source，不从本成品或备份自动生成expected值，也不做offline fallback。无额外STORE自hash表；ZIP/metadata来自外部authority，解出成员来自其绑定的CORE-PACK，TRUST与外部上下文逐项匹配，因此没有摘要自引用。

### 准备、发布和故障语义

prepare在触及store前通过M0验证完整候选及外部metadata。随后取得非阻塞独占flock；并发遇锁返回固定STORE_BUSY，不自动重试。输入FD继续保持，源路径/FD稳定性最终再次核验。只向随机、独占新建的私有staging namespace写入，原ZIP及每个固定成员都用O_EXCL/no-follow、≤1MiB流式复制/摘要核验，并校owner/mode/size、逐文件fsync。staged ZIP再经M0验证，解出与metadata全部完成read-back后fsync目录，才尝试发布。

发布使用带dirfd的原生原子no-replace：Linux libc `renameat2(RENAME_NOREPLACE)`；Darwin假包测试用`renameatx_np(RENAME_EXCL)`。符号或文件系统不支持时固定UNSUPPORTED，绝不改用可覆盖的rename。发布后fsync目标/来源父目录，并重新验证公共成品、文件/FD/父目录身份及完整闭包，成功后才返回。相同ID重入必须完整校验已有成品、外部上下文及本机target，再确认目录持久化后返回reused；缺件、篡改、额外文件或错target均拒绝，不修复、不覆盖、不删除已有成品。

公共名称一旦发布，后续fsync、回读或确认失败均报失败并保留成品，不能因“不确定”自动删除公共路径。若rename实际上成功却确认丢失，私有原名称已不存在，不得通过仍指向公共目录的FD执行清理。只清理本次独占创建且仍在私有namespace中核对到同一inode的临时对象；碰撞/被替换对象不采用、不删除。首项关闭/清理失败不短路其余自有对象处理；最后仍报告固定失败，并保留原异常关系。遗留私有内容不在此阶段自动GC。

受信`Layout(root,owner,anchor)`仅为Python测试/内部注入seam：测试明确提供自有0700临时anchor及真实测试UID，跳过的是anchor之外的测试环境，不是生产权限规则。它不成为用户参数、CLI或环境开关；生产入口只构造默认root Layout。root standalone加载仍通过受信pathless package namespace显式加载M0/store，不从cwd、user data或payload查找模块，也没有ImportError fallback。

当前Mac假包测试只证明上述实现逻辑与Darwin原子语义；Linux/glibc/musl原生符号与文件系统支持、四目标真实核心资格、store与后续选择/备份/运行时集成仍须各自Actions验收。

## M2固定官方组件客户端接口

`app/core_updates/github.py`仅提供匿名官方组件API读取及显式私有FD下载，不写store、可信缓存、active选择、用户data、全局网络配置或Release，不执行二进制。生产只构造默认`GitHubCoreClient()`；`opener=`是受信Python假transport注入，不是URL、HTTP、token或环境开关。

```python
client = GitHubCoreClient()
discovery = client.discover('sing-box', deadline=monotonic_deadline,
                            stop=cancel_event.is_set)
candidate = client.inspect(known_release_id, app_source=exact_app_source,
                           deadline=monotonic_deadline, stop=cancel_event.is_set)
client.recheck(candidate, deadline=monotonic_deadline, stop=cancel_event.is_set)
# 仅此显式方法写入caller独占新建的空私有FD，不接收输出路径。
identity = client.download_pack(candidate, detected_target, new_private_rw_fd,
                               deadline=monotonic_deadline, stop=cancel_event.is_set)
```

### 发现、检查与复核结果

- `discover(core)`返回`Discovery(hints,truncated,pages,examined)`。每条ReleaseHint精确含`release_id,tag,core,version,revision,published_at`，仅为固定官方仓库stable组件tag提示，未经过manifest的app-source资格检查。API每页20条、最多5页/100行、最多32条hint；触限必须`truncated=true`，即使hints为空也不能表述“没有更新”或“已知全部历史”。每个已取页面的必要字段、Link与重复ID/tag仍严格校验；HTTP/分页/格式错误直接固定错误，不伪装成partial成功。API分页自然结束才返回`truncated=false`；无论哪种结果都不是当前版本已最新/可运行的证明。
- UI后续仅展示近期发现且逐条inspect成功的候选；已记录current/previous按已知release ID直接inspect，不要求用户手填历史版本，也不为历史索引新建缓存框架。
- `inspect(release_id, app_source=...)`返回Candidate，字段为`authority,verified_release,tag_source,manifest,checksums,repository_metadata,release_metadata,ref_metadata`。后三类原始metadata和小文件原字节均从repr中隐藏，不作终端输出；错误不包含body、令牌、signed URL或任意上游消息。调用者不得把原metadata作为页面HTML渲染或当成命令。
- `recheck(candidate)`重新从固定API取得完整上下文，只在repository/Release/tag/所有asset ID、size、digest、stable状态及独立tag source仍一致、M0重新验证小文件仍同一VerifiedRelease时返回。JSON key及asset列表的纯序列化顺序不算身份变化。它没有跨任务持久状态，也不自动替换原任务上下文。

inspect与recheck先GET固定`/repos/ForceMind/V-UI`，实际核repository ID=1110299182、full_name及public状态，不从常量假造API身份。再GET固定`/releases/<id>`，核Release ID、组件tag/stable/published状态及精确六资产；每资产必须uploaded、API URL与同repo/ID一致，browser URL与固定repo/tag/文件名一致，但下载实际只走asset API/id。`target_commitish`即使存在也完全不作builder source。

独立builder source来自`/git/ref/tags/<组件tag>`，要求ref精确一致。轻量tag直接要求commit40；annotated tag最多剥4层，逐层核固定同repo git tag/object URL、SHA与类型，拒绝cycle/tree/blob/坏SHA，最终只能到commit。两份小asset均先核外部API size/SHA，再复用M0的完整table、精确appsource及四目标合同检查。inspect返回前再独立recheck，不能将下载前的旧API上下文冒充操作结束时仍有效。

### 网络与失败边界

所有请求由固定repository、ID、规范组件tag/commit构造，不接受任意baseURL或raw URL，不查询/改变应用latest，也不追随上游latest。默认urllib使用验证TLS、`ProxyHandler({})`禁代理继承，未读取/发送GITHUB_TOKEN、面板令牌或其他凭据。只设置固定User-Agent、Accept与GitHub API版本头，不安装全局opener。

metadata的任何redirect均拒绝；asset仅可从固定同repo/ID API跳到HTTPS官方release-assets/objects主机。每个逻辑GET两次尝试合计最多3个redirect，重试不重置跳数，所有redirect/error/partial response均关闭且不drain未知body。固定API之外的API资产ID、userinfo、端口、控制字符、非HTTPS或其他主机全部拒绝；signed query仅为传输位置，不是授权依据，也不进入错误文本。

仅502/503/504或明确`TimeoutError/socket.timeout/URLError.reason`为timeout时允许同原URL一次重试。403/404/429、SSL、泛URL/OS错误、破损HTTP、schema或digest失败不盲重试。metadata JSON≤2,000,000字节，严格UTF-8、重复key、非有限数、整数/嵌套界限；未知API字段只按协议取必要allowlist，正常有限浮点未知字段不进入闭合M0输出。必要M0字段仍保持其严格类型和64KB小文件上限。Link只允许固定repo/releases及唯一page/per_page参数，不能跟随任意分页URL。

优先使用read1；没有read1时使用≤64KiB的小块read，任何块均≤1MiB。单次socket timeout≤10秒，可由调用者绝对monotonic deadline进一步缩短；请求及每块前后检查deadline/stop。**这些检查不构成slowloris硬墙钟预算**：一个阻塞open/read期间的慢速报文可能持续超过模块检查点。上层独立worker仍必须实现有限总生命周期、取消后的kill/reap并通过故障验收；本模块不暗建线程、信号或通用任务框架来假称已完成硬预算。

### 显式pack FD下载

caller必须先以O_EXCL独占新建空0600、regular单link、owner=euid的私有O_RDWR非append FD，并保证整个调用期间独占使用。模块检查可观察的空/类型/mode/owner/link/offset/flags；仅凭FD不能证明先前的O_EXCL创建历史或父目录权限，这些仍由caller保证。下载不接收任意输出path。

模块dup自身FD，使用pwrite/pread保持caller FD及offset；开始写前先fresh recheck。原pack≤100MB，按外部asset size/SHA流式读取，重试前只把本次新私有sink截为0，丢弃partial后从原asset API重新GET，绝不append旧bytes。local sink写错不是网络timeout，不触发GET重试。完成后fsync、M0 verify_pack，再fresh recheck和最终FD字节身份稳定性检查，才返回M0 PackIdentity。

任何最终网络/校验/取消/本地写或关闭失败都不返回成功。sink可能保留partial或完整但未确认的bytes；caller负责按自己的临时文件所有权安全close/unlink，不得消费这些失败输出，模块不会自动删pathname或关闭caller原FD。它不发布store、不切换core，也不以文件下载成功代替ELF/native/configuration/service资格。

本块仅假transport、metadata与stream/FD故障测试；真实官方API互操作、实际组件Release、独立worker硬预算，以及后续store/selection/RPC/native集成仍分别待验。

## 已确认核心选择模型（M3）

此块只定义纯数据编码与切换计划，不读写选择文件、不切换核心或服务、不授予权限，也不验证本机可执行或网络资格。结构合法、修订摘要正确都不是材料真实性证明。调用者必须先验证root所属的选择文件、实际应用与bundle材料，以及组件catalog/store与当前配置兼容，再通过受管备份/停机/恢复事务持久化计划。

- schema1完整字段为schema、app_source（实际应用40位source）、target、generation、revision、cores；cores恰为sing-box和xray，每项恰含current与previous，previous可为空。不存裸路径、URL、authority、approved、pending/candidate或用户启动偏好。
- bundle引用绑定core/version/target、原应用source、payload manifest及二进制摘要/大小；component引用绑定固定仓库、release ID/tag、builder source、发布manifest/清单/pack/内manifest及二进制摘要/大小。component的稳定材料身份不含调用时app_source；提出切换时仍须M0材料结果的app_source与当前应用相同，不能凭稳定身份绕过兼容。
- revision是排序规范JSON的公开SHA256，排除revision自身但包括generation。generation由0开始，每次实际选择变化递增，防止更新/回退后相同内容重新接受旧CAS；上限到达拒绝继续变更。它不是MAC或签名，不替代root所有权及独立材料验证。
- 后续root文件/恢复层必须保留代次连续性：initial_selection只用于经确认的首次初始化，不能拿它掩盖损坏或丢失的选择文件；恢复备份不能原样复用备份generation/revision，应从当前live及输入快照推进新代次并撤销旧授权。发现运行时schema2或未解决journal而选择丢失时须进入恢复，不能静默重建为bundle。这些文件与恢复约束尚待实际集成验收。
- 所有计划先核expected_revision，即使请求材料已经是current也不能跳过旧请求冲突检查。同一current无操作、不改generation或previous；真正更新仅改变指定core，并保留原current为previous，另一个core不变。
- 回退必须由调用者重新提供已核上一材料的身份，按全部字段精确匹配previous，不以名字/版本字符串替代；成功计划交换current/previous。历史旧app的bundle引用可作为previous保留，但不能静默重绑或在新app下执行。完整软件升级与跨app兼容处理仍需后续实现。
- 计划包装与选择结构分离；encode拒绝直接编码SelectionPlan，但其after值可为journal编码。结构或编码本身不证明已提交，备份必须读取实际最后确认的live值或prepared journal的原确认快照，不能把plan.after或pending当成已确认备份。CoreRuntime的revision/enabled与停止意图仍归原运行时和后续独占停机快照管理，本模型不写这些偏好。

代码入口：app/core_updates/selection.py；初始纯数据规则在实际Python3.9.6与3.12各17项回归通过，覆盖CAS/ABA、无操作、双core隔离、上一材料精确性、应用/目标/引用字段与无自授信。新app/root文件与运行时集成、真实切换和恢复尚未由此得到资格。

## M4独立可信catalog：离线读与fresh来源持久化

`app/core_updates/catalog.py`只持久化曾经独立认证的组件上下文，供离线启动/回退给M1.read提供authority与tag_source。它不写artifact store、selection、服务、用户data、备份或运行时，不含GC、用户action授权、token、nonce、approved/fresh布尔。缓存也不是官方状态实时未撤销的证明。

```python
catalog = CoreCatalog()  # 生产固定 /etc/v-ui/core-catalog
# record写门禁在网络client构造和一切FS创建之前；生产须实际euid0。
recorded = catalog.record(candidate, app_source=exact_app_source,
                          deadline=monotonic_deadline, stop=cancel_event.is_set)
# 可复用受信caller已有的默认M2 client；测试仅注入真实M2+fake opener。
recorded = catalog.record(candidate, app_source=exact_app_source, client=trusted_m2_client)
# read没有client参数、不会初始化client，也不会触发网络或缺失记录初始化。
entry = catalog.read(manifest_sha256, app_source=exact_app_source)
# 返回字段可作为M1.read的独立预期，不能从待验artifact/TRUST取得这些值。
artifact = artifact_store.read(entry.manifest_sha256,
    authority=entry.authority, expected_tag=entry.authority.tag,
    expected_builder_source=entry.tag_source, app_source=exact_app_source,
    target=trusted_host_target)
```

返回`CatalogEntry(authority,tag_source,verified_release,manifest,checksums,reused)`，`manifest_sha256`从VerifiedRelease得到。小材料原字节不在repr中显示。caller必须从已验应用release取得当前精确app_source，而非从备份或用户输入猜测。

### 来源门禁不是对象类型声明

Candidate是公开dataclass，类型、字段或M0格式正确不能证明其来自刚完成的官方API。record默认在UID门禁之后才创建安全的M2 GitHubCoreClient；传入client也必须是真实M2类，生产受信caller不提供用户transport/URL/token。每次record包括same-ID重入均强制M2 fresh recheck，并重新M0验证**本次给定**app_source，不能只相信candidate.verified_release。普通JSON、backup字典、任意回调、`fresh=True`不是合法入口。真实M2复核失败时在任何catalog创建之前拒绝，原live记录也不改变。

如果备份携带的字节在调用者明确执行官方fresh复核后恰好匹配，它们已被重新认证，而非以备份自授信；没有fresh API或独立既有live记录时不得seed。record没有离线跳过门禁或自动导入功能。deadline/stop转交M2做其已声明的IO/检查点控制；它仍不替代上层独立worker对网络和FS阻塞的硬生命周期预算。

root本来具有任意文件写入与Python对象修改能力，本模块不声称防御恶意root。live信任入口由固定root路径、受信代码、fresh record流程和文件权限共同保护，不能用一个包内checksum、自签对象或来源布尔替代它；动作是否获用户授权仍由独立上层授权检查决定。

### 三文件不可变目录

固定布局为`/etc/v-ui/core-catalog/<CORE-RELEASE.json完整SHA256>/`，精确含：

| 文件 | mode/owner | 校验与含义 |
| --- | --- | --- |
| CONTEXT.json | 0644/root | 严格schema1/kind=core-catalog-v1、normalized六资产authority和独立tag_source40 |
| CORE-RELEASE.json | 0644/root | 原API asset digest绑定字节，拒绝重序列化改变外部摘要 |
| SHA256SUMS | 0644/root | 原完整table字节及M0精确五行关系 |

CONTEXT.json精确为`{schema:1,kind:"core-catalog-v1",authority:<M0规范上下文>,tag_source:<可信tag-ref commit40>}`，不存原API任意body或artifact里的TRUST。每文件≤64,000字节，总材料≤192,000字节，无base64、额外自hash表或摘要循环。normalized authority写入时按asset name排序；读取可容忍JSON key/asset纯序列化顺序，但same-ID record的实际authority/tag_source/原材料身份漂移必须拒绝覆盖。

父目录、catalog根及每记录目录为root所有0755；逐层dirfd/no-follow核祖先root所有且不可由组/其他人写，文件regular单link，模式/大小/FD与路径身份前后稳定。read完全offline、不打开0600写锁或0700 staging，允许非root读取这些root-owned0644材料。它从这个独立live目录取得authority/tag_source，再M0重验当前app_source、manifest ID及四目标；不读取data、artifact/TRUST或backup作为信任后备。缺失返回固定CATALOG_MISSING，坏/未知闭包拒绝，不创建或修复。

此处选择小闭合目录而非单个base64 JSON：两份64KB材料base64会使单JSON约175KB并需要新的大JSON decoder；三文件可直接复用M0小JSON边界和M1整目录原子语义，避免新增不必要的编码/发布框架。

### 发布、回读与失败

独立`prepare.lock`为root0600空regular单link，非阻塞独占flock；`.staging/`为root0700。仅在fresh认证与M0验证完成后才建立必要路径，复用已审M1 `_Scope`、`_Stage`、`_rename_new`，不重构M1。随机自建private namespace中写三文件、校大小/摘要/mode/owner并逐项fsync，经完整M0/闭包read-back后以native no-replace原子发布整目录，fsync父目录并公共回读后返回。

同manifest ID已存在时只完整验证并复用；残缺、篡改、额外文件、错误mode/owner/link或身份变化都拒绝，不采用空目录、不覆盖修复、不删旧记录。同ID并发最多一次新发布，另一个只能完整复用或固定busy失败；它不是selection CAS。

native primitive缺失或FS不支持时固定UNSUPPORTED，不回退覆盖式rename。发布后fsync/回读/确认失败保留公共记录并报失败，绝不借已移动的FD删除公共目录；caller下次必须显式完整复核。只清理本次确知自建private对象，碰撞/外来对象不删除；首项清理或close失败仍尝试其余自有对象，所有异常不返回成功。只读/写入错误均固定诊断，不echo任意body/URL/path。

`Layout/owner/anchor`复用M1的受信内部临时测试seam，不成为用户/env参数；生产入口使用固定默认路径。standalone根控制加载仍须显式预载pathless受信namespace中的M0/M1/M2/catalog，不从cwd、data或用户payload查找模块，无ImportError fallback。

backup恢复缺独立live catalog时，上层必须在停止或替换data前明确从固定官方API重新认证并record，或拒绝并提示先准备所需核心。M4不把备份内CONTEXT/manifest/table复制成live authority，也不恢复旧任务权限或generation。本块仅puretemp/M2 fake与实际Python解释器测试；Linux/musl原生发布、真实官方组件、独立worker硬预算，以及选择/启动/回退/备份/RPC集成仍待各自Actions资格。

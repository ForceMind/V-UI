# P3 核心独立升级与回滚契约草案

状态：设计及已实现前置分块独审通过；当前具备制品/来源、选择恢复元数据、confirmed启动/诊断、私有完整备份预检及Root鉴别的managed启动接线，完整受管核心升级/回退尚未实现或验收。代码事实核对基线为4bd1d9a648a8096fbf2fa05314e35049a2408e7c。它不属于当前DNS候选PR145，不改变当前公开22、23发布门禁、核心pin或服务器。

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
- 恢复时，备份中的ZIP/manifest/选择仍是输入，不能自授执行权。须匹配本机独立可信catalog或重新从固定官方API核authority；将成为current或执行的材料必须与目标应用精确兼容，历史previous只作归档保留时另走来源/字节验证，不因此取得当前资格。缺可信清单/制品或离线无法确认时，在停止/替换data前拒绝并提示先准备。暂存好全部闭包后才按租约/journal交换data与选择；失败保留原两者，不能只恢复一半。DNS冻结和旧授权撤销保持。

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

设计及M0–M7当前源码已分块独审：制品库/官方客户端/独立catalog、选择恢复元数据、离线resolver、Manager冻结快照、活动bundle转换及confirmed启动/诊断接线均已实现。初轮M0–M4的118项合成检查和macOS临时目录别名、归档成员卷号漏检失败保留；后续e6f5eb2自身九组/21 jobs及原生三环境各184项契约、staged Python32项假执行器绑定无skip，属于该提交的基础资格。新增boot/diag在本机假材料/AST中验证，仍须其自身Actions。host停止/独占、真实备份闭包、trial/confirm/recovery协调器、迁移及RPC/终端/面板入口尚未实现；真实不同组件版本、四目标切换及公共组件Release尚无资格。P2的104f8c8自身已完整验收但等待单独合并发布批准，不用P3证据替代它或推断发布权限。

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

## M5 root选择与恢复元数据事务（仅元数据）

`app/core_updates/selection_files.py`提供`SelectionFiles`。本块不调用systemd、data_owner、核心、安装器、RPC、网络或实际备份；所有停止、writer独占、备份完成及健康验证参数都只是受信协调器提供的观察记录，不是本机证明，不授予root权限或材料执行资格。M7已把已确认选择接到整应用验证后的只读boot/resolver；实际worker握手、受管切换和本块元数据事务仍未接通，不能称完整独立核心升级已实现。

生产只用默认`Layout(root=/etc/v-ui, owner=0, anchor=/)`，写方法先要求实际UID0。显式`Layout/owner/anchor`仅供受信Python临时目录测试，不增加用户路径、环境或CLI选项。目录从可信anchor逐层no-follow打开并绑定父FD；生产祖先root所有且组/其他不可写，`/etc/v-ui`为0755。元数据文件严格regular、单link、精确owner/mode、有界读取、严格JSON及读前后FD/路径身份；沿用M1的Scope和原生no-replace，不改M1原语。

### 固定文件与准入

- `core-selection.json`：root 0644，M3的完整已确认选择结构。
- `core-selection.fence.json`：root 0644，永久初始化及最高generation栅栏，精确字段为`schema=1, initialized=true, highest_generation, confirmed_revision, settled, transaction`。`transaction`为本次32位小写hex随机事务ID，非权限nonce。
- `CORE_UPDATE_PENDING.json`：root 0644，精确`schema=1, kind=core-update-pending-v1, transaction`；一段事务的公共pending字节和inode不随phase变化。
- `core-selection.lock`：root 0600、单link空文件，非阻塞flock，不自动重试。
- `.core-update-active/`：root 0700；`journal-NNN.json`是root 0600的连续不可覆盖快照，含前一文件原字节SHA、before/after完整引用、首次停止前的panel运行状态和两core预停意图，以及后续最终意图、备份绑定、验证与发布动作。最多96份，每份≤64,000字节。 journal先写随机0600私有stage，完整fsync后NOREPLACE发布到连续名称，短写不会制造坏的已发布尾journal。候选新文件也用随机私有slot；未被journal绑定的半写stage只作为有界opaque恢复材料保留，不能授权执行、采用为新计划或被重试清理。
- `.core-update-history/<transaction>/`：root 0700，保留完成事务的原journal及所有parked原件，不自动GC。原先公开0644的selector/fence/pending被park后保持原mode与字节；私有目录限制访问。原件不属于可清除临时文件。

`read_stable(app_source=...,target=...)`及普通`backup_stable`要求无pending、无active，且selector与`settled=true`的fence具有完全一致的generation/revision。缺损、冲突和未完成清理一律拒绝，不默认退回bundle。必要的公共读取不需要writer UID或访问0700私有journal；它不证明引用材料的authority，resolver还须独立验证。

显式`bootstrap(initial,writer_observation=...,service_snapshot=...)`只接受generation0的双bundle基线，且selector/fence/pending/active/history全部缺失。`writer_observation`恰含两core，每项恰为`schema,revision,enabled`：缺state为`None,None,false`，合法legacy为`1,<完整revision>,<bool>`；schema2、坏/未知结构拒绝。caller须先真正扫描所有writer；这里只核观察结构及与snapshot的一致性，不扫描宿主。初始化中断或存在任何初始化残留，禁止重bootstrap；缺可信journal的初始损失需受信离线恢复处理，不能靠本层猜测重建。

### 有界事务接口

```python
files = SelectionFiles()  # 生产固定路径
before = files.read_stable(app_source=exact_source, target=trusted_target)
tx = files.begin(m3_plan, expected_revision=before.revision,
                 service_snapshot=pre_stop_observation)
# tx为None仅表示准确CAS后确认同ID无需变化；无裸commit(plan)接口。
files.record_quiesced(tx, final_stop_and_writer_observation,
                     app_source=exact_source, target=trusted_target)
old = files.backup_snapshot(tx, app_source=exact_source, target=trusted_target)
files.record_backup(tx, backup_receipt, app_source=exact_source, target=trusted_target)
trial = files.provision(tx, app_source=exact_source, target=trusted_target)
# trial_view只给root协调器私有提案，不发布after，不启动/恢复进程。
files.record_validation(tx, health_observation,
                        app_source=exact_source, target=trusted_target)
files.confirm(tx, app_source=exact_source, target=trusted_target)
```

`service_snapshot`精确为`panel_running`和两core的`artifact_id/revision/enabled`；artifact ID绑定完整M3 reference。`begin`在首次自动停止之前先durable pending，再durable admitting/prepared journal。快照capture与CAS读到的原selector/fence FD身份和raw SHA再次比较，不能把期间出现的foreign文件晋升为原件。普通实现不能仅持有plan就声称已完成停止或已选择运行中的新核心。

`record_quiesced`仅在prepared后记录外层真实停机且取得全部writer/data_owner独占后的最终已提交意图，允许纳入停机前已准入并完成的最后一次配置/停止。`backup_snapshot`只提供原confirmed选择、该最终意图及原panel是否运行，永不提供after作为可恢复选择。`record_backup`精确绑定`kind=backup-components-v1`、有界摘要ID、备份SHA、before revision及最终意图规范字节SHA；本块不生成或检查实际备份闭包。

`provision`仅在backed_up后进入trial，`record_validation`仅在trial后记录after revision、原panel拓扑、每core完整artifact ID/预期binary SHA、最终配置revision/停止意图及所属监听观察。原panel停止时不要求也不声称核心实际运行，`owned_listeners_verified`仅在原panel运行且该core enabled时要求为true；材料字段只是协调器已校验选择的绑定，停止core不因此取得native运行资格。原panel运行才要求`panel_health_verified=true`。这些字段须由后续真实协调器从实际descriptor、配置、所属监听及HTTPS独立取得，不能由desired元数据自填并当验收。

journal只允许`admitting→prepared→quiesced→backed_up→trial→validated→confirming→confirmed→cleanup`，以及显式`rollback_required/recovery`分支。连续journal不得改写原snapshot/plan/文件身份，已记录观察不可改写；候选确认前必须同时存在最终意图、绑定before的备份receipt及对应after的validation。失败或中断不自动重试candidate。`mark_failed`仅留恢复需求；`recovery_view`给事实，不启动进程。确认前缺件/损坏/foreign无法确定时保留材料并拒绝自动恢复。

### 发布、连续屏障与恢复

每个selector/fence替换动作先保存新私有文件，journal durable记录旧FD精确身份及raw SHA、新FD身份与raw SHA，再以原生NOREPLACE把旧公开文件park到私有固定槽。移后核原inode、owner/mode、size/mtime与字节摘要；native rename引起的ctime变化单独允许。只有核对成功才NOREPLACE发布新文件并fsync双方父目录。foreign现名、碰撞或unknown内容从不覆盖或删除；stat与rename之间的特权竞态仍可能移动foreign，检测后仅用NOREPLACE尝试归还原名，不能归还则保存私有原件并拒绝。此操作不是内核inode-conditional CAS，也不能声称阻挡恶意root绕过锁。

after selector与`settled=false` fence发布后，confirmed journal必须先持久化，after才是确认选择。之后active整体归archive，静态pending最后安全park到同archive并fsync双方父目录，cleanup journal durable后才安全替换为`settled=true` fence。因此pending已离开现名但清除fsync/确认失败时，公共read仍因false或缺失fence拒绝。`finish_cleanup`只能显式完成已有durable confirmed/cleanup journal的元数据清理，不能从trial/confirming继续候选。所有自有FD关闭均attempt；首个close失败不短路其它FD，不重试可能已释放并被复用的数字。任何关闭/写入/fsync/确认失败不返回成功，已发布或parked材料仍保留。

`rollback_metadata(tx,observed_old_topology,known_generations=...,final_intents=...)`只在外层已经停止候选、重新取得writers并实际恢复原descriptor/配置/停止意图后记录元数据。若quiesced journal尚未durable（包括最终意图写入短写），强制caller重新扫描并提供`final_intents`，写入recovery快照；缺该观察即拒绝，绝不退用较早pre_stop意图复活最后已停core。已有durable quiesced时该参数若提供必须一致。它不操作进程或data，恢复before的current及previous原值，失败candidate不会塞入previous。generation高于live selector、fence、before/after及caller所知输入值；同一已持久恢复动作可重入，但发现新的更高输入值时拒绝复用旧generation。`begin_restore(snapshot,...)`同样生成高于live/fence/输入snapshot和已知值的新generation/revision及事务ID，不复制备份revision或旧nonce；实际data/闭包恢复还未接入。

M5仅覆盖上述元数据状态机和临时目录故障逻辑。真实准入还必须把所有writer检查接到pending与完整稳定选择规则，并实现Root installation锁→worker operation→pending/原意图fsync→stop/prove→全部writer独占→最终意图/重验→备份闭包→trial→放writer保留operation→恢复原拓扑/核实际artifact/config/listeners/HTTPS→确认/clear→finish。独立v-ui子进程握手、release_writers后重新取得writers、受信trial resolver通道、完整备份恢复、权限授权及四目标实际核心/服务资格均未由本块取得；不root直接import用户session，不以假观察替代宿主验收。

### M5 独审P2：写前阶段图与confirmed边界

每次journal append在创建新stage前，重新读取上一份durable原字节，严格验证`previous→next`阶段图及不可改写字段；非法迁移在任何新journal/publication之前拒绝，不能等下一次reader才发现链损坏。

普通事务在`admitting`已durable但`prepared`写入失败时，可由受信协调器重新停/取得writers、扫描最终意图并显式调用`rollback_metadata(...,final_intents=...)`恢复；允许`admitting→recovery`，没有最终观察仍拒绝，不能自动续candidate或退用pre_stop意图。bootstrap没有可信before的早期残留仍不猜测恢复。

`confirmed`一经durable，同一事务不再允许`rollback_metadata`，即使active/pending尚在、归档或清理失败也必须先显式`finish_cleanup`。确需再回退时，在清理完成后的stable选择上创建新计划/新事务，保留已确认选择的current/previous、停止意图和新备份门槛；不把`confirmed→recovery`写入旧journal。

## M6 共享只读材料解析与 Manager 启动快照

`CoreResolver`接收受信调用方已验证的活动应用 `app_source`、实际平台 `target` 和完整的两核心 bundle descriptor。它固定调用方映射快照；bundle 引用必须与该活动应用的完整引用相同，不能按版本号猜测或退回另一个包。组件引用从独立 live `CoreCatalog` 取得 authority/tag source，再由 `CoreArtifactStore.read` 验完整材料、当前应用兼容性和引用身份，最后复核实际二进制。没有网络、写入、缺失修复或 TRUST/备份信任后备。

`current(state,core)`只做已验证选择的材料解析；`SelectionState`类型、checksum 或传入 trial 不能自行授予选择和执行权限。上层仍须认证稳定 root 选择或明确获准的受管 trial。resolver 不把引用来源、用户动作授权和实际执行结果混为一件事。

`CoreManager.configure_artifacts`只在受信启动阶段一次性安装两核心完整快照，必须在任何 runtime 或 proxy coordinator 创建之前调用。映射先整体验证，之后固定；相同快照重入无副作用，快照变化或旧 runtime 已创建时要求受管停止及新进程，不在状态查询中热换。Adapter 通过同一个 manager 锁建立唯一 runtime，并把同一 descriptor 传给 `CoreRuntime`。没有 descriptor 的旧调用仍保留原来三个位置参数和固定核心行为。内部 `runtime_artifact(core)`读取快照不创建 runtime，供后续诊断接同一材料。

本段交付时尚未接入实际 `serve`、独立诊断子进程、root 受管切换或备份闭包，后续只读接线见 M7。临时假材料和 AST FakeRuntime 测试只能证明引用拒绝、映射完整性及缓存边界，不证明真实 ELF、新版本兼容、服务切换或生产升级。分支 `6c7ccaa` 的九组 Actions 已成功；原生 ARM64 glibc 与两种 musl 的 121 项基础契约及 staged Python 的 32 项 Linux 假执行器绑定均无 skip。该证据属于先前提交，不冒充 M5/M6 新增文件或真实组件升级验收；新增契约须在其自身提交重新跑 Actions。

### 活动 bundle 事实转换

`active_verified(root)`保留原活动应用校验流程，返回`release,current,manifest,ready`；原`active(root)`仍返回前两项。只有一遍完整 payload/runtime 校验，旧manifest与READY绑定及release_id错误继续拒绝。返回字典只是这次受信校验的观察，不是可转交的authority token，也不封住其所有者后续写入。

`bundle_artifacts(payload,manifest,ready,versions=...,owner=...)`只转换这些已验事实。严格核schema1/release/platform、完整source40、实际完整target包含于manifest、两个固定core成员的摘要/size/0700，版本来自已验应用的`CoreRuntime.VERSIONS`常量，不能取顶层应用version或用户latest。owner来自整包已核的服务UID；不假设root/0755。不读文件、不再次全包hash，实际文件由resolver/runtime在各自边界复核。与serve的只读接线见M7；数据迁移仍未接入，旧legacy runtime不能无条件换成schema2。

## M7 已确认选择的启动与诊断接线

`serve`在panel/data_owner租约中复用`active_verified`单遍结果。固定VUI路径后、证书manager/DB初始化/main及任何runtime建立前，`configure_boot`读取root选择并一次性配置现有全局CoreManager。只接受stable confirmed，不从private journal阶段猜trial许可，也没有新增env、CLI、HTTP路径或版本输入。

`SelectionFiles.read_boot`仅在六类初始化痕迹（selector、fence、pending、active、history、lock）全部缺失时返回None；整个末级root目录缺失须先验证现有可信祖先，并在退出scope时重核缺失。未知稳定读取错误、孤立锁/历史、任意损坏或pending都不是“未安装”。不建目录、锁、selector或数据；原read_stable/write的缺失语义保持，write不得使用allow_missing。

持data_owner只读扫描两core实际state：逐层no-follow，私有owner/mode、单link、state≤4KiB且严格JSON，对应配置≤16MiB流式SHA及FD/路径绑定。未初始化只容missing或真实旧二键`revision/enabled`状态；schema2缺root选择拒绝。已确认选择只容missing unused或artifact_id完全匹配的schema2；既有legacy状态需要外部显式迁移。完整双核心材料解析成功且重读同选择后才configure，不能部分注入或自动回bundle。扫描只证明已保存字节，不替代新版本的配置检查/监听/健康验收。

诊断parent在检查取消/原deadline后，从同一Manager一次性发布的不可变快照直接取得sing-box descriptor，不取全局apply/recovery锁、不创建runtime；快照发布发生在受信启动完整配置两adapter之后，HTTP诊断只在启动完成后准入。既有legacy消息仍用固定核心；已选核心通过私有stdin管道发送严格内部artifact DTO，路径/引用/owner/mode由服务器快照提供，不接HTTP或profile中的任意代码来源。worker在原30秒可终止/回收生命周期内打开并复核材料，持FD经`/proc/<worker-pid>/fd/N`用于version/check/run；版本来自该材料引用，不回退1.14.2。每个执行边界重核FD，逐一尝试回收所有诊断子进程并确认死亡之后才关闭材料；取消与错误也遵循该顺序。死亡已确认但pipe清理异常仍返回失败；若任何子进程存活或无法确认，不显式close、不发成功结果，立即终止经双重校验的自身独立进程组，由原父端回收失败worker，不误杀其他进程组。关闭失败输出固定CORE_UNAVAILABLE并清空exit_ip，原父端输出allowlist不放宽。无parent进程内大文件hash、无runtime或node状态写入。

本块19项boot临时目录/AST与17项诊断纯mock覆盖源码边界，未执行真实核心或server。真实组件发布、不同新版本原生诊断/配置与固定客户端、受管stop/backup/trial/confirm/recover、全部writer准入、完整备份恢复、终端/面板/维护入口均仍待完成；只读接线不取得这些资格。所有构建、安装和后续真实执行继续仅在GitHub Actions，浏览器未验证。

### 恢复目标意图与原 live 意图分离

完整数据恢复可带回另一份配置revision及启停意图。`begin_restore`因此明确要求`source_backup_sha256`与`restored_intents`，由受信协调器在核实际incoming备份、state2、配置SHA和材料闭包后提供。方法先按incoming选择核双core artifact_id、revision/停止意图及来源SHA格式，再建立pending；这些观察不授材料、备份或执行权。

初始private journal将`restore_target={kind:core-restore-target-v1,source_backup_sha256,intents}`永久绑定，阶段转移不准改写。restore的after健康验证使用此incoming目标；quiesced仍是停止并独占后的原live-final，`backup_snapshot`和失败rollback继续使用它。普通核心更新仍保持原配置/停止意图。既有新generation、高水位、fresh事务和不恢复旧nonce的规则不变；本块只元数据，不交换实际data或启动服务。

## M8 Actions候选组件生产（尚无发行或原生资格）

`scripts/fetch_core_candidate_inputs.py`固定首轮sing-box1.14.3和Xray26.10.21的官方Release/asset ID、外部digest及完整tag-source；不改现有应用PINS/VERSIONS或旧下载注册表。实际HEAD核验后，前后复核官方repo/release/所选资产/完整tag peel身份，从固定官方API资产及codeload commit URL流式获取原材料。API与source禁redirect，资产限官方CDN及总三跳；只有typed502/503/504或timeout允许同URL第二次尝试，403/429、TLS、长度/摘要/结构或close失败不重试。不读取token/proxy环境，不把socket timeout称为硬墙钟；Actions另有25分钟任务上限。

SOURCE gzip的size/SHA是本次固定commit、已验证HTTPS响应的观察，receipt明确`independently_published_digest=false`；它不是官方另外公布的digest，也不因写进候选metadata而自授来源。archive则同时绑定已审pin和官方API外部digest。所有素材仅落在自有private workspace/stage，流式有界、失败响应关闭，输出原子NOREPLACE；不覆盖或清理其他输出。

`scripts/build_core_component.py build-target`核原archive/source大小与SHA，扫描安全成员，取唯一准确binary及canonical`<repo短名>-<commit>/LICENSE`，保留binary、完整SOURCE及license原bytes，不strip/重编译/换行。`assemble`复核四目标M0闭包、来源上下文、SOURCE中实际license原字节和Xray共用原asset的binary一致性，产生四ZIP、CORE-RELEASE及不含自身hash的五行SHA表。严格COMMON比较区分JSON boolean与integer，拒绝Python `True==1`产生的schema/revision假等值。aggregate不持原archive，不能单靠pack自hash重演历史提取；后续资格必须绑定实际生产过程及不可变artifact receipt。

新增`core-component-candidate.yml`仅read权限、PR/manual候选生产，使用项目既有固定Action SHA及Ubuntu24.04。纯fixture双Python后，两个core各下载原材料、构建四包及汇总，上传六文件及小receipt；production receipt带repo/run/attempt/source上下文，artifact名称也绑定本次run/attempt。`compatibility.app_sources=[准确HEAD]`在这里是候选待验声明。全部输出明确`qualified=false,published=false`，不生成Authority、tag/Release、catalog或安装选择，不执行新核心。真实下载、原source结构及完整性、四目标新版本native/固定客户端、实际切换及发行门禁均待其各自Actions，不得把格式生产当兼容资格。

9b02fc3首次全套单元3231项中7fail/5error的原记录保留：新boot夹具受前序umask077影响、旧active mock名及旧诊断harness未独立session。e902cd6只修三个测试，原mode/PGID/journal零调用等断言未放宽；umask077下双Python19项通过，后续Actions主单元已通过，完整自身资格另核。新M5/M8改动仍须自身提交验收；浏览器与生产未验证。

候选workflow首次63d7ce0的[42511635640](https://github.com/ForceMind/V-UI/actions/runs/42511635640)在job建立前被GitHub解析拒绝，原注释为`Unrecognized named-value: 'runner'`（48行job级env）；尚未下载或打包材料。按[GitHub context可用范围](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)，改为runner实际执行的首个step用`RUNNER_TEMP`生成私有工作路径，并经`GITHUB_ENV`传给后续step，不扩大权限或变更来源门禁。修正必须以自己的实际Actions运行证明，不能将本地Python语法检查当workflow验证。

修正后的474e6e7在[候选42512335279](https://github.com/ForceMind/V-UI/actions/runs/42512335279)完成两个Python pure job及两个实际素材/打包job；sing-box和Xray各四目标候选均产出。它只证明本次真实来源获取和格式生产，仍未执行不同新核心、验证native兼容或受管切换。同源默认应用包的oneclick42512335118在`core_sources`读取GitHub元信息时遇403限流，producer未完成，安装角色未执行；原日志及失败保留，不改认证/核心pin或断言绕过。

候选大artifact之后另生成小evidence artifact：绑定实际上传返回的producer artifact ID/name/digest、repo/run/attempt/source和真实acquisition/production/expected收据，严格核三类kind/schema、来源SHA与同源context。单独的小ZIP便于只读取元数据证据、不下载产品包；它不包含自引用hash，也不授来源或native资格。consumer仍须将其中artifact身份与独立GitHub API元数据核对，不能相信小收据自述。只有完整生产和大artifact上传成功才发小证据，缺件或跨run/摘要格式错误拒，已有证据文件不覆盖。

## M9 writer循环与历史制品归档前置

`_MaintenanceSession.release_writers_for_trial()`是显式私有入口，普通`release_writers`不新增全包校验成本。它只在可验证应用/data的done状态建立绑定本session/PID、不可复制序列化的checkpoint：Root/CURRENT原bytes和身份、活动manifest/READY、独立bootstrap及data代次。writer仍持有时捕获，再释放writer保留实际operation锁。此checkpoint不是Root核心事务、试跑授权、旧descriptor或实际停机的证明。

`reacquire_writers()`只允许同checkpoint的一轮重取，按panel→data_owner独占→双core owner/cert worker取锁，前后重验上下文；漂移、忙或部分获取失败进入failed，清理所取writers但operation保留到close。成功使用专用guard，不能重新execute旧action。writer栈每代有生命周期token，栈关闭立即撤销guard；operation/lease proof也绑定PID及关闭latch，FD号复用不能复活。`release_reacquired_writers()`在后续明确协调器恢复工作后核guard，先撤销valid/token/checkpoint再放writers，进入terminal状态，operation仍保留，供外层恢复/验证旧拓扑后close；不自动启动服务，也不能借此开始第二轮。

`close`即使writer关闭失败仍尝试operation关闭并终态closed。相邻`_maintenance_directory`的early-close丢ack可漏child并重关复用FD，已用真实纯临时red复现并改为按所有权保留所有ancestor FD、各尝试关闭一次，正常yield/mode/owner/no-follow语义保持；成本为每目录context额外O(path depth)个FD。旧`_maintenance_open_archive`的相似walk不由本块宣称已修；新增checkpoint不调用它，完整恢复接入该路径时仍须有针对性资格。

历史previous可保存而在当前应用上不可执行，因此新增`CoreCatalog.read_archive`：从独立live root catalog先验证manifest/table外部size/SHA，再用已认证声明的历史source内部复核完整M0。返回独立ArchiveCatalogEntry，声明来源列表不表示当前app资格，不返回VerifiedRelease/app_source/binary路径。不能从backup或TRUST挑source补建信任。

`CoreArtifactStore.open_archive(ref,target=...,catalog=...)`自行读取独立catalog，验证全部9文件及完整Ref、独立host target，提供仅context内可用的ZIP chunks/size/ref；流拒复制/序列化、跨PID与退出后读取，正常退出还须摘要及FD/路径稳定。调用方必须等整个with成功退出才确认备份完成。`prepare_archive`在Root UID和完整来源/原FD/Ref预验后复用NOREPLACE准备，仅返回归档结果；不暴露普通PackIdentity/binary_path，也不扩大普通read/prepare/resolver的当前app兼容门槛。缺独立catalog仍在变更前拒绝，previous bundle仅保留完整Ref及原应用外部恢复依赖，不复制runtime或静默重绑。

两块分别18项writer实际flock/fake上下文与14项归档假包测试，属于源码/临时文件前置。受信Root pending/stop绑定、IPC握手、旧descriptor及真实服务健康、完整backup-components-v1数据与选择双域事务、全部入口和真实不同版本native资格仍未接通，不把这些私有原语称完整可用的核心升级。

## M10 component私有闭包stage与inspect（尚未交换data）

`CoreBackup.stage`只在受信调用方指定的现有0700私有workspace下创建新闭包，flat `SNAPSHOT.json`及每个唯一manifest完整SHA前缀的原ZIP/CORE-RELEASE/SHA256SUMS，文件600、目录700；最多四组件、总ZIP≤400MB，保留原bytes，不加入TRUST/authority/pending/旧nonce或整个应用runtime。Snapshot保存完整M3选择、双current artifact_id/revision/enabled和原panel状态；它是否确为M5稳定或before/quiesced最终意图，仍由外层真实Root协调器和整个备份外部SHA绑定，对象、flag及本模块不自证confirmed。

stage先用普通resolver验证两current在实际app/host可用；历史previous仅走独立catalog/archive来源和完整9文件验证，流式复制且必须等open_archive成功退出后才发布。随后对整个闭包再inspect，fsync和NOREPLACE完成后才返回，不采纳既有残缺或清理foreign；发布后失ack/close失败保留完整公开闭包并报错。

inspect将闭包视为不可信，从strict Snapshot导出精确文件集合，核父FD/owner/mode/no-follow/单link、大小和预算，再与独立live catalog原metadata比对、M0验证每个原ZIP全Ref。current component额外经过普通catalog.read(actualapp_source)及M0，current bundle严格匹配已整应用校验的活动descriptor；历史bundle只作为原应用外部依赖。缺Root store也能完成输入检查，返回的ArchivePackFact仅供后续Root prepare_archive，不能当PackIdentity或RuntimeArtifact，不从备份seed信任、不写Root store或data。

十五项纯假包/临时文件测试验证上述前置；完整backup-components-v1归档组装、旧reader拒绝、Root双域恢复/中断握手、授权Root试跑启动及真正不同版本native仍未接通。819d9c9自身主单元3304项首次有一个夹具失败：前序umask077将用于拒绝检查的0755目录建成0700；仅给自建坏夹具显式chmod0755，双Python18项在umask077下通过，不放宽生产模式检查。x64 musl当时294契约已通过，但随后默认bundle的core_sources API GET被403限流中断；原失败不抹除，不等同源码回归或整套成功。

## M11 一次性managed启动传输与进程身份（尚无启动permit）

Root任务内临时broker使用固定`/run/v-ui-maintenance/core-boot.sock`，Root目录0750/service gid、socket0660；先listen再由实际Root协调器arm精确MainPID/UID/start/cgroup，一次claim只服务这个进程。Root必须持续提供实际installation/operation/worker生命周期proof；journal phase、磁盘task状态、env或请求布尔不授权。模块只做通道，不能替Root协调器确认锁/停机、读用户payload作代码或运行服务。

协议采用4字节长度与≤64KiB strict JSON，claim只带观察到的app_source、host target和data代次，不接受选择、PID或路径。Root响应选择trial或old_recovery、完整selection/intents、应用CURRENT/manifest/READY摘要，以及core pending/selector/fence和RESTORE_PENDING的精确身份+原bytes/absence摘要。相同已鉴别连接用于startup_ready、request_probe/result和释放；Root仍须自行核TLS/核心与所属监听，不能用client ok代替实际资格。

释放为三阶段：Root发送选定revision和最终barrier事实，client在实际磁盘检查成功后回release_ack但仍gated；Root再核live proof/精确身份后发released终帧，client才调用释放回调。此前EOF、超时、Root撤销、worker或身份失效均关闭上下文，不能自动续candidate；最终已确认文件与分布式结果不确定由协调器另做读回/恢复，通道不伪称原子性。socket碰撞不自动覆盖/删除，失效endpoint的恢复也须明确Root停机证明后处理。

应用侧`managed_boot`只固定地址核Root peer、观察事实和完整M3选择，封闭session/PID/生命周期并拒复制或序列化。它明确不是data_owner permit，不放宽M7 `read_boot`或普通RESTORE_PENDING拒绝，也未接serve/main的生命周期、HTTP gate、证书调度或实际启动。trusted probe/settle回调不得拿期望值冒实际观察，解封需后续实际接线。

`Services.managed_identity(nonrootUID)`固定观察v-ui.service，复用原systemd配置漂移守卫，核实际proc四UID、start ticks及精确cgroup，前后重读身份；state原active/inactive/unknown语义保持。当前Type=simple可在app启动完成前取得active MainPID供arm，但这不是健康验证，transitional/不明身份继续拒绝。只读取stat/status/cgroup，不读cmdline/environ。

M11本机验证范围：应用client16项与服务身份11项双Python纯假/临时文件通过；既有ServiceTests11项另回归通过。broker十九项在macOS为18pass+1个Linux kernel case未执行，本机AF_UNIX bind遭sandbox EPERM后，仅非Linux使用fake监听/节点及socketpair，不将此称Root、systemd或真实Unix监听资格。Linux Actions必须实际bind及SO_PEERCRED，不能按本机能力跳过；staged运行时合同保留原32项并新增broker19项，合计51项全部强制执行无skip，默认纯合同加client/身份为336项。idle撤销原定点红例保留：有context但无startup_ready、ready空闲时proof/身份失效原本等到总deadline；现0.25秒调度live核验，并保留typed proof错误而非吞为取消。调度仍不能抢占阻塞Root callback，回调本身须遵守Root总deadline。

## M12 私有worker保留operation握手（尚无核心动作）

`ops_worker`原prepared/proceed/done及普通release_writers/finish往返保持。仅Root私有stdin可显式选择`release_writers_for_trial`，进入trial_writers_released后只接受prove_operation、一次reacquire_writers或finish；重取后必须实际专用writer guard通过才能发事件，随后仅release_reacquired_writers；terminal recovery_writers_released只可prove或finish。所有命令精确只有command键，不接受Root对象、PID、路径、URL、callback或新公开action，旧execute只运行一次且quiet块保持。

`prove_operation`实际调用M9 held-FD/closed-latch/PID闭包且返回必须None，不能以alive、bool或marker授证明；它只证明该worker内operation仍保持，不证明Root installation锁、Root核心事务、旧descriptor或停机。Root协调器必须将这些独立事实共同绑定到broker，且在稳定文件与managed released完成后才发成功finish。循环不开放任意恢复执行命令。

整个worker仍同一总deadline，不因heartbeat延长；EOF、未知/错阶段/重复/extra字段及guard失败均固定error/非零并关闭session。session.close即使在finish之后失败也仍是失败，不被早先成功返回掩盖；signal handlers恢复保持。九项纯fake/临时真实pipe/flock测试双Python通过，尚未把此握手接到新的Root核心任务或实际服务/数据恢复。

1e01224自身主单元3365项有一处新fixture错误：预建broker父目录0750受前序umask077变0700，生产正确报PARENT_UNSAFE。只对自建测试目录显式chmod0750，保留Root门禁、foreign碰撞/替换不得删除及原模式断言；双Python在umask077下18pass/1Linux case未本机执行。该提交ARM64 GNU原生required Build已通过，不能据此覆盖其他目标或新fixture修正提交；Linux kernel与完整准确源码资格继续分别核。


## M13 完整私有备份容器与输入预检（不执行恢复）

新私有容器 `backup-components-v1` 包含原格式 `data/snapshot.zip` 与M10的 `core/` 闭包。原备份writer只抽出共用data快照helper，旧格式与默认restore白名单保持；新容器没有加入普通restore。内层沿用原 `backup` / `backup-dns01-v1`、隐藏文件精确名单、SQLite快照、DNS私有引用与proxy快照规则。分层预算保留：data ZIP≤850MB、含MANIFEST展开≤1.1GB/最多10000成员；核心最多四份原ZIP/合计400MB，metadata另有界；外层最多15成员与固定总预算。内层writer完成后按原reader口径再核预算，不能产生自己无法预检的公开备份。

writer要求实际stopped guard，私有暂存和流式归档完成后以NOREPLACE公开；caller仍须证明selection来自M5稳定/before状态及最终停止意图，不能用参数自证。preflight将备份视为不可信：冷副本核外部SHA、严格namespace/manifest/分层预算、独立live catalog核核心闭包，再用原reader展开data。只有本次新暂存目录会规范化权限；不改live data、Root store/catalog/选择、不撤销授权、不暂停DNS、不交换data。实际runtime配置摘要、state2 ID/原停止意图、数据库路由fence及既有committed/protected policy均须一致。旧policy checker新增内部可选精确双核心ID，仅用于已经检查闭包的state2；默认仍只接受旧二键。legacy bundle state原bytes保留，返回显式迁移清单，组件current不能借此采用legacy状态。

二十项假材料/临时文件测试覆盖完整四份组件闭包（两核心current/previous）、DNS profile/挑战及零字节私有fragment、proxy marker/空secret/active relay、默认旧reader与错误ID拒绝、节点/会话数据原样、篡改后重哈希输入拒绝、预算含MANIFEST、独立catalog缺失、symlink/hardlink和旧普通restore拒绝。此处没有Root双域交换、撤销授权、受管恢复启动或实际系统服务，不能称完整核心升级可用。真正原23工具负向与四目标完整链路仍须Actions。

db0f64a自身九组中八组及额外候选材料四job成功；原生ARM GNU和distro成功，两个musl在严格默认345合同因真实service-user case于root容器被skip而失败（[原始运行42524739739](https://github.com/ForceMind/V-UI/actions/runs/42524739739)，ARM job128865731750/x64 job128865731707）。不是下载失败，也未取得整个矩阵资格。修复只将既有smoke用户创建提前，以实际非root身份执行合同；root构建和后续staged验证保持，仍要求所有合同零skip。新源码须自己的Actions，不把这次修复或前置成功泛化成准确新候选资格。


## M14 实际受管启动接线（尚无Root核心任务）

`serve`在持panel lease并完成活动应用完整校验后，遇core/restore pending只向固定Root broker申请一次claim；无pending仍走原confirmed启动。假的连接、JSON/phase/env、替代地址/owner/socket factory或失败claim均不能获得data_owner恢复例外，也不会回落普通启动。只有真实native socket、固定生产路径及直接kernel UID0凭据支持本进程短期scope；普通真实flock仍先取得，例外只限启动线程和同一控制线程，不能传到HTTP线程池、fork或序列化。

managed路径要求现有SQLite文件和精确state2/config/选择意图，不运行init_db、迁移、apply/restore或普通可写恢复。既有proxy的每次继承读取都显式禁止初始化，并在启动前复用现有编译/地址安全恢复guard；原revision、凭据、停止意图不改。全部HTTP/WS（包括登录、订阅、导出）先被外层gate挡住，503中文；证书worker暂停。Root仍须独立核TLS与实际核心资格，不能把client ready/ok当健康。

同一Root连接监控启动期间EOF/撤销，实际probe读取运行FD身份、状态/配置revision和所属监听；selection revision从已配置snapshot重算。释放时实际检查原应用、data代次、pending清除和stable selection/fence；release_ack仍gated，只有Root最终released且再次核完磁盘事实才启动证书worker和开放HTTP。排队回调前撤销仍拒绝；撤销停止每个已创建核心并请求uvicorn退出，任何cleanup失败继续报错而非干净成功。

新增十六项文件/真实临时flock、fake transport、ASGI、AST及只读SQLite检查，两Python均通过；既有client16与boot19另回归通过。主智能体对实际七文件独审并复查上述边界，不以源码/合成检查冒称固定Root socket跨UID、systemd、新核心或公网测试。最初新case因macOS的/etc符号路径、错误fake stopped identity失败，测试明确fake Root缺席并修fixture，生产noFollow/身份门禁未放宽。

当前仍缺真正Root安装/操作锁与worker存活组合证明、停止后最终意图握手、切换/失败/中断恢复协调器及双域恢复。普通运行进程在Root CORE_UPDATE_PENDING出现时的新writer准入也尚未接通；本节完成的是pending启动后的私有scope/gate，不能称全部writer已挡住。完整四目标、不同核心版本、保数据切换与安装资格须后续Actions。


## M15 Root安装锁的私有持续证明（尚未组合任务）

`installation_lock()`默认仍走旧分支并yield None。新私有 `_proof=True` 仅在实际flock已取得后yield callable：核调用PID、active/closed latch、原held FD完整身份、Root/600/regular/单link、名字与固定parent路径绑定；不以bool或marker作证明，不重新获取flock掩盖持有中断。broker线程可调用，fork不可继承授权。新分支单独保全部祖先FD并逐层noFollow核Root/sticky精确规则，不复用旧walk的提前close；旧helper的同类失ack缺口仍是既有待办，不声称一并修复。

作用域退出先失效再关FD，丢close ack和FD数复用不能复活旧proof。独审复现过inflight缺陷：验证线程在祖先FD清理时暂停，owner退出并释放锁后仍返回成功。现验证结束后再核latch/PID，将最后生命周期读定义为线性化点，原回归已从实际红例转绿；不承诺返回后的未来持有，Root协调器必须先结束并join全部broker回调，再退出此scope。

十项临时文件/实际flock与明确fake Root metadata测试在两Python通过；默认旧锁回归另验。当前仅提供安装锁这一份实际证明，还未接daemon核心任务，不能代替worker的operation proof、服务quiescence、三阶段释放或真实Root安装验收。


## M16 普通运行准入与启动互斥回归

固定 `/etc/v-ui/CORE_UPDATE_PENDING.json` 只要存在（含无效内容、目录、断链）或检查失败，就在取得真实SH后拒绝新的普通data_owner；不读取phase或把env/布尔当许可。已准入操作可完成，真实EX及同PID/线程借用保持；private native scope仍是唯一受管SH例外。所有HTTP/WS即使尚无managed上下文或上轮已released也会在新pending时关闭，检查异常failclosed，lifespan直通。准入竞态的DataOwnerError统一中文503，无私密异常回显。普通离线Root管理入口的全量pending/兼容守卫仍须接后续任务，不能从本节推定所有入口已完成。

新私有data staging在M13 `_validate_data` 整段持真实EX，DNS/proxy共享读取只借其实际open-file description；sibling lease位于data外，不入ZIP，source live data仍需调用方实际EX。CORE pending下仅传fake stopped flag的live快照会拒绝；实际EX下DNS/active proxy/state2容器创建、预检和原bytes保持已作临时文件验证，未向调用者留下SH绕过能力。

d128a58自身[主单元42528797556](https://github.com/ForceMind/V-UI/actions/runs/42528797556)的3410项出现1fail/1error（原239skip）：普通启动把active_verified放到data_owner前，破坏两条既有早期排他断言。现普通路径先取得真实SH再校验；只有入口已pending才走verify→Root claim→私有SH，普通验证期间新pending不提升为managed，初始pending消失也不回落。旧test_data_owner断言原样保留，新增四项实际serve/tempflock负例在本机验证，但旧两项的完整Linux回归须新源Actions；不以本机模拟替代。

新增准入十一项、启动负例四项，连同M15十项与既有合同，当前完整driver为406项，两Python实际执行无skip。首次整组曾因原AST夹具未注入新只读helper、Root元数据夹具将真实/tmp1777带入非writable生产前提而失败，3.12错误打印另缺st_mtime；现仅补正常无pending的AST依赖、完整stat属性及fixture外祖先的明确fake Root/nonwritable元数据。fixture内真实bad mode/owner/link断言及生产Root规则保持，原失败保留。真正Root固定socket/systemd、不同核心切换与故障恢复仍待后续Actions。


## M17–M20 私有核心切换、首次事务与固定安装闭包

私有nonroot worker以同一实际operation完成 `core_context → check_core → prepared → proceed → core_quiesced → backup_core → core_backed_up → bind_core_ids → core_ids_bound`，随后接原单次writer试跑周期。Root先由实际worker取当前source/target，不从调用者自报或Root读取用户CURRENT推断相容性；候选在停服前核官方材料、现有只读policy及同FD的固定版本/config检查。最终EX下再次验证；只在M5记录quiesced、M13备份外部SHA应答后迁移state2，保持配置、revision和最后enabled，缺状态不建。已重取writers时只修复已保留的before/after身份，不接受任意恢复路径、callback或目标，不回滚DB。两份state文件未声称跨文件原子；部分写失效保留备份和pending。

独审实际复现冷检返回时同组后代仍活、以及慢应用校验中释放五writer后仍写state2。现冷检在既有隔离worker组，以Linux子回收者/内核ECHILD确认空子树，未知活后代不返回干净结果；writer在慢读后、每个写边界及完成后复核原真实generation、PID与operation。只读proxy checker强制不初始化，M13私有暂存同样复用；应用MANIFEST仍使用自身4MB严格JSON/重复键拒绝，与组件64KB预算分开。新进程位点是 `app/core_updates/maintenance.py::_command` 固定native Popen、`_abort_command_group` 仅自身已验证组的SIGKILL，及 `tests/test_core_cold_check_linux.py` 隔离假Python后代；生产代码不启独立逃逸组、不向协议或日志透传原配置。Linux用例加入staged强制52项，macOS未执行该内核用例。

安装器从已验同提交source ZIP复制精确15份内部core helper，和Root运行receipt/worker pathless闭包相符，不增顶层公开kit资产。刷新只接纳完整当前、完整pre-core DNS、完整历史pre-DNS等精确整组形状；部分core和无收据已有文件拒绝。P3开发候选仍用23应用schema，Actions历史升级fixture明确选独立P3闭包配置，要求全15项增量；原23默认DNS-only断言保持。未据此修改冻结PR145、VERSION或公开入口。

首次切换在一笔M5事务中保留逻辑旧bundle基线、实际原selector/fence均缺席；没有先发布一个尚未绑定state2的稳定选择。拒已有锁或其它初始化trace，pending先持久化，按同样备份与健康证据确认；首次失败允许恢复旧bundle到更高generation。Root私有协调器已消费实际worker协议、组合安装/operation证明、串行整个pipe往返、精确Systemd身份与M5/barrier；同会话已释放writers后的试跑失败可按旧ID重取、旧启动和恢复确认。已开始确认后不假报回退，未知早阶段/进程中断保留恢复义务。

独审另复现Type=simple降权前PID、lifespan早于bind、空预期监听、broker join超时尾窗。现仅arm前对已核cgroup的全UID0有界等待，并把局部预算传到实际查询；app自己的Uvicorn listener开始服务后才发私有ready，probe/settle与最终释放再次核，空预期集合不冒称有端口，仍核实际核心child与FD。Root独立核TLS，终帧后还核同一面板实际提供200登录页。join与结束后owned endpoint清理分开；停止回调后才退出锁与固定模块，仍不能证明时仅隔离任务自身退出，不报告成功。Root成功前留一代回退空间。

当前本机两Python各456项合成合同无skip、安装闭包fixture各48项，另历史升级helper23项。以上不是实际Root跨UID、systemd、新核心/固定客户端或生产验收。私有协调器尚未接daemon公开action；原停止面板保持停止。停机后、绑定前的失败恢复与断电重入、保留旧自带私有CA的独立健康验证、完整data/选择双域恢复、所有离线入口守卫、终端/面板/远程权限与四目标真实新核心资格仍需完成。当前OS CA校验不过会在停服前拒绝，未关闭TLS检查。

## M21 持久恢复上下文与自带证书健康检查

停机前保存完整三个 systemd 单元的拓扑、已验证应用的三个文件摘要、数据目录代次、受信服务配置摘要和已验证的公有证书链摘要。旧记录保留原解析方式。新切换的备份收据同时绑定固定文件名和归档摘要。确认收尾先完成 fence 发布，再归档 pending，避免两者同时缺席时失去事务定位。缺原记录必须有同一发布动作的未耗用暂存和对应已停放记录才能解释；恢复读取及最后写入均重核。

尚未确认的早失败可在同一安装锁下关闭原 worker，由新 worker 取得 operation 和真实 writers 后恢复。显式私有恢复也从 Root journal 取原拓扑和身份，复核现有应用和 data。未写新 ID 的 state2 可原样恢复，缺最终意图时在 EX 下重新观察。部分 ID 写入只修复已知 before/after 身份，并先核已保存备份字节。无可信备份的首次 legacy 迁移及未知状态保留 pending。确认记录已持久化时保留已确认选择，确认态重启协调仍待完成。

自带证书的检查由同一非 root worker 读取固定公有链文件，校验本机数字地址、主机名、证书有效性和现场叶证书。私有链仅进入独立 SSLContext，协调器只接收状态和摘要。试跑与解封后仍核同一服务进程。恢复首帧的上限为 16 KiB，其余请求保留 8 KiB。固定安装闭包现在包含 16 个内部 helper。

[b8b0964 主测试](https://github.com/ForceMind/V-UI/actions/runs/42536294524) 实际执行 3489 项，有一项进程清单不匹配，原 239 项跳过。新增 Linux 假后代用例已在 [三个 staged Linux 目标](https://github.com/ForceMind/V-UI/actions/runs/42536294496) 实际进入 52 项检查，均无跳过。清单现已补齐该固定调用点，严格匹配断言保留。

当前两版 Python 各 478 项合成合同通过，无跳过。恢复和私有 CA 测试包含真实临时 flock、归档校验及替身传输。真实 Root、systemd、私有 CA 全链路及不同核心验收仍需 Actions。公开操作入口、确认态重启、双域恢复和离线入口守卫仍列为后续工作。

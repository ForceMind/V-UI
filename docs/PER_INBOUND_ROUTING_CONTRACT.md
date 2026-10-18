# 每入口直连 / 中转 TCP 契约

2026-10-18 设计草案，基线 `a98da9afbbf58a54f4f102cccec2f26b6e07d2ee`。用户已确认方向；此稿须经独立设计审查再实施。进度与接手步骤见 [当前交接](DEVELOPMENT_HANDOFF.md)。

## 1. 范围与标准客户端

同一实例可同时提供普通 VLESS 直连节点与中转节点。手机导入标准节点，第一跳只含该入口的 server/port/UUID/SNI/TLS 参数；服务器完成第二跳。节点备注不决定路由，不增加私有 VLESS URI 参数，不要求自研手机 App。

首期新增资格仅为已有 sing-box/VLESS/直接 TCP/验证 TLS、空 flow 的中转资格面。混合直连/中转的本轮真实验收限定同一 sing-box 实例；不能借此删除既有 Xray 残余运行门禁或宣布跨核心混用获资格。固定 sing-box 1.14.2、Mihomo 1.19.32、Xray 26.3.27 和 VERSION 保持。有效 relay 入口继续服务器拒绝应用 UDP；普通 direct / inherit-direct 既有功能不静默改变。REALITY 上游既有资格不等于扩大本轮入站资格。

## 2. 三态与两个独立开关

新增持久字段 `routing_mode`，严格枚举：

| 存储值 | UI | 有效行为 |
| --- | --- | --- |
| `inherit` | 跟随全局（默认） | 全局 `relay_enabled` true 为 relay，否则 direct |
| `direct` | 指定直连 | 不加入中转来源集合，使用本服务器普通出口；不受全局默认开关影响 |
| `relay` | 指定中转 | 使用单一已选上游，即使全局默认开关 false；失败关闭，无 direct 回退 |

`local_proxy_enabled` 仅控制既有有鉴权 loopback 主机代理，不改变任何入口模式。`relay_enabled` 的文案改为“默认入口中转”，只影响 inherit。任意有效中转入口、开启主机代理，或开启全局默认中转（即使暂时无入口，保留旧语义）都需要可用且完整验证的 `selected_upstream_id`。不新增每入口上游选择。

“direct”沿用普通入口的既有路由/拒绝规则，不绕过用户已有 block/DNS 安全规则；它明确排除该中转 overlay。编译器必须验证普通出口不能引用保留的中转命名空间，不能仅凭 UI 文案宣称直出。未纳入本轮的协议只保留 inherit 旧行为；明确覆盖值的支持范围由严格资格验证拒绝，不能无声忽略。

## 3. 数据、迁移与输入

- SQLite Inbound 增加非空字符串 `routing_mode`，新建默认 inherit。旧 schema 缺列时，在现有 data-owner 和 operation 锁约束下幂等迁移为 inherit，保留所有 ID/UUID/端口/材料与数据。
- **已有列**出现 null、空串、大小写变体、未知值时拒绝，不以 `getattr(..., 'inherit')` 或 truthy fallback 吞掉腐败；不能借重建数据库“修复”。
- API 新建省略字段默认 inherit；更新省略严格保留。显式 null、非字符串及未知值 422，失败不持久化。包括 legacy sing-box CRUD，不能让完整 model_dump 的默认覆盖省略字段。
- 字段是独立路由元数据，不藏入协议 settings/stream_settings、备注或导出 URI。普通 allowlist 响应允许展示非秘密的 mode，不扩大凭据可见性。
- 禁用的 relay 节点可保存为草稿，不发起网络、不宣称可用。启用或切换为有效 relay 前必须校验所需上游、入口资格、全量快照与地址策略；无可应用上游时返回明确拒绝，不以 direct 运行。
- 首次指定 direct 不需要海外上游：双开关 off、selected_upstream 为空时可正常 check/apply 建立 active。保存时尚无 active 则显示未应用，不走普通 base 配置假称成功。
- 首次指定 relay 流程可完成而无需临时打开全局：先导入/选择上游，在双开关均 off 时正常 check/apply 该选择；保存禁用 relay 节点；启用时使用该已提交选择做 preparation/reconcile。active=None 时不启用 relay，UI 明确指引先应用已选上游。不能把未应用 draft 凭据当成 active。
- active 双开关 off/endpoint=None 的首次启用，由节点的纯 precommit 检查确认已选模型/模式/入口资格；后续显式 reconcile 在共同锁内有界解析、验证和固定该 active 上游，生成完整候选，再按原 commit-last 事务应用。fresh pin 仅限旧 committed snapshot 确实不需上游、其合法 context.endpoint=None，而新候选首次需要的转换；若旧 committed 已需上游却缺 endpoint，视为损坏并停住，绝不重新解析补救。判断旧状态不读取已修改的 desired DB 来猜测。status、startup recovery 不进行这次隐式解析；失败可重试显式应用，不必先开 global。
- 第一次保存任一非 inherit 模式前，先在 proxy 顶层私有 `state.schema_version` 升为 2 作为能力标记，使 a98 的旧 reader 本身拒绝；仅加 SQLite 列不够。marker 写入在行提交之前，失败不改行；若行提交失败，保留较新 marker 是安全的单调结果。全 inherit 旧安装不无故升级身份。仅升级 draft settings 不足，因为旧 load_active 未必读取 draft；顶层 state 则由旧 `_validate_state` 固定拒绝非1。settings/revision/secret 原结构不为此任意升级；新 runtime_context 另用 v2 保存模式快照。新程序同时严格读旧 v1 和新 v2；需要精确旧 reader 降级拒绝测试。

## 4. 统一有效策略与编译

实现一个无 I/O、严格输入的有效策略模块，集中计算 enabled inbound 的 mode/effective route、relay 来源集合、是否需要上游和非秘密能力摘要。数据层、compiler、manager、apply、export、recovery 必须用相同语义；禁止各自按两个全局 flag 短路。

- 仅有效 relay 子集进入 `_bind_ingress`、TLS/协议资格、禁止地址/全 DNS 答案检查、受保护业务 DNS、TCP 上游与末端 reject；direct 不被 global=true 覆盖。
- host local 来源独立追加。空来源集合不能生成匹配所有入口的 rule。
- 基于 ID/tag/port 的完整绑定检查保留：重复 tag、漏绑定、多余核心行、保留命名空间冲突、相互串路拒绝。
- 上游需求维持旧语义：host OR global-default OR 存在 enabled explicit relay；实际保护来源仍只取有效 relay 集合。
- 所有旧全 inherit 输入须保持既有核心配置和 `policy_identity`。现 identity 包括 coverage，不能简单给旧 coverage 添加字段使所有已应用状态失配。新模式必须有明确身份域/版本；旧 immutable snapshot 不改写、不跳过校验。
- desired mode、全局 settings、所选上游、入口全字段、TLS/路由来源、地址 pin 与编译结果必须在同一操作锁内绑定；变更后旧 check token 不可复用。

## 5. Apply、重启、回滚和恢复硬门槛

现有 commit-last runtime 事务、数据 owner/组件锁、容量预留和幂等 transaction ID 保留。新增模式不是“保存字段后调用一次重启”就完成。

必须审计以下调用点（以实现时准确源码为准）：

| 层 | 必审代码 |
| --- | --- |
| 模型/行变更 | `app/models/database.py`、`inbound_service.py`、`api/inbounds.py`、`api/singbox.py` |
| 编译/入口绑定 | `proxy_policy.py`、`core_manager.py` 的 validate/apply/recover/status/Xray gate |
| 事务 | `proxy_apply.py` 的 `_compile`、`compile_active`、`reconcile`、rollback/recovery guard |
| 私有状态 | `proxy_store.py` active endpoint 校验、selected upstream 删除保护、pending/history/容量 |
| 离线恢复/备份 | `proxy_recovery.py` 的行重建/校验、`release_tools.py` restore、对应 installed smoke |
| 导出/诊断 | `proxy_export.py`、`validated_export.py`、subscription API、proxy coverage/status |

关键不变量：

1. 全局两个 flag 都 false，但存在 enabled explicit relay，仍须加载 active 保护状态、endpoint 和策略身份。不得进入普通 base-config 路径。
2. 首次启用显式 relay 所需 pin 按第3节在有界 preparation 中建立，绑定既有已应用选择与完整 prospective rows，不能暗中应用更新的 proxy 草稿。check/apply 除 proxy revision 外还要绑定入口策略 fingerprint；节点变更后旧审阅令牌/候选摘要失效。
3. SQLite desired 行提交与私有 runtime 事务是两个提交域，不声称跨库全原子回滚。保存成功/apply 失败沿用明确 `saved:true, applied:false`；旧已提交 runtime 仅在可完整证明其原策略身份时恢复。新 desired 标为待应用，不能标新策略生效。恢复不能确认则停受影响核心，保留 journal；不转普通直连。
4. direct→relay、relay→direct、禁用/删除、全局开关、上游更换均是完整策略事务。失败/进程中断/磁盘满不得只切换一部分入口或保留未知 runtime。
5. 模式改变可能不改变核心配置 bytes，例如 global on 时 inherit→relay；仍须改变已应用策略身份，status/启动不能只比较 core_revision。启动同时核已保存 policy_identity 与明确版本的入口策略快照，保留旧 v1 全 inherit 身份。启动先恢复 proxy journal，再普通 core auto-start。损坏/null/未知 mode、缺上游、旧身份或 rows 不一致均 fail closed。新非 inherit / v2 已应用策略下，用户手动停止的核心不因模式保存/证书续期自动开启；旧全 inherit 普通 CRUD 的既有启动语义保持，显式 restart 仍可开启。
6. 当前或 pending 活跃中转所引用上游不得删除，即使双 flag 都 off；disabled draft 引用如何清理必须明确报错或解绑，不能偷偷改 direct。
7. 普通 restore 对照 committed 上下文的保护入口精确 ID/绑定集合，而不是 dirty DB、两个 flags 或数量；relay(A)→direct(A)+relay(B) 也拒绝。手动 stop 不清除保护意图。备份保留 mode 与所有关联元数据；旧无列备份仅通过明确旧 schema 迁移为 inherit。新格式恢复/回滚到不懂 mode 的旧程序须拒绝或提供明确兼容门槛，不能悄悄丢列并直出。专用 offline recovery 继续通过显式精确计划处理；installed smoke 增加混合模式及旧 reader 拒绝。

## 6. API、UI、导出

- 节点新建/编辑显示三个选项；默认 inherit。表格分别展示“已保存策略”和“已应用出口/待应用/不可确认”，不得从新 DB 值推断已运行结果。
- 全局面板显示默认开关语义、有效 relay 数量和因何需要上游；全局 off 不把 explicit relay 伪装成全停。
- 取消、刷新、重开、异步旧响应、API 409 和字段失配不得改已应用状态或残留旧导出。
- 普通分享/订阅、严格中转设备导出、D1 专用上游导出三用途分别回归。所选入口通过 ID 选择；不把 mode 当私有协议字段，不泄露海外 UUID/主机代理密码/服务端材料。
- 严格 relay exporter 改按**所选入口的已应用有效策略**判断，允许全局 off 的显式 relay，拒绝 direct；不能再要求全体 enabled rows 都是中转资格。runtime revision/identity 不匹配时拒绝。
- 普通 relay 节点导出的能力提示须与服务端 UDP reject 对应。普通 direct 旧导出字节语义保留。URI importer 可能自带 UDP 能力；不能只靠 `udp:false` 声称已在服务器禁用。
- 纯入口协议配置的导出不等于运行生效证明。含“已直连/已中转”标签或能力声明的输出必须依据核实的 active snapshot，dirty/unknown 时明确阻断相关声明。

## 7. 验收矩阵

- 旧库/新库/重复迁移/损坏列/备份恢复；新建省略、更新省略、null/非法类型、legacy API、无凭据变化。
- 全 inherit golden：配置、身份、四状态/12 切换旧门槛不退化。
- 三节点 inherit/direct/relay × host on/off × global on/off；单上游，只有 inherit 随 global 改变。
- 无上游首次 relay、disabled 草稿、enable、错资格、地址/DNS rebinding、删除活跃上游、改备注不改路线、重复 tag 和 reserved tag。
- apply/check 后并发行变更、过期 revision、幂等重放、每个事务故障点/进程中断/容量满、重启/备份恢复；保护判断与实际出口一致。
- 标准 **stock Mihomo** 使用实际产品导出的两个 VLESS 节点，在同一服务进程分别请求受控 HTTPS 目标：direct 来源为国内，relay 来源为海外；host 代理四组合独立。不能用手写 fixture 冒充产品导出。
- 上游错误 UUID/CA/SNI/停止：relay 真实拒绝原因 + 目标零送达 + 无国内直出；direct 正常不被拖入上游；修复后恢复。第一跳拒绝与上游拒绝分开。
- 有效 relay UDP 实际拒绝，普通 direct 原有测试保持。配置解析不代替数据转发。
- 浏览器创建/编辑/取消/刷新/再编辑、dirty/error/恢复、导出清除与权限；准确候选八组 CI 和同提交四目标消费，正常合并后准确 master 重验。

本契约的每入口 TCP 实现已在准确主线 `0962f0c4995b3b7606652027f2330285e4dea1f7` 收口；候选/主线八组、四目标与分层真实结果见[收口记录](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。UDP、手机、公网及正式发布仍按[交接状态](DEVELOPMENT_HANDOFF.md)独立待做。失败保留原 head/run/attempt，不缩短窗口、改成预期成功或用重跑覆盖失败；新提交仍须独立准确验收。

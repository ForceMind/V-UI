# 当前开发与接手入口

## 2026-10-23 当前独立产品目标：现有仪表盘窄屏可用

PR70 首次候选 `a031b5342fd639a51b1f3b57ff4d22a0b3f0a30d` 的原生 Test [run40031119855](https://github.com/ForceMind/V-UI/actions/runs/40031119855) 失败并完整保留，不重跑或改判。旧 CSS 对照已实测 CPU / Memory 卡体各横向溢出 106px、仪表超出内容区 126px；候选在合成 Unknown 展示阶段的页头文字检查超出约 2.936px，大于不变的 1px 界限。原日志没有具体视口/控件/transform，因此尚不能判定历史根因；后续门禁因该失败被跳过，不算通过。固定库 ElTag 的挂载缩放动画与 helper 的变换后矩形 / 未缩放内边距混算存在具体时序隐患，但仅凭这个机制不能解释为已证明原因。当前跟进只增添同次测量的阶段、视口、控件、矩形、transform / 动画诊断，保持原断言、阈值、顺序和生产 CSS，不以等待或重跑掩盖；新的准确 head 仍需完整独审和 CI。PR69 新主线 `6a6d3e46` 已于 22:09 UTC 独立完成八组 / 11 jobs / 126 steps 和四目标首次验收。

[PR69](https://github.com/ForceMind/V-UI/pull/69) 状态 Unknown 修复经独审、本地组合完整 2133 项（1918 通过 / 215 明确 skip）、准确候选 `8ed33ec446c4b03bf3a76ae7847957c7da58fa07` 的八组 / 11 jobs / 126 steps、四目标首次通过、最新完整 review 与 fresh 门禁后，于 21:52 UTC 正常合并至签名 master `6a6d3e46ebf7d64620b2842bd4dc560529f37c6f` / tree `2f76fcb18385631cd3410904b27e6a99ce5d43b7`。其自己的主线 CI 仍独立进行；准确候选的 Chromium 新场景是合成状态 GET 的真实组件验证，不等于真实宿主/核心资格。此前 PR68 主线 `52629539` 已于 21:51 UTC 完成自己的八组 / 四目标首次验收，历史 PR66 首次端口失败仍保留。

本次独立修复既有仪表盘的布局，不改变状态语义、API、控制器、菜单跳转、按钮、鉴权或轮询。静态审计发现固定 220px 侧栏、四个 25% 指标列与 126px 仪表，页头不换行且缺少对应响应式规则；现有 390px 测试只在仪表盘确认菜单挂载，防溢出断言实际覆盖的是 `/proxy` 或 `/workspace`，并不证明仪表盘可用。这是准确模板/固定库证据及原生覆盖缺口，尚不能声称已测得浏览器裁切。

最小生产修改限定 CSS：手机上现有导航与内容纵向排列，不新增抽屉或焦点/展开状态；指标明确适应一 / 二 / 四列，页头自然换行，保留桌面布局和原菜单语义。既有 Chromium gate 增加一个有界场景，以准确旧 CSS 的只读夹具取得具体几何反例，再加载候选样式；390 / 768 / 1440px 须核页面、页头、卡片内部及仪表完整可见，并验证既有导航与无额外写操作。正常场景采用真实鉴权只读 GET，长文字 / Unknown 则另标记为合成 GET 展示压力，不改实际账户或宿主。原生结果待准确 CI，不以静态宽度计算、缺库、超时或任意异常冒充红侧证据。

PR69 保持独立收口；本补丁已保存自有 stash，并从冻结候选快进至真实合并主线 `6a6d3e46ebf7d64620b2842bd4dc560529f37c6f`，再原样应用；基线 tree 相同，四份实现/夹具文件逐字节未变。新组合树仍须核独审、完整普通套件、准确候选八组 / 四目标 / 最新 review，正常合并后再独立验主线。旧 CSS 仅是测试对照，不改变运行包的固定前端资产；本地已知浏览器限制不重试、不绕过，不扩称手机实机/触控/移动线路，不改 VERSION、核心或工作流，不触碰用户 Mac、真实数据/凭据、安全网络设置、Tailscale/私有监听、受限 UDP D2、发布或部署。下方状态按日期保留，不表示持续开发暂停。

## 2026-10-23 当前独立产品纠错：仪表盘未知状态不显示旧绿灯

[PR67](https://github.com/ForceMind/V-UI/pull/67) 已正常合并至签名 master `b3ef9efe5d4079e67c8263a219a291fdce088ac8` / tree `f2b88e8c07351543746918e492eec40a8da6f5af`。准确候选和自己的主线各八组 / 11 jobs / 126 steps、四目标全部首次通过，主线于 21:05 UTC 独立收口；真实登录/API/临时 SQLite 的实例冲突浏览器通过，隔离缺核心环境仍诚实区分保存与应用。PR66 主线已独立收口，其 loopback 首次 DNS 端口占用失败与唯一 attempt 2 成功分别保留；不将历史占用者或时序当作已知。

本次是[既有可见状态轮询](LOW_RESOURCE_TARGET.md#69983e9-可见状态轮询实际结果)上的实际显示纠错，不是继续增加无产品变更的测试。未改控制器/模板与固定 Vue 已复现：首次未获回复也显示 Stopped/Missing/0%，成功后读取失败仍保留绿色 Running 和旧指标。最小修复独立记录系统/核心读取是否已确认；首次或失败显示 Unknown，隐藏旧指标，成功 false/0 仍如实显示，缺失或畸形字段不猜测。普通在途刷新保留上次已确认显示，不新增轮询、不改变 3秒/10秒、5000ms、单飞/显隐和会话规则，不改 API 或操作行为。

八项真实 Vue/模板与实际控制器的确定性控制取得原代码全红、新实现全绿；HTTP/时钟/renderer host 是双件，四个有限破坏对照均失败。既有 Chromium gate 新增真实组件加合成 GET 回复的场景，要求初始未知、两个端点独立失败/恢复、零值/false 和无写操作；不称真实宿主指标或核心运行资格。本地已知原生浏览器限制不重试，完整普通套件、独审、准确候选与新主线 CI / 四目标、最新 review 都须分别通过；尚未通过的门禁不提前登记。

[PR68](https://github.com/ForceMind/V-UI/pull/68) 仅修 strict-export DNS 测试端口预检、零生产行为变更，其准确候选 `d31786e77d4a78208a938b222f8a06594651982a` 已完成八组 / 11 jobs / 126 steps、四目标首次通过与最新 review，无重跑后于 21:21 UTC 正常合并至签名 master `52629539edd2b11d10274fa3cd68ca64945b088d` / tree `48716726e9e19a75e27d3a29858df931941049c7`。该新主线自己的 CI 仍独立待验；本产品候选已保留自有 stash、快进到实际新主线并重新应用，仅交接冲突且保留两侧，六份产品/测试文件逐字节保持。审后旧基线完整 2128 项（1913 通过 / 215 skip，187.957 秒）仅是旧树证据；组合新树须独审和重跑完整套件，不借旧绿。不改 VERSION、固定资产或工作流，不触碰真实数据/凭据、Mac、Tailscale/私有监听、网络安全设置、受限 UDP D2、发布或部署。下方历史状态按日期保留，不表示持续开发暂停。

## 2026-10-23 独立测试修正：严格导出客户端的 DNS 端口选择

[PR66](https://github.com/ForceMind/V-UI/pull/66) 已经准确候选验收并正常合并至 `0f4c9bb714dfb9c04ef4c70b80ff4752925c9beb`。该主线的 [loopback attempt 1](https://github.com/ForceMind/V-UI/actions/runs/40017305506/attempts/1) 在既有 strict-export 夹具启动 sing-box 时失败：DNS TCP `127.0.0.1:56973` 已绑定，随后同端口 UDP 返回 `EADDRINUSE`，尚未进入上游失败 / 无 fallback 断言。历史占用者及发生时序未知；原失败保留，唯一相同 SHA 的 loopback attempt 2 完整 220 项成功，其余七组首次成功，主线于 20:41 UTC 独立完成八组 / 11 jobs / 126 steps 和四目标。不把重跑成功解释为永久修复。随后 [PR67](https://github.com/ForceMind/V-UI/pull/67) 节点实例条件候选 `4e7803f49d52888d7d92ecfc99d37ef0a4b8de1c` 经独审、完整本地 2126 项、准确八组 / 四目标 / 最新 review 首次通过后，正常合并至 `b3ef9efe5d4079e67c8263a219a291fdce088ac8` / tree `f2b88e8c07351543746918e492eec40a8da6f5af`；其新主线仍独立待验。本测试修正从该实际新主线继续，不借同树或旧候选资格。

源码和确定性 socket 模型证实原 `exported_client` 用已释放的 TCP-only `bind(0)` 结果为双协议 DNS 取号，无法检查 UDP 占用，且该号码仍在 Linux 自动分配区间内。本候选仅将这一个 DNS 取号点替换为 PR56 / PR58 已有的 `local_proxy_port`，普通 mixed 端口和所有启动、TLS、DNS、无 DIRECT / fallback 及精确 listener 断言原样保留；不修改共享分配器、产品、内核参数、核心 pin 或工作流。

分配器只读自动端口区间和非特权下限，在区间之外以原有最多 128 个不重复候选调用未改动的 TCP / UDP × IPv4 / IPv6 占用检查，且没有已有进程所有权豁免。取号发生在生成子进程配置 / parser 检查与启动之前，错误继续原样传播；不在启动失败后重选或重试。它不是原子预留：显式竞争者可在探测结束后占用，两个并行分配者也可能同时选中，固定端口恢复仍可能失败。这些失败必须保持可见，普通 mixed 端口自己的既有竞争亦不在本次范围。

最初 `0f4c9bb` 基线的冻结 tree `5cb7fd142665dcf76a6b806f97ed98328d5924db` 已完成独审和完整本地 2107 项（1894 通过 / 213 skip，203.077 秒），日志 SHA-256 `b94e9a625cf0806d6befa7ff97b5742aefcc91a8953c8c12fe90b94ca17c5b53`。纳入真实 PR67 主线时保留自己的 stash，仅两份文档冲突并完整保留两侧内容；两份测试文件逐字节保持。新的组合树须重新独审、完整普通套件和准确 CI，旧树通过不代替新树。

新增有限控制直接执行实际 `exported_client`，仅替换进程、socket 与系统观察边界，验证两种格式和错误 DNS 身份、配置落点与一次分配顺序、已占 UDP / 前一个客户端、不相交的自动端口区间、分配 / parser / 启动失败无重试，以及缺少任一 DNS 协议或错误监听地址必须失败。这些是确定性夹具控制，不是固定核心或网络验收；真实 strict-export、完整八组 / 四目标及准确合并主线仍须各自通过。开发云端既有 Chromium / netlink 权限限制不重试、不绕过；不恢复受限 UDP D2，不发布、部署或改变真实网络。

## 2026-10-23 当前独立纠错：陈旧编辑/删除的实例意图

[PR65](https://github.com/ForceMind/V-UI/pull/65) 已正常合并至 `b6a4ed5cc8d8b8dce771cbdb59e134d48b7c3855` / tree `7eecd1d6305f6d5a5a2c75c54fe5899653cc6c51`，准确候选与自己的主线各八组 / 11 jobs / 126 steps、四目标全部首次通过，主线于 20:06 UTC 独立收口。此前 [PR64](https://github.com/ForceMind/V-UI/pull/64) 的 `efbbdbe5` 主线也已独立通过；历史初始失败与旧候选终态仍保留，不继承绿灯。

[PR66](https://github.com/ForceMind/V-UI/pull/66) 六处保存禁用修复经独审、本地 2102 项（1889 通过 / 213 skip）、准确候选 `6b8e9437ce8911610edc89c9eb2750bf672152e5` 的八组 / 11 jobs / 126 steps、四目标、最新完整 review 和 fresh 合并门禁，于 20:11 UTC 正常合并至 `0f4c9bb714dfb9c04ef4c70b80ff4752925c9beb` / tree `e792cdad6c0f29859055eb7bd3ce25baacc6a1eb`。自己的主线 CI 尚独立进行；实际 Chromium 控件场景通过，但其延迟 422/200 回复是合成的，不称新增 API/核心应用资格。

本次从该实际新主线独立修复旧编辑器/删除确认仅凭整数 ID 作用于自然删建替代实例的问题。未改原控制器 + 真实 API 函数/临时 SQLite 已复现错误修改和删除，未使用真实数据，不称发生实际泄露。新增同一行/编辑快照的非秘密实例摘要、严格条件 PUT 和不回退的专用 guarded-delete POST；比较、校验和 DB 写入同一 `BEGIN IMMEDIATE`，拒绝早于证书准备和核心应用。旧 API 省略条件仍兼容且明确不受保护，新 UI 对旧后端失败关闭。详见[实例条件 API 与兼容边界](API.md#已有节点的实例条件2026-10-23-修正候选)。

独审要求保留调用者未提交事务、提交前冻结结果，不以提交后的 ORM 刷新读取替代节点。既有证书/runtime 提交后边界不变，不声称整个流程跨进程原子化；失败保留草稿，明确刷新重开/重新确认，不能自动采用替代实例。本候选须组合树完整普通套件、独立实现审查、准确候选八组/四目标/最新 review 和正常合并后的独立主线验收。PR66 新主线也须先单独收口。没有 DB 迁移、真实数据/凭据、权限、私有监听、Tailscale、Mac、UDP D2、发布或部署操作。下方按日期保留历史，不表示持续开发暂停。

## 2026-10-23 当前独立候选：节点编辑器保存期间锁定所有已有控件

[PR64](https://github.com/ForceMind/V-UI/pull/64) 的准确 master `efbbdbe5fe7ba338eaa70083c8844605c53cc50f` 已于 19:38:49 UTC 独立完成八组 / 11 jobs / 126 steps 及四目标，全部首次成功。随后 [PR65](https://github.com/ForceMind/V-UI/pull/65) 组合候选 `8cfe202b04b4403bb143b10231ce593beab19aa9` / tree `7eecd1d6305f6d5a5a2c75c54fe5899653cc6c51` 经独审、冻结本地 2100 项、准确八组 / 11 jobs / 126 steps 与四目标、真实陈旧页面条件创建浏览器及最新完整 review 无发现后，正常合并至 `b6a4ed5cc8d8b8dce771cbdb59e134d48b7c3855`。该新主线 CI 仍独立进行，不能借候选同树资格；旧 `1b615767` 已全终态通过并独立留存，不替代组合验收。

本候选从真实新 master `b6a4ed5cc8d8b8dce771cbdb59e134d48b7c3855` 独立修复六个节点控件。固定 Element Plus 2.14.6 的 `useFormDisabled` 将子控件显式 `false` 置于父表单 disabled 之前；原核心、协议、传输、安全、Flow 与指纹控件在保存等待时仍可改选，迟到成功会关闭表单，丢掉请求后未提交的选择。真实固定库 helper 与未改 `app.js` 的合成延迟 PUT 已复现，不称用户数据丢失或真实浏览器证据。

最小产品修复仅在六处原 disabled 资格表达式前加入 `savingInbound ||`，完整保留编辑身份锁、REALITY 与强制 TLS 限制，不改控制器/API/秘密/库版本。等待时不可改选，拒绝后保留原草稿并恢复原资格，成功关闭行为不变。有限 Node 控制使用真实固定 Vue/Element Plus helper、当前模板与实际控制器；既有 Chromium gate 以明确模拟的保存回复检验真实组件 create/edit、422/成功等待与解锁，不把模拟回复称运行应用证据。

最初 d4bf 基线的冻结 tree `04b76f4ef00d26f66dbb03107f975ea6365874cd` 本地 2085 项为 1872 通过 / 213 skip（190.616 秒），仅是该旧树结果。独审发现浏览器错误提示重名与对话框隐藏早于保存 finally 两项夹具时序，已改为按 create/edit 唯一定位错误，以及等待保留原生控件恢复 enabled；不改产品行为或降低断言。后续已审补丁在新基线的五个非交接文件逐字节保持，仅交接协调；新组合树仍须自己的独审、全套与准确 CI / 四目标 / 最新 review。候选 CI 可与 b6a4ed5 主线 CI 并行，但正常合并须两者各自通过，合并后的 master 再另验。

不新增测试范围或私有监听实现，不改 VERSION、固定核心或工作流，不触碰实际数据库、凭据、Mac、Tailscale/VPN、网络安全设置、受限 UDP D2、发布部署或费用。下方历史状态与失败记录全部保留。

## 2026-10-23 当前组合候选：节点选择条件纳入证书修复主线

[PR64](https://github.com/ForceMind/V-UI/pull/64) 准确候选 `4bc6febedc5e2f99dcaafed1bece4fce474c767c` / tree `2d15b38b32152b4308d1013b659ab42edc5fdcff` 经冻结本地 2084 项（1872 通过 / 212 skip）、独审、准确八组 / 11 jobs / 126 steps 与四目标全部首次通过、最新完整 review 无发现及合并前 fresh 门禁后，已正常合并至 `efbbdbe5fe7ba338eaa70083c8844605c53cc50f`。真实 ACME 和编辑器的两处 CSP 等待器故障已在新 head 转绿；初始 `96de501` 两项失败及全部终态仍保留，未改判或取消。准确新 master 的 CI 正独立进行，不能继承同树候选资格。

[PR65](https://github.com/ForceMind/V-UI/pull/65) 原独立节点条件候选 `1b615767e3db39db2d8716da005bb5f0ab268eb7` / tree `d5908d27d72c51cc45ca8f701dd9b1031fbf2c9b` 已完成本地 2099 项（1887 通过 / 212 skip）、独立安全审查及该 head 的完整 review 无发现；其原 CI 尚待终态，完整留存，不用新头掩盖。现在正常合入上述真实主线，保留双亲历史，仅本交接文档冲突。节点功能各文件保持原候选字节，证书各文件保持主线字节；组合树须重新独审、本地全套、准确候选 CI / 四目标及最新完整 review，通过后才可正常合并。两项独立功能不混写资格，后续新主线还须另验。

## 2026-10-23 独立产品候选：陈旧页面创建订阅的节点实例条件

从准确已验主线 `d4bf74c80c56450777d3c6649593bc5ef2b96758` 独立修复新授权的陈旧 UI 选择，不与 [PR64 证书刷新](https://github.com/ForceMind/V-UI/pull/64) 混提交。PR63 只完成私有管理设计，不代表其他批准产品开发暂停；真实 Tailnet、监听与权限决定保持独立。

已隔离复现旧页面显示旧节点、另一页自然删除重建同整数 ID 后，新创建却绑定替代实例。PR60 旧令牌防线仍有效，不称实际泄露。最小修复为同批节点读取的全库存实例修订，以及现有创建 JSON 的严格可选条件，在 SQLite 创建事务内、token 生成前核验；新版工作区必传，冲突后明确刷新和重选，不自动重试。普通 API 省略条件仍为旧当前-ID 语义，升级前已打开页面需重载，不能泛称所有客户端均强制条件。详见[安全与兼容契约](SUBSCRIPTION_IDENTITY.md#2026-10-23-新授权的陈旧页面选择保护候选)。

本次正常代码、隔离测试及最小文档须独立安全复审、完整普通套件、准确候选 CI / 四目标和最新 review，再按届时主线正常合入与重新验收；PR64 或其他提交的绿灯不可继承。没有数据库迁移、新凭据、权限、真实网络、私有监听、Tailscale、Mac、受限 UDP D2、发布或部署操作。下方设计阶段文字保留历史，不覆盖持续开发范围。

## 2026-10-23 当前有界目标：证书页面刷新不丢选择、不回写旧状态

[PR63](https://github.com/ForceMind/V-UI/pull/63) 的 V-TS-03 设计已正常合并至 `d4bf74c80c56450777d3c6649593bc5ef2b96758` / tree `c34e8f0f1b9478b77af66c22be71e8944bc7d421`，候选与主线各自八组 / 11 jobs / 126 steps 和同源四目标全部首次通过；主线于 2026-10-22 06:40:04 UTC 独立收口，见 [Test](https://github.com/ForceMind/V-UI/actions/runs/39733219344)、[ACME](https://github.com/ForceMind/V-UI/actions/runs/39733219512)、[loopback](https://github.com/ForceMind/V-UI/actions/runs/39733219321)、[deployment](https://github.com/ForceMind/V-UI/actions/runs/39733219304) 与 [portable](https://github.com/ForceMind/V-UI/actions/runs/39733219299)。这只是设计交付，不授权私有监听实现，也不表示整个批准开发计划暂停。

本次从该主线独立修复既有图形化证书管理：自动刷新清空绑定节点选择，后台旧结果可覆盖较新操作状态。保留明确操作及当前选择、后台轮询单飞、旧结果失效，不改变任何后端 API、CA、凭据、信任、绑定或续期策略。复现、预期行为与分层验收见[证书页面刷新](CERTIFICATES.md#证书页面刷新与待选节点2026-10-23-修正候选)。新候选须独审、完整普通套件、准确 CI / 四目标及 review 后正常合并，再核准确主线，不能借用 PR63 绿灯。

不改 VERSION、固定核心或工作流门槛；不操作真实 CA、用户数据库、Mac、Tailscale/VPN、私有监听、安全设置、受限 UDP D2、发布或部署。私有管理的订阅可达性、目标环境、维护与权限决定继续独立保留，不妨碍本次无关产品纠错。下方阶段文字保留历史。

首候选 `96de5010cd0e1bf5c57952b11f3c9f39bfbf2e95` 的 [ACME 首次运行](https://github.com/ForceMind/V-UI/actions/runs/40005990827) 失败：新浏览器夹具的 `wait_for_function` 在严格 `script-src 'self'` 页面内部使用 eval，尚未进入旧响应断言。核对固定 Playwright 1.57 源码后，改为测试专用 DOM 完成标记及有界 locator 等待，保留真实响应、瞬时回写观察及全部断言；不放松 CSP、不改安全选项。原失败不改判，后续准确提交仍须重新完成全部门槛。

## 2026-10-22 当前有界目标：V-TS-03 私有管理设计，不实施

[PR62](https://github.com/ForceMind/V-UI/pull/62) 的五类 SAVE 回归已正常合并至准确 master `6b22fb7687b1c26b2af8cf166812aba8786dfd33` / tree `1d76efb8643f2c3864bfa6fa4154571099340994`。候选与新主线各自八组 / 11 jobs / 126 steps 和同源四目标均首次通过，主线于 05:44:18 UTC 独立收口；[Test](https://github.com/ForceMind/V-UI/actions/runs/39727941401)、[loopback](https://github.com/ForceMind/V-UI/actions/runs/39727941337)、[deployment](https://github.com/ForceMind/V-UI/actions/runs/39727941355)、[portable](https://github.com/ForceMind/V-UI/actions/runs/39727941391)。下方待验文字保留历史，不继续扩大测试。

下一阶段仅交付 [V-TS-03 私有管理入口设计](TAILSCALE_PRIVATE_MANAGEMENT_DESIGN.md)，补整 HTTPS 监听包含订阅的取舍、现有回环健康/安装升级/服务就绪缺口、面板与核心停启耦合、故障不回公网和带外恢复验收；不虚构已存在的 private-mode 开关。V-TS-02 软件已由 PR54/55 完成，但真实 Tailscale、C0–C8/R0–R4 与新增 P0–P9 均不因此获资格。

本轮只改设计及最小交叉引用/状态，不改源代码、测试、工作流、固定核心、VERSION、监听或系统配置，不安装/运行 Tailscale，不创建凭据或授予权限，不执行网络实验、真实数据迁移、发布部署或受限 UDP D2。文档须独审及准确提交 CI / review 门槛；设计交付后明确尚待范围/目标/维护与权限决定，不自动展开实现。

## 2026-10-22 当前有界目标：保存确认与恢复的五项回归覆盖

[PR61](https://github.com/ForceMind/V-UI/pull/61) 准确候选 `eb0712a49bedebb4c4964db3a726a1bb8018a774` 经独审与候选验收后，于 04:30:35 UTC 正常合并至 `76b0f5ca4459dcfef96cd521f3e6e57063a05983` / tree `b612bb44e5c8556e3edf8aece82a37430680ec04`。该准确主线已于 04:51:04 UTC 独立完成八组 / 11 jobs / 126 steps，全部 attempt 1 success，以及同源四目标；不是沿用候选绿灯。公开记录见 [Test](https://github.com/ForceMind/V-UI/actions/runs/39722773294)、[loopback](https://github.com/ForceMind/V-UI/actions/runs/39722773352)、[deployment](https://github.com/ForceMind/V-UI/actions/runs/39722773274)、[portable](https://github.com/ForceMind/V-UI/actions/runs/39722773328)。下方候选、待验和全部失败记录保留为历史，不覆盖本次主线收口。

本次从该准确主线独立增加五项保存确认/恢复回归，不修产品行为：真实 422 后纠正保存、第二写入者 409 后显式重载、已落盘但确认不可解析、确认后预览失败、保存后预览等待中的新编辑。直接运行未改动的 `workspace.js`，通过每场景一个有界 Python 进程调用实际模型、路由函数、验证器与临时磁盘存储；复用既有小型 DOM 夹具，原 24 个重载用例正文及 40/35 个新地址/实例状态控制不变。详见[测试边界](WORKSPACE_ALPHA6.md#2026-10-22-保存确认与恢复回归覆盖)。这不是实际 HTTP、认证、浏览器、导航或核心资格。

本轮仅测试/夹具和最小合同/交接文档，仍须冻结树独审、完整普通套件、准确候选 CI / 四目标、正常合并及准确主线独立验收，不预填未来通过结果。完成这五项后停止扩大测试，交付剩余计划清单；不继续滚动新增低价值测试 PR。未发布、部署、修改 VERSION/固定核心/工作流或依赖，不操作实际用户数据、Mac、Tailscale/VPN、凭据、网络安全权限或受限 UDP D2。

## 2026-10-22 当前独立候选：工作区新地址生命周期，基线已纳入 PR57

本次准确基线为 `9e439d3b3872a52e1177390e470f8101984be01c` / tree `c605063d936edb0801b005d62f345360fbee9b58`。[PR57](https://github.com/ForceMind/V-UI/pull/57) 的准确候选 `f44258f54a4c2c915a1ff7f49d80f06d525cb20c` 已完成独立审查、完整本地套件、八组 / 11 jobs / 126 steps、同源四目标和真实浏览器验收，最新完整复审无发现，于 04:01:43 UTC 正常合并至该主线；新主线自己的完整 CI / 四目标仍独立进行，不能继承同树候选资格。[PR60](https://github.com/ForceMind/V-UI/pull/60) 的准确主线 `9e77ed0137c9b6a2864b74048c28af349aafc6e1` 已于 03:49:56 UTC 独立完成八组 / 11 jobs / 126 steps 及同源四目标。下方“主线待验”“未合并”等文字保留为各阶段历史，不覆盖本节更新。

新地址修正只解决创建/轮换已显示 URL 后、列表等待期间 Hide 被 busy 禁用的实际缺陷，并对迟到签发、下载 fetch/blob、复制反馈增加独立内容代次。隐藏/pagehide/401 清空本地内容后，旧完成不得恢复显示或发起新下载；请求先隐藏旧内容再记录自身代次，正常新结果不受误伤。隐藏不撤销已授权的服务端创建/轮换，不增加重试；已发出的剪贴板写入和已经开始的下载无法召回。后端 API、If-Match、订阅实例绑定和普通明文 DOM / 无存储约束保持。详见[最小合同与分层证据](WORKSPACE_ALPHA6.md#2026-10-22-新地址的本地生命周期修正候选)。

原独立本地候选基于 9e77ed0，冻结 tree `0cd2f617791635373d5f8b84cc274b360d55b223`。其五个非交接文件在本次原字节移入，已核对它们的基线内容在 9e77ed0 与 9e439d3 之间未改变；只在本交接文档追加并协调当前状态，不修改 PR57 分享代码或 PR60 身份代码，不把别的分支提交混入修复。工作区合同中的 9e77 / “不含 PR57”描述指原候选的证据基线；当前候选则明确以已合并 PR57 的 9e439d3 为父基线，必须取得新组合树自己的完整验收。

原树 40 个新控制在基线为 24 失败 / 16 通过，修复后全部通过；10 种变异被检出，既有重载 24 项、实例绑定 UI 35 项及原 10 项审计控制通过。原树首次普通全套为 2063 项、1857 通过 / 205 skip / 1 failure，245.723 秒；唯一失败是既有 PTY 菜单包路径预确认的 Ctrl-C 子场景，60 秒内没有退出，原因尚未确定。原日志 SHA-256 `f5b4a8d4c11d5408aa692f3d8e2dfb7e22c5de6d7298802b5d78a93fcd3df878`。随后同树同方法、原六个子场景和 60 秒断言原样诊断一次通过，0.499 秒，日志 SHA-256 `cd0937785488bc126fc8e7c944aa83b82a2d28d1befc35ee98c1862232dbfa8e`。经协调，原树完整 attempt 2 为 2063 项、1858 通过 / 205 skip、185.033 秒，日志 SHA-256 `4871175b502e9a81a16c84e82b1d05b4f19b3feb795e7eaecf7a957d1fbaec55`。前后全部受控文件 manifest 与冻结树不变；未改源码、选择范围、超时或断言，首轮失败不改判，后续成功不证明 PTY 根因已修或永久稳定。

本次新组合树冻结后须另跑一次完整普通套件，原树成功不能替代；准确结果与树及日志绑定记录。普通运行只使用固定前端资产与临时数据目录，真实 core/browser 专项不启用。既有 Chromium gate 已增加创建/轮换等待真实列表 GET 时的普通 Hide 点击场景，不挂起页面导航、不伪造成功 API；实际浏览器仍需新准确提交 CI。受控 pagehide 测试不证明真实导航或 bfcache 行为。候选继续独立审查 / 准确 CI / 四目标 / 正常合并门槛，无发布、部署、真实 Tailscale、Mac、网络敏感权限或受限 UDP D2 操作。

## 2026-10-22 当前目标：PR57 纳入订阅隔离修复主线后独立重验

[PR60](https://github.com/ForceMind/V-UI/pull/60) 的安全修复已正常合并至 `9e77ed0137c9b6a2864b74048c28af349aafc6e1`，tree `f8747314f0dc190bc72ab5bfda630065834bdb79`。PR57 从 `a1d633be036cfa578907ada966deacf0cd018274` 正常合入该真实新主线，保留双亲历史，不强推；仅本交接文档冲突，分享代码及测试保持 a1d 原字节，订阅绑定、工作区和迁移等内容保持 PR60 主线原字节。新组合树须独立审查、完整本地套件、准确新 head 的八组 CI / 四目标、真实浏览器与最新完整复审后才允许正常合并；9e77ed0 自身主线 CI 另行验收，不借同树候选绿灯。

a1d 候选 clean HEAD 本地 2041 项（1829 通过、212 显式跳过）、八组 / 11 jobs / 126 steps 与四目标均首次成功；真实分享 / D1 Chromium 20 项（含即时 Escape 取消及会话失效）全部通过。该 head 在 02:47 UTC 因真实 PR59 基线变化请求的新复审，截至 03:25 UTC 摘要仍为 running，尚无终态，不能称通过或取消。本次因 PR60 主线再推进而重验，不是重复空触发；旧 0649 Ready 复审与 a1d 复审的未完成记录保留，不能借 earlier completed review 绕过最新门禁。旧 cdf 已 cancelled 的确切终态及历史失败继续保留在下文。

## 2026-10-22 已合并、准确主线独立待验：订阅节点实例绑定

[PR60](https://github.com/ForceMind/V-UI/pull/60) 候选 `60d599fa3dcbe1c0fe0d652e11b68e9ce8fb7caf` / tree `f8747314f0dc190bc72ab5bfda630065834bdb79` 已完成重点独立安全审查、clean HEAD 本地 2062 项（1857 通过、205 显式跳过）、八组 / 11 jobs / 126 steps 全部首次成功及同源四目标。Ready 触发的准确 head 复审于 03:19 UTC 完成，无发现和未解决项；合并前最新 comments / reviews / inline / threads 全量核验无阻断，于 03:31 UTC 正常合并至上述 9e77ed0，同树但主线资格仍单独进行。

[订阅节点实例绑定契约](SUBSCRIPTION_IDENTITY.md)修复整数 ID 自然复用后旧令牌读取新节点的授权缺陷：私有稳定实例身份与完整授权集合共同验证，读取和轮换均失败关闭，不禁止 SQLite 复用 ID。旧授权缺少历史实例证明，升级后必须明确重新创建并更新客户端 URL，管理 API / UI 有对应原因，不自动补绑定。完整恢复保留身份记录但继续撤销会话 / 订阅访问；旧 reader 不读取新增字段，不能声称安全降级。修复不能撤回已下载配置或自动轮换节点协议凭据；本轮只有合成复现，没有实际用户泄露证据。

初始化预检查、建表和迁移在一个 SQLite 事务内，独立订阅 schema 标记保护当前库缺表 / 缺列，原 policy schema、RECOVERY_REQUIRED 和恢复防线不变。独审 38 项安全 / 迁移控制和 7 种安全变异通过；准确候选真实 workspace Chromium 追加的旧授权无绑定及自然 API 删除 / 重建两场景通过，验证三格式 GET/HEAD 拒绝、轮换 409 且授权不变、正确禁用状态、新明确授权和无关授权正常。缺失核心的合成 CRUD 仍准确报告 saved:true / applied:false，不伪称应用成功。原真实核心、loopback 220 项、D1 四严格组合和非 root HTTPS / 恢复门槛均通过。未迁移实际用户库、发布或部署；Tailscale、受限 UDP D2 和真实环境资格边界保持。

## 2026-10-22 前次组合记录：PR57 纳入 PR59 主线（历史）

[PR59](https://github.com/ForceMind/V-UI/pull/59) 的独立工作区修正已正常合并，故 PR57 在原 head `0649a14b0bb5d7d38a47b5f8488b3ed4c910ce8f` 上正常合入 `fc9796523c0ed30f47b0fe14ba4b319bbdda74d7`，保留双亲历史，不强推。实际合并仅本交接文档冲突；分享产品代码与工作区产品代码及测试彼此独立，分别保留原内容。新组合树、准确新 head、完整本地/真实 CI/四目标与最新独审重新验收，不拼接两个旧 head 的绿灯；不是空触发旧复审。fc979652 自身主线验收仍单独进行。

旧 0649 候选已完成独审、本地 2040 项（1828 通过、212 显式跳过）以及八组 / 11 jobs / 126 steps、同源四目标首次成功；真正 Chromium 的原即时 Escape aborted 断言与会话结束场景通过。20:39 手动复审回执无 findings，但 20:55 Ready 后的另一次复审直到基线更新仍只有 eyes、无终态，不能称该次完成。本次基线变更后的准确新 head 须以自己的最新完整复审为合并门槛，旧未完成记录保留，不冒称取消或通过。下方旧草稿与失败文字为历史，不覆盖本节。

旧 cdf [run 39655236236](https://github.com/ForceMind/V-UI/actions/runs/39655236236) 已于 2026-10-22 00:43:56 UTC 结束为 cancelled，浏览器步骤 cancelled、后续 editor skipped。最后测试信号是 18:49:48 的 session 子场景 ERROR，直到取消仍无 traceback；日志仅称操作取消，不能仅凭约六小时推断具体原因，我方未发取消。终态片段 SHA-256 `906d4e8c77e0543c320035cff5966f6cb704144ee4c02499d9b5174988734906`。下方“检查时仍运行”保留当时观察，不再是当前状态。

## 2026-10-22 已合并并完成主线验收：工作区重载保护新草稿

本候选独立基于 [PR58](https://github.com/ForceMind/V-UI/pull/58) 已验 master `2ce795ddfb1d40520a029e40f518881a39a9a161`，tree `6e1d3b60d208ee2a8effb84b6ecc8be89460e477`。该主线八组 / 11 jobs / 126 steps 已通过；one-click 首次 GitHub 403 后原样 attempt 2 成功，其余七组首次成功，原失败不改判。基线结果不授本候选资格。

实际缺陷是“重新载入”期间仍可编辑的工作区表单被迟到 snapshot 覆盖，随后被误标为已保存。现以编辑代次保护 snapshot 与已保存 preview 两个 await 边界，仅在两次读取成功且无新编辑时一起提交页面状态。确认放弃仅针对点击时已有草稿；请求后新输入与原保存修订保留，用户须再次明确载入或保存。无自动 PUT、无冲突重试，不改变 If-Match、订阅或后端 API。详见[工作区合同及验收边界](WORKSPACE_ALPHA6.md#重新载入时保留新输入2026-10-21-独立修正)。

24 项 Node 控制直接执行产品脚本，基线 13 项失败、修复后全通过；完整本地套件在五文件原树 `42d14e0dbf16530e336a955a36b5bb7e8247f45d` 为 2022 tests / 1817 passed / 205 skips，日志 SHA-256 `022c2357ba657236b8cc09906eccf8d6f6e606a39c16fa1bff5080883a8a49e0`。独立审查和五种变异控制已完成。最终准确候选 `2c0113250829110071672d715dd269ac80328c03` / tree `88e020540208c3fddcd85079e1f52ea0af1d990e` 在 clean HEAD 完整运行 2022 项，1817 通过、205 显式跳过，179.533 秒，日志 SHA-256 `16af1f0e33c5a08a24009e0c4378eef0898b6a01c2d96a748490b42932c10bb0`。候选八组 / 11 jobs / 126 steps 和同源四目标首次成功，真实 Chromium 的四组延迟真实 API 响应场景全部通过；Ready 复审完成无 findings，合并前完整 review/inline/threads 再核为空。PR59 已正常合并至相同 tree 的 `fc9796523c0ed30f47b0fe14ba4b319bbdda74d7`，该准确主线已独立完成八组 / 11 jobs / 126 steps 全部首次成功及同源四目标；真实工作区四场景、loopback 220 项与非 root HTTPS / 恢复均通过，完整记录在 PR59。不能以本地 Node 控制或 skip 代替 Chromium。开发云端既有 Chromium/netlink EPERM 限制未通过改权限绕过。

本修复只改独立的 `workspace.js` 与对应测试/文档，不含 [PR57](https://github.com/ForceMind/V-UI/pull/57) 分享对话框代码。PR57 的准确 head `0649a14b0bb5d7d38a47b5f8488b3ed4c910ce8f` 候选 CI 通过，但 Ready 后复审仍缺终态，保持未合并；本 PR 不代其通过或跳过门禁。PR57 旧 [cdf run](https://github.com/ForceMind/V-UI/actions/runs/39655236236) 于 2026-10-22 00:43:56 UTC 结束为 cancelled，浏览器 session 子场景已有 ERROR，后续编辑器 skipped；日志只确认取消，原因未能证实，我方未发取消。两候选仅交接文档有重叠，若主线推进须重新协调文档并在合并前验证准确差异、独审及全 CI，不能借旧绿。未发布、未部署，Tailscale 与受限 UDP D2 边界保持。

## 2026-10-21 当前目标：普通节点分享窗口生命周期修正

在 PR56 的端口验收修正之后，独立推进既有普通“分享节点”窗口的真实缺陷，不新增协议：并发 A/B 请求的旧响应会覆盖新选择；关闭、页面离开和会话结束未清空 URI / 二维码，迟到成功还能重新打开窗口。确定性执行原 `app.js` 已重现这些控制器问题；不是认证绕过或真实用户凭据泄漏的证明。

修正保持原 GET 端点、普通分享 URI 字节和能力说明不变。每次选择先失效旧请求，待加载窗口可立即关闭；请求代次、AbortController、Vue 挂载边界和可取消绘制定时器共同防止迟到响应/错误/二维码复活。关闭、导航、列表刷新、pagehide/popstate、401 清理和会话结束同步清空状态、只读文本框、二维码子节点及 qrcodejs 写入的 `title`。关闭或失效会话禁止新的复制，旧复制结果不得更新提示；已交给浏览器的剪贴板写入不能撤回，也不宣称清除了用户剪贴板。

验收含合成 I/O 控制器、固定版本真实 Vue 与 Chromium 的隔离双节点流程，分别覆盖乱序返回、真实 HTTP 取消、关闭按钮 / Escape / 遮罩、列表/导航/历史/会话清理、QR 属性残留和剪贴板拒绝。浏览器节点与口令均为合成数据，剪贴板用受控边界，不代表真实凭据、OS 剪贴板权限、移动网络或转发资格。助手云端 Chromium 启动在断言前因 `socket() Operation not permitted` / SIGABRT 失败，保留失败，不改权限或启动绕过参数；真实 Chromium 结果须由新准确提交 CI 独立提供。

PR57 首个候选 `cdf128f` 的浏览器组出现异常长等待，检查时仍在运行，不能记为通过或已取消。已独立证明一个测试顺序缺陷：挂起登录导航检查旧 DOM 时，Playwright locator 断言会先等待该导航；此处改为直接检查旧控制器和全部 DOM 秘密，普通关闭仍保留可见性断言。该缺陷通常只有 5 秒断言超时，不能单独解释长等待；新增每 90 秒 Python 调用栈诊断及本浏览器 step 的 10 分钟失败上限，以终态日志定位实际卡点，不放宽通过条件。

[PR56](https://github.com/ForceMind/V-UI/pull/56) 已正常合并至 `0de276fd93344bbeaee03cb2c146347c0a4f8af4`，tree `e383d7b3c6b1de8120bb458bb6e840323526e064`。准确候选和主线各自八组 / 11 jobs / 126 steps 与同源四目标已验收：两者 loopback 均为原 SHA attempt 2，其余七组 attempt 1。候选首次为未改动导出链路的正向 DNS 超时，主线首次为未改动真实 apply 的 REALITY `LOCAL_PORT_OCCUPIED`；原失败保留，重跑不证明这些独立间歇机制永久修复，主线确切 socket 占用者仍未知。主线 D1 四场景重新通过，108.080 秒。

PR57 新候选 `3f1af02` 的 [Test run](https://github.com/ForceMind/V-UI/actions/runs/39658712928) 在 10 分钟上限处明确失败：真实取消和剪贴板拒绝/迟到反馈方法先通过，会话子场景约 30 秒时报 ERROR 后未结束；后续 editor 步骤未执行。调用栈仅显示 Playwright 调度 greenlet，不能据此断言具体调用。源码与确定性控制进一步证明测试夹具的两处问题：登录观察器晚于 logout，可能漏掉并发轮询触发的早到跳转；subTest 捕获错误后继续隐私检查，而被挂起的导航到最终 cleanup 才释放。修正将观察器前置，导航作用域在成功或异常时立即退出，原异常立即打印，并补充被挂起测试调用者的栈；会话条件和全部 DOM 秘密检查保留。早到跳转是已证明的竞态机制，不冒称已还原该次 CI 的具体事件顺序。旧 `cdf128f` run 检查时仍运行，不记为已取消或成功。

后续 `ce3f945` 的 [Test run](https://github.com/ForceMind/V-UI/actions/runs/39662133610) 仍在 10 分钟上限失败，但新诊断明确定位到会话旧 DOM 检查的 `page.evaluate`（browser test 第 482 行）：Playwright 在导航时可无超时地等待替换执行上下文，而夹具又挂起了登录文档，不能依此读取旧页面。这次失败不归因于分享 HTTP 或生产剪贴板逻辑。下一测试修正不再挂起网络导航：真实 Share 点击提供 sticky activation，测试添加 beforeunload 确认并取消原跳转，在同一已失效但仍存活的文档中验证真实 logout、`/auth/me` 401、原控制器/DOM 身份、URI/二维码属性清空、迟到响应和复制守卫；随后移除测试监听器，由夹具明确导航到登录页，不称原跳转已完成。同步 DOM 读取使用有界检查，假值仍只返回一次而不是重试断言；全部秘密断言与真实会话拒绝保留。异常清理、原错误保留和固定版本控制需通过，最终 Chromium 仍须新准确提交 CI，不能由这些源码推导代替。

准确候选 `f89c1f7c151bb2247e05430545628e63aad406d4` 的 [Test run](https://github.com/ForceMind/V-UI/actions/runs/39667123027) 已在 75.747 秒完成 Chromium 组，20 项中只有 delayed-completion 的 `escape` 子场景失败：关闭键返回后请求的 `aborted` 仍为 false；后续 editor 跳过，其余七组 CI 成功不代表可合并。两项真实会话结束方法通过，证明本次 beforeunload 观察方式完成了这些检查，不再卡在旧导航上下文。

固定 Element Plus 2.14.6 的关闭路径暴露了实际产品边界缺陷：Escape / 标题关闭 / 遮罩先改变组件内部可见性，退出动画的 `afterLeave` 才发出 `update:modelValue=false`。只观察父级 v-model 会把请求失效与秘密清理推迟到动画之后，动画期间迟到响应仍可写回；原“显示后 Escape”测试先等不可见，因此漏过该窗口。本次在 `before-close` 钩子中同步执行同一个关闭清理，再通知组件完成关闭；直接 model 变更的同步 watcher 保留。原即时 `aborted` 断言不删除、不加等待，增加关闭完成回调之前已清空、待响应/待绘制/已显示三种状态及迟到结果/复制守卫的控制，固定 Vue 与真实 Chromium 均须新准确提交重新验证。不是仅延迟测试来掩盖失败，也不改变分享端点、协议或凭据策略。

本任务仍为独立草稿 PR，已从原 PR56 候选分支改基线至 `0de276f`；PR58 正常合并后再以保留历史的双亲提交纳入 `2ce795ddfb1d40520a029e40f518881a39a9a161`，两项既有端口测试修正原样保留。PR58 新主线已独立完成下节验收，不因本任务的旧结果而授予分享修正资格。改基线后的准确 diff、独审、完整八组 / 四目标与 Chromium 均须重新核验，不能继承原头的通过项或绕过失败，再允许正常合并并复核新主线。无发布部署、真实 Tailscale、网络安全设置、凭据或受限 UDP D2 动作；原失败和下方历史阶段均保留。

## 2026-10-21 已合并并完成主线验收：真实 apply 切换夹具端口

[PR58](https://github.com/ForceMind/V-UI/pull/58) 准确候选 `65ad9bb891623a5e0e26999c017e87351aa126bc` / tree `6e1d3b60d208ee2a8effb84b6ecc8be89460e477` 已完成独审、八组 / 11 jobs / 126 steps 首次通过与同源四目标，正常合并至相同 tree 的 `2ce795ddfb1d40520a029e40f518881a39a9a161`。准确主线已独立完成八组 / 11 jobs / 126 steps 与同源四目标验收；installer 首次在未改动的核心下载 GitHub API 查询处收到 HTTP 403，原 SHA 仅失败 job 的 attempt 2 成功，其余七组 attempt 1。原失败保留，未改 pin、凭据、权限或请求端点；该主线资格不授予尚失败的 PR57 浏览器资格。

主线 [Test](https://github.com/ForceMind/V-UI/actions/runs/39666726461) 为 2021 项，1815 通过、206 显式跳过；独立 Chromium 的认证 / 工作区 / 代理 / 每入口 / D1 / editor 六组分别 1 / 1 / 21 / 4 / 9 / 2 项，均无跳过通过。[loopback](https://github.com/ForceMind/V-UI/actions/runs/39666726262) 十组共 220 项全部执行通过；真实 apply 四项 50.711 秒，D1 四项 91.392 秒，REALITY URI/YAML 各 DNS18/HTTPS10、TLS 各 DNS24/HTTPS13，D1 普通 DNS 均 0 且最终排空完整。同源四目标和原 installer 403 证据列在 PR58，未创建发布或实际部署。

从 [PR56](https://github.com/ForceMind/V-UI/pull/56) 合并后的准确 master `0de276fd93344bbeaee03cb2c146347c0a4f8af4` 独立修改测试。该主线的 [loopback attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39657832360/attempts/1) 在真实 apply 切换组出现 `LOCAL_PORT_OCCUPIED`；日志未识别占用 socket，不能断言具体历史根因。一次失败后，后续 subTest 的计划切换标签不一定等于实际已运行的前态，不能仅凭标签归因。

`ProxyApplyRealTests.setUp` 在父夹具完成后、构造 store/runtime 或首个 draft 之前，仅调用一次已有 D1 端口分配器，替换父夹具释放的 `bind(0)` 结果。相同端口贯穿 TLS/REALITY 两套 12 条切换、失败回滚及两种重启；不在 check/apply 失败后重选或重试，不修改共享父夹具、分配器、生产代码、内核设置、固定核心或工作流。分配仍非原子预留；原真实 TCP/UDP × IPv4/IPv6 竞争绑定拒绝、旧 PID/活动 revision 保留、core SHA 和回滚断言保持不变。

新增 `test_proxy_apply_fixture_ports.py` 直接执行实际 setup、draft、编译器、私有 store、coordinator 和切换/回滚/重启测试方法；仅核心与网络拓扑为 fake，覆盖一次分配的顺序、全程固定端口、分配失败、preflight 错误原样传播，以及 core apply 失败正常回滚后返回错误且无重试。它不是实际二进制、TLS/REALITY 转发或真实重启资格。开发环境已有 netlink EPERM 限制，本次未重试真实核心或 Chromium；准确候选与合并主线的完整 CI、独立审查及原实际链路门槛仍须另验。本地控制不代替新准确提交验收；不发布或部署。

本地聚焦验证共 135 项：131 通过、4 个真实 apply 方法因未启用而明确 skip；Python 编译与文档检查通过。六项新增控制之外，另以仅内存 AST 变异确认：移除首次分配、在 draft 中重选、错误后重试 apply 均被控制捕获；不改变磁盘上的测试或生产代码。完整套件及真实 CI 尚未据此判定通过。

## 2026-10-21 当前目标：D1 TCP 验收端口分配修正

主线已推进至 [PR55](https://github.com/ForceMind/V-UI/pull/55) 的 `41503cb6190a88bf330f5d5f997065b7682b7f97`，tree `88c81bc1a64067d66d3c1bfa268e6a7fcbb5094b`；准确候选与该主线均八组 / 11 jobs / 126 steps 首次成功。默认关闭的状态卡片和托管 launcher 修正已合并，真实 Tailscale 与共存/恢复仍未验；下方旧候选文字按历史解释。

下一实际交付修复 D1 双实例 TCP 验收夹具的自动端口竞争。旧 master `7512051` 的 [loopback attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39638041254/attempts/1) 曾在 TLS-YAML 导入后的 check 返回 `PROXY_LOCAL_PORT_OCCUPIED`；原样 attempt 2 通过不等于根因永久消除。确定性 socket 模型已证明提前 `bind(0)` 取号并释放后，HTTP 客户端可占用该自动端口；历史日志没有占用者，不能断言那次失败的具体 socket 身份。

本修正仅在 D1 测试首次启用本机代理之前，从只读 Linux 自动端口范围之外选取最多 128 个不重复候选，复用未修改的生产占用检查；选定后所有负向/恢复场景固定同一端口。PR56 独审提出的加固命名空间边界也纳入：只读 `ip_unprivileged_port_start`，候选不低于该有效阈值且至少 1024；缺失或非法内核值拒绝，不修改任何 sysctl。无 HTTP 409 重试，无 bind(0) 回退，不改变产品代码、协议、TLS/认证断言、网络设置或工作流。显式竞争绑定仍须拒绝，不声称原子预留。确定性控制、原双实例实测与独立准确候选/主线 CI 均为门槛；受限 UDP D2 不恢复，不发布部署。

## 2026-10-21 独立修正：托管启动器状态选项

[PR54](https://github.com/ForceMind/V-UI/pull/54) 已正常合并至 `7512051dfa192a8f623a3cebe3cb43d37f7f42e0`；候选八组 / 11 jobs / 126 steps attempt 1 成功，但合并后发现新 inline P1：托管 launcher 未转交状态 opt-in。合并前漏读 inline 的门禁缺口及证据保留在[状态合同](TAILSCALE_STATUS_CONTRACT.md#pr54-后的-launcher-集成修正)，不能将原绿灯当成完整功能资格。独立修正仅在 panel 模式白名单转交字面 `0` / `1`，默认不设置；不改变服务配置、socket 权限、认证或实际启用状态。准确修正候选/主线验收另记，合并前必须读最新 review 与 inline 内容。

## 2026-10-21 独立候选：可选只读管理状态

基于 PR52 正常合并的准确 master `66f7ff74247741b1fe3e5f1610e1d33d52d42f58`，按 Issue53 V-TS-02 实现[默认关闭的状态观察卡片](TAILSCALE_STATUS_CONTRACT.md)。固定本机命令、限时限长、白名单与缓存，无安装/登录/网络/权限/核心动作；真实 Tailscale、共存/回滚及 URL/身份恢复尚未验收。新候选准确审查/CI 见独立 PR，不继承旧提交资格。受限 UDP D2 不恢复，不发布或部署。

## 2026-10-20 独立文档任务：Tailscale 管理通道

[PR52](https://github.com/ForceMind/V-UI/pull/52) / [Issue53](https://github.com/ForceMind/V-UI/issues/53) 仅推进[可选管理通道方案](TAILSCALE_MANAGEMENT_OVERLAY.md)和[共存/回滚验收计划](TAILSCALE_MANAGEMENT_ACCEPTANCE.md)。最低共存和恢复门槛前移到安装建议之前；原 HTTPS 域名、端口、证书与 Host/Origin 的管理端定向解析路径尚待独立环境验证。`tailscale0` 地址变化可能影响策略恢复的结论为源码推导，未实测，不能排除接口或自动重新应用来绕过。尚未安装、实现、改网络/权限或获得新资源资格，不发布、不部署。

此前 `implement-vui-restricted-udp-d2` 已被平台中止；本次不恢复、复制、重新审查或执行该工作。下方历史 UDP“下一步”不是恢复授权；本轮不手动启动/修改该专项 CI。PR52 原 Portable distro-probe 的源超时/python3 缺失/exit 127 仍保留 failure，详见验收计划账本，不称产品逻辑失败或全绿。下方历史状态与原失败不覆盖本段范围。

更新：2026-10-18。每入口 TCP 三态已完成 PR49 正常合并及准确主线验收，见[收口证据](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。本文同时保留尚未完成的独立任务；计划记录不等于那些任务已通过。

## 先做什么

1. 阅读 [开发约束](../AGENTS.md) 和 [每入口路由契约](PER_INBOUND_ROUTING_CONTRACT.md)。
2. [PR49](https://github.com/ForceMind/V-UI/pull/49) 已正常合并至签名 `0962f0c4995b3b7606652027f2330285e4dea1f7`，tree `b12d1e40d16060d3adfa1c29fb380ae5949896f5`，准确主线八组与四目标已独立验收。接手先核远端默认分支 **master**、当前工作树及后续提交；从准确最新 master 新建独立任务分支，不回到旧 feature 分支覆盖变更。
3. 已完成：同一 V-UI 实例中，现成客户端导入不同普通 VLESS 节点，分别“跟随全局 / 指定直连 / 指定中转”。下一独立计划是标准客户端 UDP 补验；先读下表及兼容计划，不能把 TCP 收口当作 UDP、手机或发布许可。新提交仍需自己的准确验收。
4. 使用固定官方核心。不要从 UDP 实验分支整批移植补丁，也不要把手机客户端自研/重编译设为产品前提。
5. 每阶段更新本文状态表与证据，提交可恢复的 WIP 文本备份。最终准确候选仍须完整审查及 CI；WIP 不是通过或可合并声明。

## 用户已确认的产品含义

- 直连节点：客户端 → 该入口服务器 → 目标。
- 中转节点：客户端 → 国内入口 → 选定海外上游 → 目标。
- 手机只导入所选入口的标准配置；第二跳由服务端决定，海外 UUID、地址与本机代理口令不进入设备配置。
- 主机本身代理开关与入口默认中转开关独立。每入口策略覆盖默认，所有中转入口共用一个 `selected_upstream_id`，本轮不做每入口多个上游。
- 本轮新增资格仅为 TCP。**有效中转入口的 UDP 继续拒绝**；普通直连节点原有能力和导出语义不被笼统的“UDP 关闭”改变。
- 单元、核心配置检查、真实 Linux loopback、浏览器、手机 VPN/TUN、生产线路分别验收。没有手机真机结果。

## 可追溯基线与当前状态

| 工作 | 准确依据 | 状态及剩余 |
| --- | --- | --- |
| 正式 v0.4.8 | `a9f9b5d9c0dd2aada5e8f5f2270b2ce93440d5ff` | 已发布的安装器修复；后续主线功能不自动包含在旧发布中 |
| M0–M4 双开关 | `9a97ad13aae2fabb5ed969fdc0f930c36fdc55b7`，[PR44](https://github.com/ForceMind/V-UI/pull/44) | 主线收口；[历史失败/四目标证据](DUAL_PROXY_RELAY_PROGRESS.md)保留 |
| D0 UDP/DNS 能力门槛 | `13c884a0539cf9822fca2fe2db58325fa4644048`，[PR46](https://github.com/ForceMind/V-UI/pull/46) | 主线收口；不是开放 UDP 中转 |
| D1 TCP 上游专用导出 | `a98da9afbbf58a54f4f102cccec2f26b6e07d2ee`，[PR47](https://github.com/ForceMind/V-UI/pull/47) | 本阶段之前的产品基线；标准设备导出与“供另一 V-UI 导入的上游导出”用途不同 |
| 每入口三态 TCP | 签名 master `0962f0c4995b3b7606652027f2330285e4dea1f7`，[PR49](https://github.com/ForceMind/V-UI/pull/49) | P0–P5 已收口；准确候选及主线八组、四目标、真实标准客户端和浏览器均独立验收，见[完整记录](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。尚未发布 |
| D2 XUDP/大包/owner 实验 | `afbdba100c5f0e3dc6888684fdbab31980720df3`，tree `996d37d925be8edd18cbf921d3cc580f43c0730d` | 独立实验部分合同通过，整体仍失败；未并入产品，见下节 |
| 标准客户端 UDP 补验 | [兼容边界及最小计划](STANDARD_CLIENT_COMPATIBILITY.md) | 尚未执行新的 stock Mihomo 大包/同应用关联恢复组；不阻塞本轮 TCP 产品功能 |
| 手机、真实外部线路 | 实机与运营线路独立门槛 | 未验收；Linux core loopback 不代替手机导入/TUN/移动网络 |
| 低资源长期目标 | [资源合同](LOW_RESOURCE_TARGET.md)、[已有软件收口](LOW_RESOURCE_CLOSURE_20261011.md) | 既有软件优化证据保留；真实 1 vCPU/512 MiB 整机和 24 小时稳定性仍待专门环境 |
| 新功能正式发布/部署 | [发布门槛](RELEASING.md) | 未执行；代码合并不等于发布，生产部署及新权限仍另行批准 |
| 其他远期范围 | [路线图“后续”](../ROADMAP.md#后续) | DNS-01/通配符、核心升级/回滚、任意 YAML 合并、专用容器/NixOS 等仍是后续独立版本，不并入本轮 |

### a98 产品主线 CI

2026-10-18 只读复核准确 a98 的八个 run 均 completed/success、attempt 1：
[test 38918235245](https://github.com/ForceMind/V-UI/actions/runs/38918235245)、
[docs 38918235289](https://github.com/ForceMind/V-UI/actions/runs/38918235289)、
[ACME 38918235288](https://github.com/ForceMind/V-UI/actions/runs/38918235288)、
[one-click 38918235306](https://github.com/ForceMind/V-UI/actions/runs/38918235306)、
[ToClash 38918235349](https://github.com/ForceMind/V-UI/actions/runs/38918235349)、
[portable 38918235256](https://github.com/ForceMind/V-UI/actions/runs/38918235256)、
[deployment 38918235367](https://github.com/ForceMind/V-UI/actions/runs/38918235367)、
[loopback 38918235294](https://github.com/ForceMind/V-UI/actions/runs/38918235294)。
这些是旧基线结果，不能替代每入口功能新准确提交验收。

### afb 实验结果与不能继承的资格

[run 39146603341 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39146603341)、
[PR48 研究记录](https://github.com/ForceMind/V-UI/pull/48#issuecomment-6252755306)。PR48 仍 Draft，实验 head 与 PR 自身 head 不同，均不代表产品合并。

- owner 机制 838 叶分别在 race / race+debug 严格通过；base/final 各 23 组与 22 项跨平台编译通过。
- 实验 owner-on 三角色链 C：78 case/198 叶，其中 195 次完整请求-回复与 3 次本地 EMSGSIZE；D：6 case/12 次请求-回复通过。
- 三条 idle 场景各在同一应用 SOCKS 关联上完成一次恢复：60000 字节往返，等待完整六秒观察窗，再以新的 32768 字节包启动第二代。旧私有传输退出先于新代；不是同一真实 owner 连续 200 次恢复，也不授整窗 OS 连续性。
- owner-off 原 D/A/V 仍失败，B/U 只是诊断完成且容量失败；原官方设备矩阵两 arm 各 19/20，四个 VMess 官方设备大包仍 32768 → 精确 15992 前缀，60000 尚未发送。
- 因此整体 run 仍 failure。旧失败不改判；成功的三角色组合只是一组已测条件，**不是所有现成客户端必须替换核心或必须自研 App 的证明**。
- 完整源码补丁、测试和失败账本留在实验分支 `experiment/xudp-mux-lifecycle-20261017` / `experiment/socks-owner-recovery-20261018`。不把实验二进制发布、安装到用户设备或迁入本轮产品。

## 本轮执行顺序与退出门槛

| 阶段 | 交付 | 当前状态 | 退出门槛 |
| --- | --- | --- | --- |
| P0 | 本入口、设计契约、精确调用点与兼容纠正 | 已完成，设计和文档已独审 | 无未决的保护策略降级，独审可据文档接续 |
| P1 | 严格三态字段、旧库迁移、统一有效策略、身份兼容 | 已完成，纳入准确候选/主线全套验收；线程迁移测试不扩称跨进程 | 旧全 inherit 配置/身份保持；非法/null 拒绝；省略更新保留 |
| P2 | apply/rollback/启动/恢复/备份/删除保护 | 已完成，双向 fence、事务/恢复和四目标 installed smoke 通过；模拟与真实层次仍区分 | 双全局 off + explicit relay 仍受保护；任何不一致不退普通直连 |
| P3 | API/UI/导出三用途与 desired/applied 标识 | 已完成，主线 proxy 20 方法、实际 API/core 浏览器 4 方法、D1 九方法及 managed editor 两方法全部通过 | 标准客户端只获入口信息；取消/刷新/失败无假生效/秘密泄露 |
| P4 | stock Mihomo 同实例三个节点和 host 四组合 | 已完成，主线 7 控制 + 3 真实方法；61 次业务 HTTPS + 3 控制，各场景单客户端 PID | 真实目标分别观察本机/海外出口；负向原因、零误送达、无回退；四状态/12 切换 |
| P5 | 最终候选、独审、八组、四目标、正常合并后重验 | 已完成，b5 候选八组 attempt 1；0962 主线七组 attempt 1、deployment attempt 2，所有最新 job/step 通过 | 精确 SHA/tree 与原包绑定，独立验收；历史失败和 skip 单列 |

## 历史 WIP 与失败记录（不改判）

- P1 保留旧全 inherit 编译身份；12 组旧实现逐值对照一致。迁移并发只做了线程/RLock 幂等测试，不能称跨进程迁移已验。
- P1 独立旧 proxy 宽扫曾出现 112 errors、6 failures、50 skips，主要是旧瞬时 fixture 缺新字段和未整合跨层实现；保留原失败，随后必须在完整候选重新运行。
- 前端新增 4 组 Chromium 生命周期用例已接入原 browser gate；本地首次 13 项在 Chromium 启动时 socket EPERM，另一次启动工具遇只读 sandbox 错误。没有取得浏览器运行成功证据；不通过改变系统权限解决。
- P2 首版独审发现双向能力 fence 缺口：`state.schema_version=2` 却缺 DB mode 列、以及 DB 已有非 inherit 行但缺完整 v2 状态（含整个 proxy store 缺失）。已补 create_all 前的文件/表/列检查和持久行运行校验，拒绝 bootstrap 或补写标记掩盖损坏；合法 prospective 保存仍先写 fence 再提交 SQL。局部迁移/恢复控制通过，最终独审与全套结果继续单列。
- 首次完整集成宽扫：1923 tests，6 failures / 28 errors / 200 skips。失败包含独立维护 controller 的 restore 依赖闭包缺失、旧极简备份/异常转换和旧协议 fixture 未显式填写新字段；正在按真实原因修复。该结果不能写成全套通过。
- 第二次完整集成宽扫（`c4b2d367352501f14c84063f8d670ee8e5a69680`）：1965 tests，2 failures / 5 errors / 205 skips。4 个错误为新增导出夹具绕过首次 selected00/fence 流程，1 个错误为小型恢复夹具没有可验证的已选 release；两失败为新增进程尚未登记既有启动审计清单、日志 flush 与 writer Event 的调度竞争。已按真实契约修正：不减生产 fence，登记已审启动路径，拒绝函数返回当下核完整新日志，再有界 join writer。28 项聚焦控制通过，旧失败保留，最终全套仍需重跑。
- 恢复收尾额外覆盖：持久损坏策略校验先于 manager/coordinator 早退，损坏 store 的 sing-box stop 失败也不遮住 Xray stop；首次无文件/无表 v2 数据拒绝后不创建/改写文件。组件 139 项纯模式/迁移/恢复/维护控制与 55 项小型归档/manager/事务控制通过，不能相加冒充不重复的全套计数。
- 固定官方 sing-box 对复制 payload 的 installed policy smoke 完成 8 旧 + 8 混合配置检查及 4 组同配置/不同策略身份断言；这些不启动核心，也不是同提交四目标包或真实双跳资格。
- P4 首次真实尝试暴露夹具错误预期：首次 direct 保存实际正确返回 saved=true/applied=false 的 409，已按契约修正；第二次 check 有效后 apply 返回 PROXY_APPLY_FAILED。独立固定官方 sing-box 空配置启动得到 `create netlink socket: operation not permitted`，不据此判产品转发通过，也不提高主机权限绕过。余下真实方法未跑。
- 新增 8 项真实 HTTP/SQLite/private-store/完整 coordinator 控制通过，核心与 DNS 明确为 fake；覆盖首次 direct 无上游、首次 relay pin、真正 SQL commit 异常、两种过期 token、手动停止后保存和删除回滚。首稿夹具非法幂等键与清理顺序失败已修。删除测试曾错误要求可信完整旧配置也必须停机；按已冻结合同改为锁定旧 revision/config/context 逐值保留、dirty 与未应用响应，并验证下次启动因源不一致拒绝。界面明确提示旧入口可能仍可用，不把已保存删除称为已生效。
- 固定官方 sing-box 1.14.2 对三模式 × 四开关组合 × TLS/REALITY 上游的 8 组配置解析通过，另原 Xray/sing-box 正负配置检查通过；解析不等于转发。
- 各纯测试分别只证明对应组件。第三次完整本地宽扫结束时，真实新增核心/浏览器与同提交四目标尚未全部完成，不能由该次本地结果授予合并或功能收口；后续准确结果见下面的候选记录。
- 首次指定直连可保持两个开关关闭、**不选择上游**，正常 check/apply 建立活动配置；保存期间没有活动配置时显示待应用/不可确认。指定中转才需要先应用合法的已选上游；不能把普通直连变成必须配置海外。

## PR49 首轮准确候选与后续修复

- 准确公开候选 `8b01b4b085ff4520e72a105aac12a8e6a262d7e5`，tree `ed2cada9993f05025e895b7eba2108114ee77021`；当时 PR49 保持 Draft。该 tree 与本地独审 `483d38c1bfff0b282f53cea23404196fa60d25e1` 相同。第三次完整本地运行共 1970 tests，1765 通过、205 skips（不授对应真实资格）、零失败，187.061 秒；skip 不授真实资格。日志 SHA256 `6baa06aab56b96b1ebb055ecef74d707665d44d45d1f7bc21cb78a61e774df4e`。
- [Test attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451801) 的 1970 项基础套件与固定核心配置检查通过；proxy browser 步骤 20 方法（13 个浏览器流程与 7 个静态/路由控制）为 18 通过、1 failure、1 error。两个新 mocked editor 场景只修改已挂载页面的 URL fragment，没有真实切换 Vue 入站菜单，因此找不到预期行。后面的新实际 API/core browser 尚未执行。原日志 SHA256 `338b5f02200a39a28d5eb2f287ea5162839d5f24617d8295cab0c76ffbd7d678`。
- 后续修复仅改变测试导航：实际点击入站菜单并确认列表 GET 200，再执行原断言；不增加生产 hash 导航，不改变时间窗或资格断言。必须在后续准确 head 重跑真实浏览器，静态控制不能把本次失败改绿。修复后完整本地再次运行 1970 tests，1765 通过、205 skips、零失败，181.935 秒；日志 SHA256 `8c4a26493c56fa8fc6c957542574944023e6c1d9b2c6867acb72c25f5070861b`。
- [Portable attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451913) 的 aarch64-glibc 在固定上游 commit API 遇到 GitHub HTTP 403 rate limit，尚未进入 bundle/stage smoke；x86_64-musl、aarch64-musl 与 distro probe jobs 成功。失败日志 SHA256 `2615a3068670d52d9d141897f04529a0347207314bf99c23ceb8bd261104c921`。这不授四目标完整通过；代码已有修复时不通过重复旧 head 掩盖缺口。
- 同 head 的 [ACME](https://github.com/ForceMind/V-UI/actions/runs/39153451847) 实际 4 个证书测试与 1 个浏览器测试通过；[one-click](https://github.com/ForceMind/V-UI/actions/runs/39153451776) 实际非 root/systemd 流程通过。属于旧回归，不代替新增三态转发或实际浏览器资格。
- [真实链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451708) 成功。新增每入口组 10 方法（7 控制 + 3 真实）全部通过，53.497 秒。固定官方 sing-box 1.14.2 与 stock Mihomo 1.19.32 消费实际普通 VLESS YAML/URI；三个场景分别完成 2 / 14 / 45 次业务 HTTPS，共 61 次，另各有一次独立目标可达性控制。三模式 × host/global 四态的 12 条有向切换完整，每场景保留同一客户端 PID。错误 UUID/SNI/CA/上游停止均有实际拒绝原因、目标零误送达和直连保持；普通 direct 小 UDP 成功、relay UDP 实际 reject。每场景最终停止/排空核验通过。这是 Linux loopback，不能扩称手机真机/TUN或中转 UDP 能力。
- 该真实链路归档 `12146705982`：20290 bytes，SHA256 `e3b9f058b5b113d99cc9d6a3553dccb1ac188e645b9b61093ea4fe6ef0045643`；`result.log` SHA256 `b121a4f0707a08e4d58801f0ac635b7c0242474ae1002e7af6d7f5ee1b26168f`。固定 provenance 和完整旧链路组保留，独立复核已确认该有限合同，不能继承到下个 head。
- [部署消费 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39153451878) 成功：4 方法通过，512.597 秒；实际非 root 安装、HTTPS/原浏览器入口、停止后恢复及代码回滚通过。包内新增混合策略 smoke 为 8 配置检查 / 4 同配置异身份，恢复 smoke 为 4 混合恢复 / 4 旧 reader 拒绝；这些明确不启动代理核心、不代替三态转发。512/384/320 MiB cgroup 与单 CPU quota 回归通过，不代表同容量整机。证据归档 `12146577044`：80207 bytes，SHA256 `eebd88b7c254af3e9e738da6d1b76e9eb37264f2299a38af4585787353f81d54`。首轮八组最终 6 success / 2 failure；下一准确候选不能继承旧 head 结果，须重新核八组、真实新增步骤、四目标及最终独审。

## 第二候选 f2 的真实浏览器与保留失败

- 准确 `f2bee4319b3419a3af109ca9509f79b5c3809070`，tree `02ae09597490d9fd12727743d451d9ed4ce903a8`。相对 8b 仅测试导航与项目账本，生产逻辑、固定核心、工作流门槛未改。
- [Test attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988697) 中 proxy browser 20 方法全部通过，47.097 秒；新增每入口实际浏览器组 4 方法（3 控制 + 1 真实流程）全部通过，6.442 秒。真实流程经过创建 direct、无上游双关、检查/核对/应用、HTTP/SQLite 与实际官方核心 PID/配置/监听校验，无成功响应 mock；不等于手机或浏览器数据转发测试。
- 后续 D1 9 方法中，5 个真实浏览器流程及 3 个其他静态控制通过，唯一 pinned Vue controller 控制失败：其替代 `addEventListener` 的单值槽覆盖同名事件的旧监听，导致 `pagehide-secret` 断言失败。生产 DOM 允许多个独立监听；修复只把测试改为原生 `EventTarget`，同时严格验证 pagehide/popstate 的上游导出与节点编辑器清理、请求取消和迟到结果。秘密清理生产代码和原断言不放宽。更后的 managed-certificate editor 步骤未执行。
- 原失败日志 SHA256 `701ca45b9596f636380ca54526c09c7479ff16db876d4455adb4b30c73bb574a`。静态修复不能回写该 run 为通过；下一准确 head 必须重跑所有浏览器步骤。后续本地完整 discover 显式提供固定 Vue 资产、但不启真实 browser/core opt-in：1970 tests 中 1766 通过、204 skips、零失败，182.199 秒；此前因资产未提供而跳过的 controller 控制本次实际执行。日志 SHA256 `4bc46958b4b4842e665895d4738eef1b7c56700af65d9117891f59e825413d71`。
- [Portable attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988712) 四个 jobs 全部成功，包含此前缺失的 native aarch64-glibc；原 8b 限流失败保留。新准确提交的同源四目标最终归档仍须单独核验。
- [真实链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988764) 再次通过；新增组 7 控制 + 3 真实方法，56.091 秒。业务 2/14/45、12 转移、三单客户端 PID、拒绝/完整排空与原始计数均独立核验。归档 `12147166827` 为 20341 bytes，SHA256 `b9a3e4298e8969072701407466ee2a7f521f6154030659fd18a53659dd567736`，日志 SHA256 `88a950ae87967bea43fc4cb80f5284e3c1333bde7b5f7ce6cffb29c262d3e646`；不替代后续 head 验收。
- [部署消费 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39154988641) 成功，4 方法（3 夹具 + 1 实际）407.792 秒；512/384/320 MiB cgroup 均完成且无 OOM/kill，实际 source 为 f2。证据归档 `12146528099`：80045 bytes，SHA256 `cb6d0648b4d98ab94c059138dcf042866562c1ce38b29b0c0adff4780e2c7d8a`。本候选八组最终 7 success / 1 test failure，依然不可合并；不得拼接不同 head 的绿灯。
- 用户按实际界面操作的[每入口使用说明](PER_INBOUND_ROUTING.md)与 D1 教程更正已独审；均保持未发布候选、保存与应用分离、标准客户端和已知能力边界。其余准确运行结果继续绑定 PR49，不继承旧候选成功。

## 当前收口与下一步

主线 0962 的准确结果、八组链接、四目标包摘要、首次 403 和单次重跑见[收口记录](PER_INBOUND_ROUTING_CLOSURE_20261018.md)。以上 WIP/8b/f2 段落是各次历史结果，不能当成当前尚待验收，也不能把旧失败擦除。

1. 已完成的 TCP 三态保持固定官方核心和单 selected upstream；不再重新实现 P0–P5。
2. 在独立实验分支按 [stock 客户端 UDP 最小计划](STANDARD_CLIENT_COMPATIBILITY.md)核普通标准配置、容量和同应用关联恢复。保留原 sing-device RED，不提前开放产品 relay UDP。
3. 手机 VPN/TUN、公网线路、整机 24 小时低资源与正式发布分别待相应环境/授权，不能从 Linux loopback 或 cgroup 推断完成。
4. 新功能/修复/最终文档继续准确提交独审与 CI；没有 Release、生产部署或敏感权限变更授权。

## 接手命令与环境

使用独立非生产 Linux 云环境、Python 3.12、Node 语法检查、Git；真实测试另需固定官方核心及 Chromium。仅测试临时目录、假凭据、临时 CA，不修改系统信任/防火墙。实际安装/systemd 测试只在明确空白 runner。

```sh
git status --short
git rev-parse HEAD HEAD^{tree}
git log -5 --oneline
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
export VUI_DATA_DIR="$(mktemp -d)"
export VUI_FRONTEND_ASSETS="$(mktemp -d)/assets"
python scripts/vendor_frontend.py "$VUI_FRONTEND_ASSETS"
python scripts/check_docs.py
python -m compileall -q app tests scripts main.py
node --check web/js/app.js
node --check web/js/proxy.js
python -m unittest discover -s tests -p 'test_proxy*.py' -v
python -m unittest discover -s tests -p 'test_inbound*.py' -v
python -m unittest discover -s tests -v
```

真实核心/浏览器的可执行命令以 `.github/workflows/loopback.yml`、`test.yml` 和 [贡献指南](../CONTRIBUTING.md)为准：`scripts/fetch_test_cores.py` / `fetch_mihomo.py` 校验固定来源，`VUI_PROXY_LOOPBACK=1`、`VUI_TEST_CORES`、`VUI_TEST_MIHOMO` 激活真实 proxy 测试；`VUI_PROXY_BROWSER=1` 与固定前端资产激活浏览器。新增每入口真实步骤使用 `VUI_INBOUND_ROUTING_LOOPBACK=1` 执行 `test_inbound_routing_real.py`；配置解析用 `VUI_TEST_CORES` 执行 `test_inbound_routing_parser.py`。新增实际浏览器流程以 `VUI_INBOUND_ROUTING_BROWSER=1` 执行 `test_inbound_routing_browser.py`，准确 0962 已实际完成 3 项控制与 1 个真实流程；新提交仍须显式执行，不能仅因默认 discover skip 便计通过。

没有 Python/核心/浏览器时记录精确未跑条件；不要把 mock、语法检查、skip 或旧 run 当成新功能绿灯。最终报告至少包含准确 head/tree、命令/run/attempt、实际通过/失败/skip、二进制来源、资源清理、独审结论与未完成项。新代码提交后及时更新本表，不抹去先前失败。

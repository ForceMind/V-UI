# V-UI 0.4.26

本候选将 P5 已验收的 XHTTP 分享纳入下一版应用。管理员可为 Xray VLESS 入站选择验证 TLS、Stream One 并显式启用 h2，再通过单节点分享或授权订阅交付 Mihomo YAML 和 VLESS URI。使用单 UUID、空 flow、Chrome 及明确的 SNI、Host 和规范 Path。 [主线验收](https://github.com/ForceMind/V-UI/pull/169) · [XHTTP 使用范围](XHTTP_PUBLIC_PROFILE.md)

- 固定官方 Xray 26.3.27 与 Mihomo 1.19.32 验证 HTTP/TCP。两种实际导出均验证了真实转发，以及错误 UUID、CA、SNI、Path、Host、mode 的拒绝和零应用送达。
- 含 XHTTP 节点的 sing-box JSON 导出会明确拒绝。编辑器无法保真的旧高级配置保留原值并拒绝本次编辑。HTTPUpgrade 保留刻画，D2 UDP 双跳保留受阻草稿，产品 UDP 中转保持关闭。 [D2 草稿](https://github.com/ForceMind/V-UI/pull/167)
- 候选升级矩阵加入公开 v0.4.25。临时 systemd 验收核对账户、节点、订阅、证书、DNS 私有材料、保存的 YAML 计划及原始输入，并回读新旧 Root 来源记录。实际通过结果与四目标同源证据随候选 PR 记录。浏览器未验证。

公开应用与短安装入口当前均为 v0.4.25。 [公开 v0.4.25](https://github.com/ForceMind/V-UI/releases/tag/v0.4.25)

## 0.4.25 历史说明

本版将已集成的 P4 YAML 应用流程纳入发行。管理员可私有合并两份 YAML，在解决冲突、保存并检查草稿后，显式应用到正在运行的受管 sing-box。应用后回读实际运行修订。启动失败或协调进程中断时，按持久事务恢复原配置、启停意图和流量。 [P4 主线验收](https://github.com/ForceMind/V-UI/pull/162) · [YAML 应用契约](YAML_APPLICATION_CONTRACT.md)

- 本轮支持静态节点和分组、已有可表达的规则及严格出口约束。受保护流量仍按计划拒绝未获授权的目标，应用失败会保留原流量路径。
- 检查记录绑定已保存修订、节点来源、地址及运行身份。记录过期或来源变化后需要重新检查，应用结果未知时沿用原幂等标识回读。
- 新增 YAML 运行上下文的私有备份与读取门槛。保存第三版状态后，缺少对应读取能力的旧版本会拒绝该数据。数据库已写入新节点或证书且与旧配置不一致时，专用离线恢复会拒绝该组合。

升级验收固定公开 v0.4.24 的应用源 `8b8d7cf82e55609a205455b351f659eb9de91974`，在临时 systemd 环境核对配置、账户、节点、订阅、代理草稿、证书与 DNS 私有材料。旧版和候选的 Root 来源记录分别回读。准确候选的四目标制品、新装及旧版升级证据见路线图。浏览器未验证。 [路线图](https://github.com/ForceMind/V-UI/blob/main/ROADMAP.md) · [公开 v0.4.24](https://github.com/ForceMind/V-UI/releases/tag/v0.4.24)

## 0.4.24 历史说明

[v0.4.24](https://github.com/ForceMind/V-UI/releases/tag/v0.4.24) 已正式发布。应用、独立组件和短入口的来源分别记录。[发布规范](RELEASING.md#当前稳定版)

- 新增 systemd 受管的独立核心选择、更新与回退，保留原配置、节点、证书和运行/停止意图。更新只接受与实际应用来源、目标及兼容合同相符的官方组件，先校验、备份，再切换和验证。
- Xray 26.10.21 的默认策略会拒绝通过节点访问部分私网及保留地址，包括服务器回环地址。升级确认会提示这一差异；如果业务依赖这些目标，请保留当前 Xray。组件保留这项上游限制，原内置版本不自动更换。
- 新增终端独立核心维护授权和维护页面的持久任务。Root 最后校权；提交结果未知时只查询同一个请求；旧普通授权不自动取得核心写权限。
- 严格导出和本机连接凭据继续核对已应用配置与原授权。使用独立核心时，还会核对实际运行的制品是否与受管选择一致；无法核验时拒绝交付。
- 远程双域备份停服前保存恢复上下文。中断后不会重做备份，面板停服时由 Root 菜单18恢复原服务状态；数据恢复撤销旧会话，完成后需重新登录和授权。
- 升级矩阵覆盖公开 v0.4.23，验证既有 DNS 数据和原配置保留。独立核心组件通过单独的协议资格和发布门禁。浏览器未验证。

真实验收、兼容范围和历史失败见[路线图](https://github.com/ForceMind/V-UI/blob/main/ROADMAP.md)及[核心契约](https://github.com/ForceMind/V-UI/blob/main/docs/CORE_UPGRADE_CONTRACT.md)。

准确应用源 `8b8d7cf82e55609a205455b351f659eb9de91974` 通过十组 Actions、32 个 job、四目标、新装和九个旧版升级。新组件的 x86_64 GNU 协议验收覆盖 27 套件、165 个方法。[集成验收](https://github.com/ForceMind/V-UI/pull/158)

## 0.4.23 历史说明

本版新增 Cloudflare DNS-01 与通配符证书管理，保留原配置、账户、节点、订阅与证书。版本公开状态、下载及准确来源以[官方 Release](https://github.com/ForceMind/V-UI/releases/tag/v0.4.23)为准；本说明描述功能和验收边界，不代替公开发行记录。

## 用户改变

- 证书页新增管理员Cloudflare DNS-01凭据、单域名或根域+一级通配符申请、续期与具体连接域名绑定。Token只写不显示，保存只核对Zone，不证明写权限。
- 旧HTTP、DNS单域和DNS通配符可同根共存；证书身份/profile关联不可静默改变，续期保留具体SNI，换证书保留原协议、UUID、传输及未知字段，停止核心不自动启动。
- 遗留验证记录可仅清理own TXT；未知写入不靠一次空查询假判成功，显式无残留核对记录为人工确认，不重签。
- DNS私有备份含凭据、历史修订和journal，使用新kind`backup-dns01-v1`；0600 ZIP没有加密。恢复后profile禁用待验证，auto_renew偏好保持，旧会话/订阅/维护授权撤销规则保持。

## 安装、升级和回退

DNS配置从已经安装、可管理员登录的面板开始。初装仍默认HTTP-01，需要80端口条件；本版不提供绕过首次安装的新入口。通俗步骤见[DNS-01指南](https://github.com/ForceMind/V-UI/blob/main/docs/DNS01_CLOUDFLARE.md)。

受支持升级会复用有效DNS面板绑定的已应用旧revision，不重签或改联系人；同ID待应用状态保留期望值和错误，不晋升为已应用。期望/已应用ID冲突或材料损坏时拒绝混用。旧用户、节点、订阅、证书和代理设置保护继续保留。

含DNS数据不能使用旧运行时回退，真实旧22 reader会拒绝DNS新备份kind；受支持activate/restore/在线维护在切换前核对读取能力。不要改kind、删除数据库或移除材料来绕过。原维护授权可使用既有材料，未扩大为DNS凭据、签发/续期设置或清理权限。

## 验收与限制

源码实现、三块应用及安装控制兼容独审已收口。早期单元/夹具错误和固定Pebble DNS传输假设失败均保留，工具下载HTTP504的两次运行未进入测试，不能补记通过。局部真实CA成功、失败流和旧reader拒绝也不能代替新版本准确全量资格。来源与运行链接集中在[契约记录](https://github.com/ForceMind/V-UI/blob/main/docs/DNS01_CLOUDFLARE_CONTRACT.md#候选与验收状态)和[路线图](https://github.com/ForceMind/V-UI/blob/main/ROADMAP.md)。

正式发布只晋升准确main已通过九组Actions/21个job、四目标构建、新装与八旧版升级的同源材料，并核对每workflow最新run/attempt、每个必要step和10角色收据。发行记录列出准确来源和运行链接；旧失败不能改判，旧提交的成功不能补齐新来源。官方短入口在新版公开并独立验证后切换，生产升级由管理员另行发起。

浏览器、真实Cloudflare账号、手机、物理512MiB整机与24小时稳定性未验证。有效中转UDP、XHTTP/HTTPUpgrade公开导出、固定核心和单上游边界不扩大，Go迁移继续暂缓。

## v0.4.22历史全文

上一版完整说明保留在[已收口文档提交59fc1cc](https://github.com/ForceMind/V-UI/blob/59fc1cce15df8da850dd0a13d58b4efe6879a6be/docs/RELEASE_NOTES.md)，原发布源说明见[固定应用提交558687a](https://github.com/ForceMind/V-UI/blob/558687a76cd29f4e4735284926eb05bf6664472e/docs/RELEASE_NOTES.md)。[维护分享发布记录](https://github.com/ForceMind/V-UI/blob/main/docs/MAINTENANCE_SHARE_0422.md)保留其独立发布、入口与升级证据，旧资产不覆盖。

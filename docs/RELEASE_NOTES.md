# V-UI 0.4.24

本说明针对核心独立维护开发候选。公开稳定版仍为[v0.4.23](https://github.com/ForceMind/V-UI/releases/tag/v0.4.23)；候选版本号不代表已有公开资产或短入口已切换。

- 新增 systemd 受管的独立核心选择、更新与回退，保留原配置、节点、证书和运行/停止意图。更新只接受与实际应用来源、目标及兼容合同相符的官方组件，先校验、备份，再切换和验证。
- 新增终端独立核心维护授权和维护页面的持久任务。Root 最后校权；提交结果未知时只查询同一个请求；旧普通授权不自动取得核心写权限。
- 远程双域备份停服前保存恢复上下文。中断后不会重做备份，面板停服时由 Root 菜单18恢复原服务状态；数据恢复撤销旧会话，完成后需重新登录和授权。
- 候选升级矩阵增加公开23，验证既有DNS数据和原配置保留。新组件尚须独立协议资格及人工发布门禁，旧固定核心版本和公开23短入口保持。浏览器未验证。

真实验收、兼容范围和历史失败见[路线图](https://github.com/ForceMind/V-UI/blob/main/ROADMAP.md)及[核心契约](https://github.com/ForceMind/V-UI/blob/main/docs/CORE_UPGRADE_CONTRACT.md)。

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

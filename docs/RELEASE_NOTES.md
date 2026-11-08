# V-UI 0.4.23

**源码候选，未公开、未部署。** 当前公开稳定版仍是[v0.4.22](https://github.com/ForceMind/V-UI/releases/tag/v0.4.22)，应用源`558687a76cd29f4e4735284926eb05bf6664472e`、19项资产与短入口保持。23实现及独审已收口，准确候选的完整九组/21 jobs、四目标、新装和旧版保数据升级资格仍待完成。

## 候选用户改变

- 证书页新增管理员Cloudflare DNS-01凭据、单域名或根域+一级通配符申请、续期与具体连接域名绑定。Token只写不显示，保存只核对Zone，不证明写权限。
- 旧HTTP、DNS单域和DNS通配符可同根共存；证书身份/profile关联不可静默改变，续期保留具体SNI，换证书保留原协议、UUID、传输及未知字段，停止核心不自动启动。
- 遗留验证记录可仅清理own TXT；未知写入不靠一次空查询假判成功，显式无残留核对记录为人工确认，不重签。
- DNS私有备份含凭据、历史修订和journal，使用新kind`backup-dns01-v1`；0600 ZIP没有加密。恢复后profile禁用待验证，auto_renew偏好保持，旧会话/订阅/维护授权撤销规则保持。

## 安装、升级和回退

DNS配置从已经安装、可管理员登录的面板开始。初装仍默认HTTP-01，需要80端口条件；本候选不提供绕过首次安装的新入口。通俗步骤见[DNS-01指南](DNS01_CLOUDFLARE.md)。

受支持升级会复用有效DNS面板绑定的已应用旧revision，不重签或改联系人；同ID待应用状态保留期望值和错误，不晋升为已应用。期望/已应用ID冲突或材料损坏时拒绝混用。旧用户、节点、订阅、证书和代理设置保护继续保留。

含DNS数据不能使用旧运行时回退，真实旧22 reader会拒绝DNS新备份kind；受支持activate/restore/在线维护在切换前核对读取能力。不要改kind、删除数据库或移除材料来绕过。原维护授权可使用既有材料，未扩大为DNS凭据、签发/续期设置或清理权限。

## 实际证据与未完成项

源码实现、三块应用及安装控制兼容独审已收口。早期单元/夹具错误和固定Pebble DNS传输假设失败均保留，工具下载HTTP504的两次运行未进入测试，不能补记通过。局部真实CA成功、失败流和旧reader拒绝也不能代替新版本准确全量资格。来源与运行链接集中在[契约记录](DNS01_CLOUDFLARE_CONTRACT.md#候选与验收状态)和[路线图](../ROADMAP.md)。

候选、dev、最终main需各自重新核准确source、每workflow最新run/attempt、每job/step及同源材料。23尚未获公开发布、受保护分支合并或生产升级授权；短入口保持22，发布资格完整通过且获批准后才独立切换。

浏览器、真实Cloudflare账号、手机、物理512MiB整机与24小时稳定性未验证。有效中转UDP、XHTTP/HTTPUpgrade公开导出、固定核心和单上游边界不扩大，Go迁移继续暂缓。

## v0.4.22历史全文

上一版完整说明保留在[已收口文档提交59fc1cc](https://github.com/ForceMind/V-UI/blob/59fc1cce15df8da850dd0a13d58b4efe6879a6be/docs/RELEASE_NOTES.md)，原发布源说明见[固定应用提交558687a](https://github.com/ForceMind/V-UI/blob/558687a76cd29f4e4735284926eb05bf6664472e/docs/RELEASE_NOTES.md)。[维护分享发布记录](MAINTENANCE_SHARE_0422.md)保留其独立发布、入口与升级证据，旧资产不覆盖。

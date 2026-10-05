# API参考

HTTP接口以`/api`开头。`/docs`和`/openapi.json`也要求管理员登录，可读取当前运行版本的完整schema。下表是稳定使用入口，不代替程序schema。

## 管理认证

`POST /api/auth/login`接受username/password，返回HttpOnly会话cookie；不把token返回正文。浏览器及非浏览器写请求都必须带精确Origin和`X-VUI-Request: 1`，并正确保存cookie。没有通配CORS、默认密码或公开注册。

`GET /api/auth/me`读取真实身份；`POST /api/auth/logout`撤销当前会话；`POST /api/auth/update_profile`（旧别名`/update`）修改账号，要求current_password，成功后撤销旧会话。密码校验错误不回显请求秘密。

## 节点与核心

| 方法/路径 | 行为 |
| --- | --- |
| GET/POST `/api/inbounds` | 列出/创建；可传certificate_id选择正式托管证书 |
| GET `/api/inbounds/{id}/editor` | 返回可视化编辑 profile、绑定状态和“是否已有凭据”；不返回 UUID/密码/Reality 私钥/Obfs 密码本体 |
| PUT `/api/inbounds/{id}` | 只在原 core/protocol 内更新备注、端口、启停、profile、证书绑定；不做隐式协议迁移 |
| DELETE `/api/inbounds/{id}` | 删除节点并清理其托管证书绑定 |
| GET `/api/inbounds/profiles` | 草稿表单能力目录，不是端到端验收证明 |
| GET `/api/cores/status` | 查询运行状态和期望/生效差异 |
| POST `/api/cores/{core}/apply` | 生成并检查候选，具体生效语义依schema |
| POST `/api/cores/{core}/restart`、`/stop` | 明确启动应用/停止 |

旧 Xray/sing-box 接口仍经过同一个鉴权边界。`/api/inbounds` 原始管理列表可能含服务端配置，不能作为编辑回填或分享接口；视觉编辑只使用 `/editor` 的脱敏模型。保存配置后核心应用失败会返回“已保存但未应用”的冲突状态，不能把数据库写入当作连通成功。

## 分流

GET `/api/routing/mihomo/snapshot`返回settings、revision、source；GET `/mihomo`返回settings并带ETag；PUT `/mihomo`必须带If-Match，提交完整严格设置。

GET `/api/routing/mihomo/preview`预览保存版本，POST同路径预览请求草稿且不写磁盘。GET `/catalog`返回分类和服务开关目录。

状态码：428缺少条件，409修订冲突，422输入错误，503保存文件不可用。不存在“失败时自动改成DIRECT”的逻辑。

## 专用订阅

POST `/api/subscriptions`：label、server（节点公开地址）、inbound_ids、formats、expires_days。返回只显示一次的token与paths。GET列表不回显token。POST `/{id}/rotate`产生新令牌；DELETE `/{id}`撤销。

匿名客户端唯一授权入口为 `GET/HEAD /sub/{token}/{format}`，format为`mihomo.yaml`、`raw`或`sing-box.json`且必须属于该令牌授权范围。无效令牌/格式404，配置未准备好409。订阅token不授予管理权限；不接受额外query参数绕过作用域。

## 证书

| 方法/路径（前缀/api/certificates） | 请求/结果 |
| --- | --- |
| GET `''`、`/capabilities` | 安全元数据、环境/工作器状态、过期及绑定信息 |
| POST `''` | domain,email,environment,auto_renew,accept_terms:true；202仅表示排队 |
| GET `/jobs/{job_id}` | queued/running/succeeded/failed及安全错误代码 |
| POST `/{id}/renew` | 仅到期窗口且不在冷却期时排队 |
| PUT `/{id}/auto-renew` | enabled布尔值 |
| POST `/{id}/bind-panel` | 正式证书、同面板域名、热更新 |
| POST `/{id}/bind-inbound` | inbound_id，受支持TLS节点 |
| POST `/{id}/apply` | 重试已配置消费者，逐消费者返回结果 |
| GET `/{id}/fullchain.pem` | 管理员下载公有证书链，无私钥接口 |

证书API不接受shell命令、用户hooks、任意CA URL、DNS provider token。申请错误与应用错误分开，不能看到job成功就忽略绑定失败。自动续期暂停不取消已经排队的明确请求。

通用401/403/429与维护方式见[排错](TROUBLESHOOTING.md)。

### 节点安全模式与托管证书

更新节点时，明确非 TLS 的 `profile.security`（`none` / `reality`）会解除已有托管续期绑定；可同时传 `certificate_id: null`。未传 `certificate_id` 的非 TLS 更新也不会遗留绑定。传非空 `certificate_id` 与明确非 TLS 模式相冲突时返回 409，不会悄悄改回 TLS。

保留 TLS 并解绑必须提供新的手工证书与私钥路径；任意一项仍指向原托管材料会被拒绝。空 `profile: {}` 沿用原配置，不构成离开 TLS，也不能绕过路径检查。无效的替换 profile 不会清除原绑定；有效配置已经保存而核心应用失败时，解绑跟随已保存的期望配置生效，响应继续区分 `saved` 与 `applied`。

这只修正编辑与绑定状态；`none` / `REALITY` 并未因此扩大本阶段的公开导出和连接验收范围。续期仍保留手动停止核心的 `CORE_STOPPED_PENDING_APPLY` 状态。

### v0.4.3 VLESS/WS profile

候选范围为 `core: sing-box`、`protocol: vless`，可视化 profile 使用 `transport: ws`、`path`、可选 `host`、`security: tls` 与明确 SNI。单 UUID、空 flow、正常 TLS 校验，可选 Chrome fingerprint。ALPN 不是可视化 profile 的输入字段：原始 API / 持久化 TLS 配置中的 `tls.alpn` 必须省略或恰为 `["http/1.1"]`，可视化 WS 编辑保留已有受支持值。已有配置含表单无法表示的 TLS、header 或 early-data 参数时拒绝编辑保存，不静默丢弃。最终 exact-head 验收尚待完成。

path 的 1–256 ASCII 字符/路径段限制与 Host 的 DNS-style/253 字符/63 字符 label 限制见[配置指南](CONFIGURATION.md#043-vlesswebsockettls-候选)。无效 path/Host 在 profile 编译时返回 422；不得通过原始配置绕过严格公开导出。订阅遇到未知 transport/header 字段、early data 或不支持组合时明确拒绝。

持久化的 `transport.headers.Host` 只用于客户端导出和回填，实际 sing-box 服务端配置去除该字段，不实施请求 Host 白名单。不同合法 Host 的请求接受与错误 SNI/CA 的 TLS 拒绝必须分别理解；Host 不改变证书绑定域名。新 WS 范围只声明 HTTP/TCP，编辑回填不返回 UUID 或私钥；公共订阅只提供连接所需 UUID，不包含私钥或服务器材料路径。

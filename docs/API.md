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

旧 Xray/sing-box 接口仍经过同一个鉴权边界。普通 GET 列表、POST 创建和 PUT 更新响应现在采用允许列表摘要：节点 id/core/protocol/remark/port/enable、流量/到期/tag/user_id 等元数据，`credentials`（user_count/has_uuid/has_password/has_obfs_password）和 `managed_certificate_eligible` 布尔值。**响应不再包含 `settings` / `stream_settings`，也不返回 UUID、认证/obfs 密码、REALITY 私钥、未知配置秘密或服务器证书/密钥路径。** 这同时适用于 `/api/inbounds`、`/api/xray/inbounds` 和 `/api/singbox/inbounds` 的正常列表/创建/更新响应，是对依赖旧原始响应的管理客户端的有意破坏性收窄；原始创建/旧接口更新的请求结构未因该响应修复改变；v0.4.7 REALITY 原始输入另受下述严格校验。

视觉编辑继续使用鉴权后的 `/api/inbounds/{id}/editor` 专用模型。它不回传 UUID/密码/REALITY 私钥/obfs 密码本体；为保留既有手工证书编辑能力，可回传特权管理员需要的证书/密钥路径字符串，但从不返回密钥文件内容。证书卡只使用 `managed_certificate_eligible`，不再读取原始 TLS 配置。该标志是保守的界面筛选提示，不替代绑定接口授权；含 `reality` 键的导入配置（即使为空对象）不进入证书选项，v0.4.7 绑定接口在写入 binding 前亦拒绝 REALITY。内部数据库、核心应用、受授权的客户端导出与停机备份独立于普通响应，不做脱敏写回；客户端导出仍包含必要客户端凭据，但永不包含服务器私钥或材料路径。保存配置后核心应用失败会返回“已保存但未应用”的冲突状态，不能把数据库写入当作连通成功。

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

这只修正编辑与绑定状态；`none` 不属于公开导出范围，REALITY 仅按下述独立 v0.4.7 限定候选推进。续期仍保留手动停止核心的 `CORE_STOPPED_PENDING_APPLY` 状态。

### v0.4.3 VLESS/WS profile

已验收 WS 基线范围为 `core: sing-box`、`protocol: vless`，可视化 profile 使用 `transport: ws`、`path`、可选 `host`、`security: tls` 与明确 SNI。单 UUID、空 flow、正常 TLS 校验，可选 Chrome fingerprint。ALPN 不是可视化 profile 的输入字段：原始 API / 持久化 TLS 配置中的 `tls.alpn` 必须省略或恰为 `["http/1.1"]`，可视化 WS 编辑保留已有受支持值。已有配置含表单无法表示的 TLS、header 或 early-data 参数时拒绝编辑保存，不静默丢弃。准确主线验收见[WS 收口记录](VLESS_WS_CLOSURE_043.md)。

path 的 1–256 ASCII 字符/路径段限制与 Host 的 DNS-style/253 字符/63 字符 label 限制见[配置指南](CONFIGURATION.md#043-vlesswebsockettls-基线)。无效 path/Host 在 profile 编译时返回 422；不得通过原始配置绕过严格公开导出。订阅遇到未知 transport/header 字段、early data 或不支持组合时明确拒绝。

持久化的 `transport.headers.Host` 只用于客户端导出和回填，实际 sing-box 服务端配置去除该字段，不实施请求 Host 白名单。不同合法 Host 的请求接受与错误 SNI/CA 的 TLS 拒绝必须分别理解；Host 不改变证书绑定域名。新 WS 范围只声明 HTTP/TCP，编辑回填不返回 UUID 或私钥；公共订阅只提供连接所需 UUID，不包含私钥或服务器材料路径。

### v0.4.4 VLESS/gRPC profile 已验证基线

限定 `core: sing-box`、`protocol: vless`；可视化 profile 使用 `transport: grpc`、字符串 `service_name`、`security: tls` 和明确 `server_name`（SNI），单 UUID、空 flow、正常证书校验。`service_name` 必须匹配 `[A-Za-z0-9._-]{1,128}`，保留字面值/大小写，不 trim 或类型强转；非法值在编译阶段返回 422。`.` / `..` 合法，但 `/`、query、百分号转义、Unicode、空白和非字符串不合法。

ALPN 不属于可视化输入：原始 API / 持久化 `tls.alpn` 必须省略或恰为 `["h2"]`，显式 `null` 也拒绝，编辑保留已有受支持值。Chrome fingerprint 独立可选。原始导入配置含不能表示的 TLS/transport/authority/headers/timer/multi-mode 选项时拒绝编辑，不静默删除；原始 API 也不能绕过严格公开导出。

持久化与 sing-box 导出对应 `transport: {"type": "grpc", "service_name": "vless.grpc_0-4.4"}`；URI 对应 `type=grpc` / `serviceName`，Mihomo 对应 `grpc-opts.grpc-service-name`。仅 HTTP/TCP，Mihomo `udp: false`、sing-box `network: tcp`。无 authority/Host 白名单，不把 service name 当证书域名；证书绑定仍依据 SNI。`/editor` 隐藏并保留 UUID，订阅可包含连接凭据，不含私钥或服务端材料路径。

已有 gRPC 节点缺失或畸形 UUID 时，编辑返回 422，不能通过自动生成新 UUID 掩盖导入问题。省略 flow、fingerprint 或 skip-cert-verify 对应编辑字段会保留原值，不将畸形或不支持配置自动变成可公开配置；畸形 SNI 类型也拒绝。

本范围已经完成[gRPC 准确主线验收](VLESS_GRPC_CLOSURE_044.md)，后续变更仍须重验。现有 gRPC Lite 错误 CA/SNI 可能表现为调用者超时，不能承诺及时返回 TLS 原因；首次失败、前置与当时候选文本见[历史契约](VLESS_GRPC_044.md)。

### v0.4.5 Hysteria2 profile 已验证基线

限定 `core: sing-box`、`protocol: hysteria2`；可视化 profile 使用 `security: tls`、`transport: quic`、明确 `server_name`（SNI），以及正式 `certificate_id` 或手工 `certificate_path` / `key_path`。原生 QUIC 是该协议自己的传输，不另生成 WS/gRPC transport。证书校验不能关闭。

`profile.hysteria2_password` 为可选密码：省略或空字符串，新建时自动生成、已有节点保留；非空字符串明确替换。密码保留字面字符，不 trim，长度 1–256，不能全为空白、含 Unicode 控制字符（Cc）或不能编码为 UTF-8 的 surrogate。错误类型或值返回 422，不回显秘密。`GET /api/inbounds/{id}/editor` 返回空 `hysteria2_password` 和 `hysteria2_password_set` 标志，不返回原密码。已有 users/password 缺失或畸形不能靠编辑自动生成替代；缺失或禁用的 TLS 不能靠无关的部分更新悄悄修复。

新 HY2 不默认注入 `up_mbps`、`down_mbps` 或 Chrome fingerprint。已有可表达的带宽/obfs 草稿允许保留其原值，但仍不符合严格公开契约；无法表示的导入字段拒绝编辑，不静默清除。公开导出只接受单密码和原生 QUIC 默认值，拒绝 obfs、hopping、带宽/拥塞、ALPN（包括显式 null）/uTLS 覆盖、多用户、未知字段及 TLS/SNI 冲突。

公开 URI 为标准 `hysteria2://`，密码百分号编码、查询为 `sni` 和 `insecure=0`；Mihomo 节点包含密码、`sni`、`skip-cert-verify: false` 和 `udp: false`；sing-box 出站包含密码、验证 TLS/SNI 和 `network: tcp`。订阅可以含连接凭据，不能含私钥或服务端材料路径；失败不退 DIRECT。仅验 HTTP/TCP 负载，QUIC UDP 端口本身不证明应用 UDP。

HY2 正式托管证书绑定/续期沿用原材料与应用状态分离，停止核心保留 `CORE_STOPPED_PENDING_APPLY`。该范围已完成[HY2 主线验收](HYSTERIA2_CLOSURE_045.md)，后续变更仍须重验；裸前置 58 项、历史失败和最终集成验收分别记录于[原契约](HYSTERIA2_045.md)。


### v0.4.6 TUIC v5 profile

限定 `core: sing-box`、`protocol: tuic`，profile 使用 `security: tls`、`transport: quic`、明确 `server_name` 及正式 `certificate_id` 或手工材料路径。新建生成服务端 `tls.alpn: ["h3"]`；严格公开导出要求该字段恰为 h3，不接受省略/null/空列表/其他 ALPN。默认拥塞 cubic、原生 relay、零 RTT 关闭、无指纹覆盖。

`profile.tuic_uuid` 与 `profile.tuic_password` 分别省略/空字符串时，新建生成、编辑保留；非空只替换对应项。UUID 是规范的连字符字符串；密码 1–256 字面字符，不 trim，不允许全空白、Unicode Cc 或无效 UTF-8 surrogate。非法类型/值、已有缺失/畸形凭据返回 422，不回显秘密、不悄悄重新生成。已有导入缺失 TLS 时，有意修复须明确 `security: "tls"`；已有 TLS 对象缺少 ALPN 仍拒绝，不通过部分编辑补齐。

`/editor` 返回空 `tuic_uuid` / `tuic_password` 与 `tuic_uuid_set` / `tuic_password_set`；普通列表/创建/更新继续使用 PR #23 摘要允许列表。特权编辑保留手工证书路径字符串，明确授权导出包含客户端 UUID/密码且无服务端材料。高级导入字段保留或拒绝；草稿保存不改变严格公开导出范围。

TUIC URI 是固定 Mihomo converter 的非官方客户端约定，不是官方通用标准；Mihomo YAML 包含 `sni`、`alpn: [h3]`、`skip-cert-verify: false`、`reduce-rtt: false`。其 TUIC adapter 硬编码 UDP 能力，省略无效 `udp: false`，不得声称禁用 UDP。sing-box 出站 `network: tcp`，应用 UDP 未验收，QUIC 仍要求节点 UDP 可达。

证书绑定/续期/失败保护/停止待应用沿用现有语义，TUIC 准确候选/主线验收已完成，见[TUIC 收口](TUIC_CLOSURE_046.md)。


### v0.4.7 REALITY/Vision profile 候选

限定 sing-box/VLESS。profile 使用 security=reality、transport=direct、flow=xtls-rprx-vision、client_fingerprint=chrome、skip_cert_verify=false，以及显式 reality_target/reality_server_name。reality_uuid 与 reality_short_id 为空时仅新建生成，编辑时保留；非空独立替换。short ID 必须为 16 位小写 hex。编辑响应这两项均为空，并有 reality_uuid_set/reality_short_id_set/reality_private_key_set 布尔状态；从不返回私钥。

已有 REALITY 节点必须先通过完整存储形状、UUID、配对密钥与 short ID 校验，不能靠编辑重生成缺失凭据。未知字段、ALPN、额外传输、mux、普通证书材料及非 Chrome/精确 Vision 拒绝。严格导出与编辑共享存储校验；不能借修改备注或切换安全模式静默删除未知项再公开。

绑定接口在写入 REALITY desired binding 前拒绝；有效 legacy raw TLS→REALITY 更新在节点保存后解绑，core apply 失败仍保留已保存/未应用语义。非法更新保持原节点/绑定。此前隔离 SQLite/API 复现仅观察到残留绑定及 TLS_NODE_REQUIRED 错误，后续 reapply 未修改 REALITY 或调用核心；不声称发生真实事故。


现代 PUT 另有已复现的显式转换边界：已有 REALITY 节点仅提交 certificate_id（包括空 profile 或未指定 security）时，旧代码会合成 TLS 并改变节点/绑定。v0.4.7 要求明确 profile.security=tls 才能转换并绑定；未明确请求在读取证书材料前拒绝，节点/绑定保持不变。显式 REALITY→TLS 正向回归保留 UUID。此事实与上述旧绑定后续 reapply 未覆盖 REALITY 的复现是两个不同路径，均只使用隔离假数据。

# 双开关代理与中转一期契约

日期：2026-10-15。契约版本：1；数据 schema：1。产品语义、数据/API、DNS/地址/诊断常量与验收矩阵的 M0 冻结候选；实际核心与链路资格仍待后续阶段证据。规划基线：`a9f9b5d9c0dd2aada5e8f5f2270b2ce93440d5ff`。

**本文冻结待实现的行为与验收门槛，不是已实现、已通过 CI、已发布或已部署的证明。** 各阶段事实只记入[进度与证据账本](DUAL_PROXY_RELAY_PROGRESS.md)。本契约承接 2026-10-15 经批准的双开关工程方案；新的计划内开发范围取代旧文档中仅限安装器/发布准备的当时范围，历史事实、失败与发布边界不改写。M0–M4 分别独立 PR；不修改 `VERSION`、固定核心或已发布资产来表示未完成的能力。

## 1. 产品语义与首个目标

新增两个默认关闭、相互独立的开关：

- `local_proxy_enabled`：服务器上明确配置 HTTP/SOCKS 代理的程序经本机鉴权入口访问海外上游。
- `relay_enabled`：设备经本面板已启用的鉴权节点接入后，由服务器转交所选海外上游。

首个目标是 M0 契约独立审查及准确提交收口；一期最终目标是 M4 取得完整软件证据。未取得证据前不得把保存草稿、开始监听、固定核心语法通过或旧版链路通过称为双跳可用。

| 状态 | 本机开关 | 接入开关 | 本机显式代理入口 | 现有节点接入后的出口 |
| --- | --- | --- | --- | --- |
| 00 | 关 | 关 | 不监听 | 原有普通行为 |
| 10 | 开 | 关 | 经唯一所选上游 | 原有普通行为 |
| 01 | 关 | 开 | 不监听 | 经唯一所选上游 |
| 11 | 开 | 开 | 经唯一所选上游 | 经同一个上游 |

仅开接入中转时也能直接导入、选择、检查和应用上游。关闭任一开关不能停止另一开关仍使用的上游。双关保留草稿和凭据，不自动删除。管理员明确关闭接入中转并成功提交后恢复普通行为，这是策略变更；上游故障不能自动改开关或恢复 direct。

### 1.1 入口资格是整体门禁

首期接入资格仅为 **sing-box / VLESS / TCP / TLS**，本机入口为 **sing-box mixed / loopback / 独立鉴权**。接入 VLESS 凭据、国内地址、端口、证书与海外上游完全独立；海外凭据不发给设备。

应用前枚举全部启用入口的 ID、核心、协议、传输及资格原因。只要一个启用入口尚未获中转资格，就阻止 `relay_enabled=true` 整体生效。不自动关闭不合格入口，不只中转部分入口后仍声称全部中转。普通模式下既有协议、Xray 高级节点和现有导出继续保留。

中转生效后的新建、恢复、启用、编辑、删除、证书更新和配置应用，均必须在同一快照/操作锁中重新检查资格和来源绑定。不能随后出现未绑定而直出的启用入口。入口矩阵扩大须另有逐组合真实链路证据。

### 1.2 不包含的能力与权限

不含 TUN、透明代理、全局环境变量、系统 DNS 接管、策略路由、真实防火墙修改、ICMP/ping、Git SSH 自动代理、多出口切换、链式多跳、远程订阅/provider 下载、脚本执行、应用 UDP 或任意客户端配置整包迁移。不要求修改 `/etc/environment`、shell profile、全局 Git 或 systemd 全局环境。

固定官方 sing-box **1.14.2**、Xray **26.3.27**、Mihomo **1.19.32**；不换 pin、不重编译、不引入额外常驻核心、不开放 XHTTP/HTTPUpgrade。四个 Linux 目标与既有 40 项 ToClash 范围保留。计划内开发不代表 tag/Release、资产替换、生产部署、真实 CA 条款、凭据或安全敏感权限变更授权。

## 2. schema 1 领域模型

所有对象严格类型校验，拒绝未知字段、类型强转、冲突表示、显式非法 `null` 和无法无损表达的值。客户端配置原文不能直接拼接进核心配置。

### 2.1 规范化 `UpstreamProfile`

下表是导入器与持久化层共同使用的内部 profile；`uuid` 只存在于短期解析对象和服务端私有存储，不得当普通 API DTO 返回。

| 字段 | schema 1 约束 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `protocol` | 字面量 `vless` |
| `name` | 1–128 个字符；省略时 `Imported upstream`；无控制字符 |
| `server` | 合法 IP 字面量或 ASCII DNS 主机名；不含 scheme、路径、用户信息或内嵌端口；导入仅验证语法，联网前另做地址策略检查 |
| `server_port` | 整数 1–65535，不接受布尔值或数字字符串冒充整数 |
| `uuid` | 单个规范小写、带连字符 UUID，秘密字段；不生成替代值、不把空值当成功导入 |
| `transport` | 字面量 `tcp`；源格式省略时仅按其明确 TCP 缺省值处理 |
| `encryption` | 字面量 `none`；缺省为 `none`，其他值拒绝 |
| `flow` | 空字符串或完整字面量 `xtls-rprx-vision`，不使用 `vision` 缩写；合法组合见下表 |
| `tls.mode` | 明确 `tls` 或 `reality`，不猜安全模式 |
| `tls.server_name` | 明确提供的 ASCII DNS-style 字面 SNI（最长 253 字符、每 label 最长 63，不含尾随点/端口/纯数字点式 IP），不从 `server` 猜测，不关闭证书验证 |
| `tls.alpn` | 有序且不重复的列表；TLS 允许 `[]`、`["h2"]`、`["http/1.1"]` 或两者的任一不重复顺序；REALITY 仅 `[]` |
| `tls.fingerprint` | 空字符串或 `chrome`；REALITY 必须 `chrome` |
| `tls.public_key` | TLS 为 `""`；REALITY 为无 padding 的规范 base64url，解码恰为 32 字节 |
| `tls.short_id` | TLS 为 `""`；REALITY 恰为 16 位小写十六进制字符串 |

| 出口组合 | flow | 其他条件 | 一期处理 |
| --- | --- | --- | --- |
| VLESS / TCP / TLS | `""` | 单 UUID、显式 SNI、验证 TLS、上述 ALPN/指纹范围 | 纳入实现及真实双跳验收 |
| VLESS / TCP / REALITY | `xtls-rprx-vision` | Chrome、规范公钥/short ID、无 ALPN 覆盖；固定包具备 `with_utls` | 纳入实现及真实双跳验收 |
| VLESS / WS 或 gRPC | 任意 | 固定核心有能力不等于本期字段映射及双跳已验 | 拒绝转换 |
| XHTTP、HTTPUpgrade、KCP、HTTP 伪装或其他协议 | 任意 | 不改协议或删除字段使其可导入 | 拒绝转换 |
| TLS + Vision、REALITY + 空 flow、`-udp443`、非 none encryption、ECH/未知 REALITY 字段 | 任意 | 超出严格组合 | 拒绝转换 |

profile 的稳定内部 ID 为 `up_<32 位小写 hex>`。来源 `source_format`/`source_fingerprint` 与秘密存储引用属于持久化 envelope；不混入源协议字段。持久化来源格式为 `manual`、`uri`、`v2ray-json`、`clash-yaml`；三种导入不产生 `manual`。`source_fingerprint` 为原始 UTF-8 输入 bytes 的 SHA-256，不保留可回放原文。ID 不由显示名称或用户自定义 tag 充当。

### 2.2 `ProxySettings` 与修订快照

| 内部字段 | 默认与约束 |
| --- | --- |
| `schema_version` | `1` |
| `local_proxy_enabled` | `false`，严格布尔 |
| `relay_enabled` | `false`，严格布尔，与本机开关独立 |
| `selected_upstream_id` | `null`；任一开关为开时必须引用当前有效且受支持的 profile |
| `local_port` | `7891`；仅非特权整数端口 1024–65535，应用前检查实际冲突 |
| `local_username` | 默认 `""`；ASCII `[A-Za-z0-9._-]{0,64}`；本机开关为开时非空，不复用面板管理员或入口凭据 |
| `local_password_ref` | 默认 `null`，仅服务端私有引用；普通响应不返回引用或密码 |
| `dns_policy_version` | `1` |
| `deny_policy_version` | `1` |

监听地址固定 `127.0.0.1`，不是可由 API 任意填写的地址。IPv6 loopback 只有另行完成 `::1` 监听/冲突/防外网验收后才启用；不得用 `::` 或 `0.0.0.0`。`set_system_proxy=false`。

`revision` 是配置身份；规范化确定性序列化及秘密版本都参与身份变化。只改秘密也必须得到新 revision；无变化写入保留 revision。草稿、活动快照和事务 journal 分离，单一已提交 `active_revision` 是生效事实来源；保存草稿不能写活动状态。默认双关的增量幂等迁移不改变原节点、凭据、端口或规则。

内部 `load_draft` / `load_active` 可供编译器读取私有材料；对外 `read_snapshot` 仅返回脱敏信息。读响应固定为 `{revision, settings, upstreams, active, pending}`，其中 `active` 为 `null` 或 `{revision, settings, core_revision}`；公开 settings 去除 `local_password_ref` 并增加 `local_password_set` 布尔值；该标记不是可写字段。`upstreams` 为摘要数组，`pending` 为 `null` 或未结束应用事务，不能被解释为应用成功；另以草稿/活动 revision 差异判断待应用修改。pending 存在时阻止新的草稿写入。

### 2.3 秘密与存储边界

- profile UUID、本机密码、REALITY 公钥/short ID 等连接材料不在预览、普通列表、设置响应、错误响应、浏览器日志或公共诊断中返回。profile 摘要保留非秘密字段，`uuid` 改为 `uuid_set`，TLS `public_key`/`short_id` 改为 `public_key_set`/`short_id_set`；再附 `id`、来源格式/摘要。存在标记不能代替访问控制。
- 原文只存在于短生命周期解析缓冲；不保存到数据库、浏览器 localStorage、遥测、公开日志或制品。
- 服务用户私有目录 0700，秘密/快照/核心配置文件 0600；拒绝不安全路径、符号链接替换及非预期所有权。文件引用不对客户端开放。
- 本机密码通过独立 write-only `local_password` 输入设置/替换，不自动生成或回显；新非空值为 12–255 个不含空格的可打印 ASCII 字符。省略、`null` 或空输入表示保留。新建启用本机代理必须已有密码，不能默认为匿名入口。删除/轮换必须是明确动作；清除密码只允许草稿和活动本机开关均关闭，双关本身不触发删除。
- 不宣称数据库已加密。备份包含敏感材料，按原有停机、权限和保管边界处理；测试秘密、测试 CA、数据库及原始敏感日志不进公开包。

## 3. 三种输入格式与严格转换

`source_format` 仅为 `uri`、`v2ray-json`、`clash-yaml`。格式解析成功不意味着候选协议获得支持。

| 格式 | 允许的容器 | 节点转换 |
| --- | --- | --- |
| `uri` | 单个 `vless://` URI | 严格参数白名单与上面的两类出口组合；其他 scheme 明确不支持 |
| `v2ray-json` | 单个 outbound 对象，或标准 `outbounds` 数组 | 只选择一个受支持 VLESS outbound，单 `vnext`/单 user；不启动原配置 |
| `clash-yaml` | 单个 proxy 映射，或 `proxies` 数组 | 只选择一个受支持 VLESS proxy；组、DIRECT/REJECT 不是上游节点 |

预览可同时显示支持和不支持候选；不得跳过不支持项后自动挑第一个。**保存时始终显式提交从 0 开始的 `candidate_index`，即使只有一个候选。** 重名显示序号，名称不是选择键。保存时对提交的同一原文重新解析/验证，不信任浏览器返回的规范化 profile 或旧预览内容。

已识别但不导入的客户端区块逐项列入 `ignored_sections`。保存必须提交完全匹配的 `acknowledged_ignored_sections` 集合；漏项、多项或过期确认均拒绝。未知顶层结构、所选候选内未知/危险/矛盾字段或丢失关键语义均阻断；不能“警告后忽略”。

仅允许已识别的客户端 `inbounds`、路由、DNS、策略、日志/API/统计、监听/控制器、组/规则等区块以“不会导入”方式列出；各格式准确白名单见下一节，与解析器测试共同冻结。provider 从不联网；仅有 provider 而无内嵌节点时要求具体节点，混合文件也只能选内嵌节点，并明确 provider 未读取。脚本、模板、include、环境变量展开、插件或任意远程下载拒绝执行。

### 3.1 逐字段映射与拒绝边界

| 规范化字段 | URI | V2Ray/Xray JSON | Clash/Mihomo YAML |
| --- | --- | --- | --- |
| 名称 | fragment；缺省为固定名称 | outbound `tag`；可由保存请求明确重命名 | `name` |
| `server` / `server_port` | authority 主机/端口 | `settings.vnext[0].address` / `port` | `server` / `port` |
| `uuid` | userinfo UUID | `settings.vnext[0].users[0].id` | `uuid` |
| `transport` | `type=tcp`，允许省略为 TCP | `streamSettings.network=tcp`，允许明确缺省 TCP | `network=tcp`，允许明确缺省 TCP |
| `encryption` | `encryption=none` 或省略 | user `encryption=none` 或省略 | 无此源字段，规范化为 `none`；显式 `encryption` 拒绝 |
| `flow` | `flow`，缺省空 | user `flow`，缺省空 | `flow`，缺省空 |
| `tls.mode` | 明确 `security=tls/reality` | 明确 `streamSettings.security` | 明确 `tls: true`；`reality-opts` 决定 REALITY |
| `tls.server_name` | `sni` | 对应 `tlsSettings.serverName` / `realitySettings.serverName` | `servername` |
| `tls.alpn` | `alpn` 逗号列表，保持顺序 | TLS `alpn` 数组 | `alpn` 数组 |
| `tls.fingerprint` | `fp` | TLS/REALITY `fingerprint` | `client-fingerprint` |
| `tls.public_key` | `pbk` | REALITY `publicKey` | `reality-opts.public-key` |
| `tls.short_id` | `sid` | REALITY `shortId` | `reality-opts.short-id` |

准确白名单如下；未知字段即使为空也不能忽略：

- URI query：`type`、`security`、`encryption`、`flow`、`fp`、`pbk`、`sid`、`sni`、`alpn`。`security`/`sni` 必填。`headerType`、`insecure` 等不在白名单中，任何值均拒绝。
- JSON outbound：`protocol`、`tag`、`settings`、`streamSettings`。`settings` 只接受 `vnext`；唯一 server 只接受 `address`、`port`、`users`；唯一 user 只接受 `id`、`encryption`、`flow`。
- JSON `streamSettings`：`network`、`security`、`tlsSettings`、`realitySettings`，两个安全对象互斥。TLS 对象只接受 `serverName`、`allowInsecure`、`alpn`、`fingerprint`；REALITY 对象只接受 `serverName`、`fingerprint`、`publicKey`、`shortId`。`mux`、`tcpSettings`、`spiderX` 等拒绝。
- YAML proxy：`name`、`type`、`server`、`port`、`uuid`、`network`、`tls`、`servername`、`alpn`、`client-fingerprint`、`flow`、`skip-cert-verify`、`udp`、`reality-opts`；REALITY 对象仅 `public-key`、`short-id`。`tls` 必须为严格布尔 `true`；`udp` 省略或严格 `false`，不接受 `true`。
- JSON 已识别且不导入的顶层区块：`log`、`inbounds`、`routing`、`dns`、`policy`、`api`、`stats`、`metrics`、`observatory`、`burstObservatory`、`fakedns`。
- YAML 已识别且不导入的顶层区块：`proxy-groups`、`rules`、`dns`、`tun`、`hosts`、`profile`、`sniffer`、`proxy-providers`、`rule-providers`、`port`、`socks-port`、`mixed-port`、`redir-port`、`tproxy-port`、`allow-lan`、`bind-address`、`mode`、`log-level`、`ipv6`、`external-controller`、`external-controller-tls`、`external-controller-unix`、`external-ui`、`external-ui-url`、`secret`、`authentication`、`skip-auth-prefixes`、`lan-allowed-ips`、`lan-disallowed-ips`、`interface-name`、`routing-mark`、`find-process-mode`、`unified-delay`、`tcp-concurrent`、`keep-alive-interval`、`geodata-mode`、`geo-auto-update`、`geo-update-interval`、`geox-url`。只记录区块名，不回显其中秘密或读取其中 URL。

JSON `allowInsecure` 与 YAML `skip-cert-verify` 只能省略或严格 `false`，`null`、字符串和真值拒绝。服务端私钥、证书路径、自定义 CA 文件路径、额外 transport、multiplex、ECH、detour/socket/代理链参数不属于客户端上游契约。重复 query 参数、错误百分号转义、UUID userinfo 密码、冲突安全标记和非法路径拒绝。不得猜 SNI、REALITY flow 或指纹来“修好”输入。

### 3.2 冻结的解析资源限制

| 资源 | 硬上限 / 行为 |
| --- | --- |
| 编码 | 只接受严格 UTF-8；拒绝非法编码 |
| 单次原始文本/文件 | 1 MiB，即 1,048,576 bytes；上传与粘贴同限额 |
| 单 URI / 单字符串 | 各 8 KiB，即 8,192 UTF-8 bytes |
| 候选数量 | 最多 128 |
| 结构深度 | 最多 16 层；在完整对象构造前检查 |
| 名称 | 最多 128 个字符；拒绝控制字符、不可见换行注入 |
| token 数 | 最多 32,768；在对象完整构造前检查 |
| 整数 | 有符号 64 位：−2^63 至 2^63−1 |
| worker 输出 | 最多 256 KiB，即 262,144 bytes |
| API 原始请求体 | 最多 2 MiB，即 2,097,152 bytes；解析内容本身仍受 1 MiB 上限 |
| 解析执行 | 受限子进程；2 秒绝对期限；地址空间上限 128 MiB |
| JSON | 拒绝重复键、NaN/Infinity、尾随拼接文档和超限整数；无任意对象执行 |
| YAML | 安全构造且额外拒绝重复键、alias/anchor、merge key、显式 tag/directive、多文档及超限结构 |
| 输入扩展 | 不解压、不解 Base64 订阅集合、不读取远程订阅/provider URL |

字节、token、节点数量与深度在构造大对象前受控；仅 `safe_load` 不构成资源安全证明。上述 token 总数、整数范围及输出限额作为解析器同版本常量纳入边界测试，不允许无界缺省。解析器只有规范化数据输出，零网络、零核心启动、零 shell/插件执行。四目标分别验证限额能执行；超限、worker 崩溃或超时均失败关闭，不能退回无隔离解析。错误仅含稳定错误码、受控字段路径及非敏感原因，不拼接原输入。

## 4. 管理 API 合同

全部 `/api/proxy/*` 使用现有管理员鉴权、同源/CSRF 边界；不新增匿名接口。请求额外字段拒绝。预览和保存均不发起连接。

| 方法与路径 | 请求 | 响应 / 副作用 | 阶段 |
| --- | --- | --- | --- |
| `POST /api/proxy/import/preview` | `{format, content}` | 脱敏 `{source_format, source_fingerprint, candidates, ignored_sections, notice_codes}`；不持久化、不启监听 | M1 |
| `POST /api/proxy/profiles` | `{format, content, candidate_index, name, acknowledged_ignored_sections}`；`name` 可省略或 `null`；必需 `If-Match` | 重新解析、显式选择、保存规范化 profile 与秘密；201 返回 `{snapshot, upstream_id}` 及新 `ETag`；不自动选为出口、不应用 | M1 |
| `GET /api/proxy/capabilities` | 无 body | 明确 `draft-only`、`apply_available=false`，格式/协议/限额；不声称运行可用 | M1 |
| `GET /api/proxy/settings` | 无 body | `{revision, settings, upstreams, active, pending}` 及 `ETag: "<revision>"` | M1 |
| `PUT /api/proxy/settings` | schema 1 可写 settings；本机密码仅独立 write-only 字段；必需 `If-Match` | 保存草稿；返回相同形状的脱敏快照及新 `ETag` | M1 |
| `POST /api/proxy/apply` | `{base_revision}` | 同版本完整检查/事务应用；只有活动提交成功才表示生效 | M2 |
| `/api/proxy/diagnostics` | 后续仅已保存候选 ID、revision 与受控测试项 | 有界任务、状态、取消；不得接受任意 URL/命令 | M3 |

候选摘要精确字段为 `{index, name, protocol, server, server_port, transport, tls_mode, supported, reason_code, reason}`；支持候选的 reason 字段为空，不支持候选的原因来自受控错误码，不能带源输入。`notice_codes` 单独承载 provider 未读取等受控提醒。

PUT 可写字段精确为 `schema_version`、`local_proxy_enabled`、`relay_enabled`、`selected_upstream_id`、`local_port`、`local_username`、可选 `local_password`、`dns_policy_version`、`deny_policy_version`；不能提交服务端 refs、`local_password_set`、活动字段或任意监听地址。schema/策略版本可默认 1，其余设置在更新时明确填写。

`If-Match` 只允许一个准确的 64 位小写 hex revision（可带一对双引号），不接受 wildcard、弱 ETag 或多个值。缺失 428 `REVISION_REQUIRED`；格式错误 422 `INVALID_REVISION`；冲突 409 `PROXY_REVISION_CONFLICT`；确认集错误 422 `IGNORED_SECTIONS_ACKNOWLEDGEMENT_REQUIRED`；其余非法导入/设置 422 `UPSTREAM_OR_SETTINGS_INVALID`；私有存储不可用 503 `PROXY_STORAGE_UNAVAILABLE`。原始请求体超限返回 413。内部解析原因可用于脱敏候选，HTTP 异常不直接 stringify 原异常；不得把错误显示为配置已保存。

M1 **不注册可成功启用代理的 `/apply`**，没有运行时接线不能返回成功。诊断接口只有 M3 达到安全门槛才开放；不能用返回假成功的占位接口代替。

内部契约：`preview_upstreams(text_or_bytes, source_format)` 返回 `ImportPreview.to_dict()`；`select_upstream(text_or_bytes, selected_index, source_format)` 返回规范化 `UpstreamProfile`，后者提供 `from_dict` / `to_dict` / `redacted`。持久化 `ProxyStore.read_snapshot` / `save_draft(settings, expected_revision, local_password=...)` / `save_upstream(profile, expected_revision, profile_id=..., source_format=..., source_fingerprint=...)` 与内部 `load_draft` / `load_active` 分开；事务接口为 `begin_apply` / `commit_apply` / `abort_apply` / `recoverable_pending`。接口名称冻结不等于已实现。

## 5. 来源路由、DNS 与地址策略

### 5.1 编译顺序

编译器只接受一致的入口/设置/上游/策略快照。内部 tag 使用保留命名空间并检测碰撞，不能相信用户自定义 tag。对本次必须代理的入口按顺序：

1. 拒绝应用 UDP。
2. 拒绝字面禁止目标及自身目标。
3. 域名经明确的业务 DNS 解析。
4. 检查全部解析结果，任一禁止地址则整次拒绝。
5. 用已经检查的同一组结果经唯一海外 outbound 建立 TCP。
6. 同组入口保留兜底 reject。

中转生效时，缺路由、profile 读取失败、部分配置损坏不能让入口落到全局 `final=direct`。海外 outbound 不经本机 mixed，不允许自指 detour 或循环。设置 outbound `network=tcp` 并显式 UDP reject；前者不是系统防火墙，不证明不存在其他 UDP。

### 5.2 `dns_policy_version=1`

本节常量在 M0 冻结，M2 必须按相同常量取得固定核心检查和真实双跳证据；没有证据不开放应用，不在实现中静默换服务或退回系统 DNS。

- 业务 DNS 固定 Cloudflare DoH：连接 `1.1.1.1:443`，TLS `server_name=cloudflare-dns.com`，路径 `/dns-query`，DNS wireformat；明确 `detour=所选上游`。不依赖全局 final 的隐含效果，无普通 DNS 或 direct fallback。DNS 失败终止连接。
- 业务域名执行“先经上述 DoH 解析 → 检查全部结果 → 用同一结果拨号”，不能检查后再解析，避免 DNS rebinding。
- 上游 IP + 独立 SNI 无 bootstrap。域名上游只在显式检查/应用时使用现有系统 resolver，经独立有界 worker，最长 3 秒；它只解析上游终止地址，不接收业务目标。
- 对 bootstrap 返回的全部地址执行 public/禁止地址/自身地址检查，任一禁止则整体拒绝。结果按 IPv4 优先、同地址族规范数值升序稳定排序，选一个已检查 IP 固定写入核心 outbound，保留原独立 SNI。
- 运行中不重新解析上游主机名，不用未检查的新地址自动切换。UI 显示所固定 IP 及解析时间；动态 DNS 地址变化需要显式重新应用，这是一期限制。上游原为 IP 字面量时执行同样地址检查和固定策略。
- UI 说明：bootstrap 可能把节点主机名交给系统 resolver；Cloudflare 会接收到本次业务目标 DNS 查询，连接经海外上游。此项不等于全机/客户端 DNS 零泄漏。

Cloudflare wireformat 及 HTTPS 服务依据：[官方 DoH 请求说明](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/)。服务文档仅证明接口定义，不证明本次用户线路、固定核心或双跳已验收。

### 5.3 `deny_policy_version=1`

以下为冻结的最小明确 CIDR 集，拒绝匹配任意一项的字面 IP、bootstrap、业务 DNS 或诊断地址结果：

| 地址族 | CIDR |
| --- | --- |
| IPv4 | `0.0.0.0/8`、`10.0.0.0/8`、`100.64.0.0/10`、`127.0.0.0/8`、`169.254.0.0/16`、`172.16.0.0/12`、`192.0.0.0/24`、`192.0.2.0/24`、`192.168.0.0/16`、`198.18.0.0/15`、`198.51.100.0/24`、`203.0.113.0/24`、`224.0.0.0/4`、`240.0.0.0/4` |
| IPv6 | `::/128`、`::1/128`、`::ffff:0:0/96`、`64:ff9b::/96`、`64:ff9b:1::/48`、`100::/64`、`2001::/23`、`2001:db8::/32`、`2002::/16`、`fc00::/7`、`fe80::/10`、`ff00::/8` |

IPv6 还必须在 `2000::/3` global-unicast 范围内；IPv4-mapped/转换地址不能绕过 IPv4 或动态自身检查，以上映射/转换 CIDR 在一期直接拒绝。IPv6 scope ID 拒绝。Python/核心的 private 判定仅为额外防线，不代替明确表。

应用快照额外加入全部自身网卡地址、面板端点受控解析的全部地址和其他自身监听地址，精确 `/32` 或 `/128` 拒绝；不能只比较端口。所选上游固定 IP 也加入业务/诊断目标拒绝集合，防止业务重新指向上游自身。`169.254.0.0/16` 覆盖常见 link-local metadata；`100.64.0.0/10` 同时覆盖 `100.100.100.200`，不只防单一云厂商地址。

任何解析结果包含禁止地址时整次拒绝，不挑剩下公网结果继续。上游自身地址检查在连接前执行，导入仅语法成功不表示地址允许。禁止集合与动态自身地址快照参与编译配置身份；改变静态集合须升级策略版本并补充 IPv4/IPv6、多答复、rebinding、metadata 和自身目标负例。

生产 API、环境变量与配置中没有关闭防护的入口。私网测试例外只能由独立测试 policy 构造器注入，不能打包为生产绕过选项。M2 必须验明上述常量及实际核心结果一致，不以“待后续选表”代替安全边界。

## 6. 应用事务与失败关闭

状态机：草稿 → 已校验候选 → 应用中 → 已应用 / 失败。草稿和正在运行状态始终分别显示。

1. 持有既有操作锁，检查 `base_revision`、入口快照及所选上游未变化。
2. 生成全部配置、入口资格表和脱敏变更预览；本机端口按 TCP/UDP、IPv4/IPv6 和实际所有者检测，数据库去重不够。
3. 运行固定核心配置检查；任何诊断都绑定同一 revision。
4. 写私有候选与 journal，保留旧活动配置，不覆盖唯一恢复原件。
5. 在明确提示的短暂中断内切换；核验进程、实际绑定地址/协议/端口和来源选路。
6. 最后一次提交 `active_revision`、`effective_flags` 和 `core_revision`；此时 UI 才显示生效。
7. 重启/中断恢复只依 journal 和最后提交记录，不按草稿猜运行状态。

| 故障/操作 | 必需结果 |
| --- | --- |
| 首次 00 → 10/01/11 失败 | 保留旧已提交组合；明确开启未生效，不显示“已开但直连” |
| 中转已生效后换上游失败 | 恢复上一份已提交中转配置；旧上游也不可用则拒绝，不恢复普通出口 |
| 任意四组合切换失败 | 回到一个完整旧组合，不一半新一半旧 |
| 管理员关闭接入中转失败 | 保持原已提交中转策略，不显示已关闭 |
| 上游不可达/认证失败/进程退出 | 故障状态及拒绝，无隐式 direct 和业务 DNS 兜底 |
| 删除/停用仍被活动组合引用的上游 | 拒绝；须先显式关闭相应开关或成功切换上游 |
| 恢复失败 | 保留证据和版本，停用受影响入口并显示可恢复故障，不空建数据库或删除 journal |
| 不理解新配置的旧程序/降级 | 拒绝不安全降级，不启动旧无鉴权/普通直出配置冒充恢复 |

这些门槛同样适用于原 `CoreRuntime` 的旧配置恢复；不能因复用旧回滚代码而在已提交中转策略后退回 direct。

## 7. 面板、诊断与导出

两开关并列；任一待应用开关打开即显示上游配置，双关折叠但不删除。流程为格式选择 → 文本/小文件 → 脱敏候选 → 明确选择 → 字段映射/未导入区块确认 → 保存草稿 → 有界诊断 → 明确应用。应用前展示四组合、受影响入口、监听地址、上游摘要及连接可能短暂中断；关闭接入中转明确提示恢复普通出口。

界面分别显示待应用设置、活动 revision 和最近结果；双击、取消、刷新、后退、双标签冲突和会话过期不得导致重复应用或假成功。秘密不回显，不把可用性检查失败改成 insecure 后继续。

### 7.1 分层证据

解析/核心配置、TCP 地址、TLS/REALITY、代理认证/转发、HTTPS 业务、出口观察、DNS 路径、外部客户端完整链路分别标记 **通过 / 失败 / 未测试 / 证据不足 / 已过期**。每项绑定 revision、时间、端点和脱敏错误码。变更配置使旧结果过期。

端口监听、TCP connect、保存和重启成功不能单独证明 UUID/REALITY 认证或 HTTPS 请求成功。没有独立证据就合并说明或标证据不足，不制造“认证成功确认包”。服务器自测不能把外部客户端项变绿；出口观察仅说明该请求的源地址，不证明地理位置、整机/全部设备/全部目标都使用同一出口。

### 7.2 诊断安全硬限制

仅已保存候选 ID + revision；固定 HTTPS 目标白名单、正常 TLS、禁止重定向，不接收任意 URL、命令、网卡名。节点和目标分别执行全部地址/防重绑检查，不用于内网扫描。

`catalog_version=1` 在 M0 固定为单一 HTTPS GET `https://api64.ipify.org?format=json`；不带用户 headers、cookies 或凭据，不跟随重定向。仅接受精确 HTTP 200、符合大小限制且只含一个 `ip` 字段的 JSON 对象；该字段须为有效 IP。HTTPS 业务与观察到的出口 IP 来自同一次证据，不能伪装成两个独立测试。DNS 诊断仅查询 `api64.ipify.org`，经第 5.2 节业务 DoH，并检查其完整结果。

UI 显示 Cloudflare DNS 与 ipify 服务名，说明后者会看到本次代理出口 IP 和请求元数据。服务依据：[ipify 官方接口](https://www.ipify.org/)。目录、地址和响应要求已冻结，M3 仍须取得实际安全/核心证据后才能开放诊断；官方接口说明不等于用户链路结果。

单任务最长 30 秒，响应最多 64 KiB，每管理员最多 1 个活动任务、全局最多 2 个；取消后清理所有子进程。每管理员新任务启动间隔至少 30 秒，必须有并发/限额负例。生产不启用含密钥材料的 trace，原始核心输出不直接发浏览器。公共出口服务显示服务名称和将暴露的出口地址，不发送节点凭据、订阅令牌或访问历史。

外部客户端自测用短期关联标识匹配预期入口、上游和目标。没有独立外部观察者就保留未测试；CI loopback 同源 IP 不能冒充真实海外线路。

### 7.3 导出与普通模式

保留现有 URI/Base64、Mihomo YAML、sing-box JSON 和订阅鉴权；海外出口变更不轮换设备入口凭据。导出只描述已生效状态，不把草稿当服务器能力，不包含海外秘密。

新增严格中转完整配置时须无 DIRECT 候选、无业务 DNS 直连兜底；与既有 ToClash 分流模式并列，不替换用户原规则。VLESS URI 仅是节点资料，不能承诺设备系统 DNS、ICMP 或所有程序接管。新增 V2Ray/Xray JSON **导入**不等于新增通用 JSON 导出承诺。curl 使用显式 HTTP CONNECT 或 `socks5h`，Git 仅 HTTPS 明确代理；默认示例无真实密码，不把密码放完整命令行、shell 历史或诊断附件。

## 8. M0–M4 独立退出门槛

每阶段独立 PR、独立审查、准确候选 SHA、正常合并及准确 master SHA，后续修改不能沿用先前 SHA 的成功。各阶段代码只开放自己已达到资格的能力。

| 阶段 | 交付 | 退出条件 |
| --- | --- | --- |
| M0 合同冻结 | 本文、进度账本、模型/API/字段映射/安全上限/矩阵与拒绝语义 | 独立审查无矛盾；DNS/地址/诊断常量已冻结，后续真实检查仍未完成；四状态无先开本机依赖；文档检查、准确候选和主线证据分别记录 |
| M1 导入与数据层 | 严格三格式导入、规范化、秘密隔离、幂等迁移、草稿和冲突控制 | 两类 profile 三格式映射一致；显式选项及确认集；危险/未知/超限拒绝；升级不改原数据；无新增监听/应用 |
| M2 双开关运行与事务 | 来源路由、唯一上游、本机鉴权、DNS/地址规则、活动状态、journal 恢复 | 四状态与全部 12 条有向切换真实通过；不合格入口整体阻断；故障无 direct；已冻结 DNS/地址常量及限额的真实证据齐全 |
| M3 面板诊断与导出 | 导入向导、草稿/活动状态、分层诊断、严格导出、终端说明 | Chromium 完整生命周期通过；普通导出保留；无秘密泄漏；诊断限额/SSRF负例；外部未测如实保留 |
| M4 整体可发布候选 | 独立审查、准确候选八组、正常合并、准确主线八组、同提交四目标资产 | 下表全部必需软件项有真实证据；失败/skip/未测独立记录；版本与发布决定另行确定，无旧包冒充新实现 |

M0 的文档收口不解锁 M2 应用，也不声称 M1–M4 已完成。真实用户线路、真实小型 VPS/24 小时、生产部署和公开发布分别需要相应环境、证据与授权，不能用普通 CI 替代。

## 9. 一期验收矩阵

12 条有向切换必须逐一记录：`00→10`、`00→01`、`00→11`、`10→00`、`10→01`、`10→11`、`01→00`、`01→10`、`01→11`、`11→00`、`11→10`、`11→01`。每条既验成功，也覆盖相关跨模式失败回滚；重启恢复检查单一活动版本。

| 编号 | 必测范围 | 真实通过标准 |
| --- | --- | --- |
| A01 | 四状态、12 切换、仅接入导入、独立关闭 | 实际监听/本机出口/接入出口/活动 revision 匹配，无相互依赖或混合状态 |
| A02 | URI、JSON 单项/多项、YAML 单项/多项 × TLS/REALITY | 规范化一致；明确选择；未导入区块逐项确认；秘密不回显 |
| A03 | 编码/重复键/alias/anchor/merge/tag/深度/大输入/多文档/大整数/未知字段/provider/脚本 | 受控失败、零网络/执行、无子进程残留、资源上限在四目标成立 |
| A04 | 国内 VLESS/TCP/TLS → TLS 与 REALITY 上游；Mihomo 与 sing-box 客户端；本机 CONNECT 与 SOCKS5h | 独立各跳计数/短期标识、目标经指定上游收到请求、HTTPS 验证有效 |
| A05 | 国内错误凭据；上游 UUID、CA、SNI、REALITY 公钥/short ID/flow 错误 | 实际拒绝原因 + 目标零送达 + 无 direct；超时/EOF/旧日志本身不够 |
| A06 | 上游端口拒绝、超时、中断、DNS 失败、进程退出 | 受控失败/恢复；零业务 DNS direct fallback，已生效中转不回普通出口 |
| A07 | 字面/域名、IPv4/IPv6/mapped、多答复/rebinding、CGNAT/metadata/自身地址 | 所有禁止目标零送达；检查结果复用；bootstrap/业务 DNS 独立计数 |
| A08 | SOCKS UDP、节点 UDP、未支持入口/协议 | 明确拒绝；不能把 QUIC/DoH 传输或配置解析称为应用 UDP 已支持 |
| A09 | loopback、错误密码、外网接口、端口占用、IPv6 | 仅明确 loopback + 正确认证可用；无 wildcard listener |
| A10 | 写入/校验/启动/提交故障、并发、取消、崩溃及恢复失败 | journal/旧版本保留；一个完整活动组合；不降低已提交策略 |
| A11 | 中转后的节点增删改/恢复/启用、证书续期和订阅 | 同快照资格检查；无遗漏直出入口，不轮换现有凭据 |
| A12 | 粘贴/上传/选项/取消/双击/刷新/后退/双标签/会话过期 | 浏览器真实完整流程，草稿/活动准确，秘密不回显、不重复应用 |
| A13 | 旧库迁移、双关、仅本机、所有既有协议/导出 | 数据/端口/规则不变，无隐式新增公开监听或全局系统设置 |
| A14 | 未授权诊断、URL注入/重定向、刷测、超大响应、取消 | 同源/权限/版本/SSRF/限额有效，无内网扫描及残留子进程 |
| A15 | 四目标原生包与固定核心、旧协议/证书/安装/恢复/导出回归 | 准确提交、实际包来源/pins/组件一致；不是只跑新增单元测试 |

负例先证明受控目标本来可达；各跳与 REALITY 参考握手/伪装流量分别计数，不能用参考目标请求冒充业务送达。仅测试进程使用临时 CA/假凭据，不改主机信任；本地/CI环境错误按实际记录，不能泛化、掩盖或用跳过断言换绿。

八组沿用[贡献指南](../CONTRIBUTING.md)中的 Test V-UI、ToClash、真实 loopback、deployment、ACME、one-click、portable、documents/release contracts；须核 workflow、job、每一步和 attempt，不只看总体灯。候选、merge tree、最终 master 分别绑定证据，不预填未来 SHA。

四目标为 `x86_64-gnu`、`aarch64-gnu`、`x86_64-musl`、`aarch64-musl`，以同一准确提交的新资产和对应原生启动/组件消费结果验收。不继承旧 0.4.8 制品，不把构建成功等于实际安装或生产部署。历史失败、unchanged-code 重跑、环境阻断和首次证据不足保留；queued/running/skipped/解析检查不等于相应场景通过。

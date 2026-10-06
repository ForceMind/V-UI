# 节点、分流与客户端订阅

## 节点

从HTTPS面板登录，在“入站节点”创建节点。发布包默认新建 sing-box/VLESS/TLS、端口10443；Trojan/TCP/TLS 也已进入公开验证矩阵；先使用[兼容矩阵](COMPATIBILITY.md)列明的已验证组合，flow保持空、证书校验保持开启。

sing-box 的 VLESS/TLS、Trojan/TLS、VMess/TLS，以及当前候选 Hysteria2/TLS 的证书来源都可以选择已申请成功的正式托管证书，自动填入服务端材料并建立续期绑定；也可以手动填写证书/私钥路径与SNI。测试证书不进入可上线选择。后台仍以`material`校验结果为准，不能通过前端选择绕过域名、期限和环境检查。

创建后可以直接从节点列表点“编辑”。编辑表单由当前持久化核心配置反解，不重新生成节点：

- core / protocol 在普通编辑中锁定；要迁移协议或核心请新建节点；
- UUID、Trojan/TUIC/HY2 密码等只保留在服务端，页面只显示“已有凭据”；
- 限定 REALITY/Vision 的 UUID、私钥、short ID 均不回显；空输入保留，Chrome/direct/Vision 固定，未知导入项不会被静默清除；
- Hysteria2 Password 空输入在新建时生成、编辑时保留原密码；非空输入明确替换，已有秘密不回传；
- Hysteria2 Obfs 密码留空表示在类型不变时保留原值；这是高级草稿保留语义，obfs 仍不属于严格公开契约；
- 切换 WS/gRPC/XHTTP/RAW 时会清理旧传输块，避免残留配置冲突；
- 安全模式省略/空值时按协议默认解释：sing-box Trojan 仍使用 TLS，不能借此解绑后继续引用原托管材料；空 profile 不改变绑定。
- 托管证书绑定会回填，可明确改绑或解绑。仍使用 TLS 时，解绑必须提供不同于原托管材料的证书和私钥路径；不能只关掉续期却继续引用旧材料。
- 将安全模式改为 `none` 或 `REALITY` 会清除托管绑定，不要求无关的证书路径；取消编辑不会修改已保存绑定。切回 TLS 时需重新选择托管证书或提供手工材料。
- 保存后的核心应用若失败，期望配置与解除绑定仍会保留，并明确显示“已保存但未应用”；不要把它当作核心已生效。

刷新页面再次编辑应得到同一组非秘密参数；这也是发布浏览器验收的一部分。

创建成功与核心生效是两件事：检查“running”、候选验证结果、期望/生效修订和错误，不把HTTP请求返回或数据库保存当作节点连通。失败配置保留原运行状态；按明确错误修正后再应用。

## ToClash 工作区

“分流与订阅”直接使用固定ToClash0.3.8参考语义：

| 模式 | 默认行为 |
| --- | --- |
| 常规 | 本机/内网优先直连；自定义与服务规则后，中国大陆直连，其他交给PROXY |
| 默认直连 | 只把明确指定的服务/域名交给FORCE_PROXY，其余DIRECT |

常规模式需要客户端已有可用GeoSite/GeoIP数据；服务器不会替客户端保证远程库可下载。PROXY可以手动选择AUTO、DIRECT或节点；FORCE_PROXY不含DIRECT。相邻REJECT规则用于阻止继续匹配时落入直连，不是所有连接失败的通用回调，也不能控制未经过Mihomo的程序流量。

40项服务目录按需开关，保留原四项默认选择。服务域名列表不是完整流量发现器，共享登录/CDN可能同时被多个服务引用；关闭一个开关不等于“该服务强制直连”。自定义直连/代理、父子域覆盖、CGNAT及企业内网DNS都在预览中检查。

草稿、预览、保存分开。预览不改变订阅；保存带修订号，其他页面已经更新时返回冲突，要求重新加载，不自动覆盖。文件损坏或非法输入不能悄悄替换为默认规则。

## 专用订阅

填写名称、公开节点连接地址、明确节点范围、输出格式和有效期。**节点地址与面板地址是独立概念**，CDN面板域名不能自动当作节点服务器。当前一个订阅内的节点共用填写的连接地址；不同服务器地址应分开订阅。

创建后只显示一次新URL，保存到客户端；列表不会再次显示原始令牌。遗失时轮换，旧URL失效；撤销、过期、管理员密码改变也会使旧访问失效。订阅令牌不能调用管理API。

| 输出 | 内容 |
| --- | --- |
| Mihomo YAML | 节点、策略组、DNS和rules，适合直接导入Mihomo类客户端 |
| URI/Base64 | 节点连接信息，不包含ToClash规则 |
| sing-box JSON | 已验证的连接配置，不冒充完整Mihomo规则迁移 |

保存分流后刷新原URL即可获得最新已保存版本，不必重建令牌。客户端是否自动刷新取决于客户端设置。令牌泄露后立即撤销/轮换，禁止把含token的URL发进公开Issue或访问分析服务。


## 0.4.0 Trojan/TLS

公开 Trojan 支持严格限定为 **sing-box / Trojan / 原生 TCP / TLS / 单密码用户 / 正常证书校验**。

可直接生成：
- Trojan URI / Base64；
- 完整 Mihomo YAML；
- sing-box 客户端连接 JSON。

服务端证书路径和私钥不会进入客户端导出。多用户、禁用证书校验、未知字段以及 WS/gRPC 等未单独验收传输会明确拒绝，不通过丢参数来生成“看似可用”的配置。

## 0.4.2 VMess/TCP/TLS 基线

仅 sing-box / VMess / 原生 TCP / TLS / 单 UUID 用户已进入 v0.4.2 主线验收范围。选择 TLS 后可从托管正式证书列表选择，也可以在证书管理页面的卡片选择已有 VMess/TLS 节点绑定；编辑会回填绑定而不回传 UUID；取消不保存，修改名称/端口不重新生成 UUID。手工解绑仍使用 TLS 时必须换成两条新的手工材料路径，不能关闭续期却继续引用原托管文件。

导出使用 Mihomo YAML、VMess URI/Base64 或 sing-box JSON；不关闭证书校验，不忽略未知字段，不扩大为 Xray、WebSocket/gRPC 或 UDP 支持。准确验收范围见[兼容矩阵](COMPATIBILITY.md)。

## 0.4.3 VLESS/WebSocket/TLS 基线

此已验收基线仅针对 sing-box 1.14.2 / VLESS / WS / TLS，[PR #18 的准确主线](VLESS_WS_CLOSURE_043.md)已通过八组 CI。客户端固定为 Mihomo 1.19.32 与 sing-box 1.14.2，只新增 HTTP/TCP 验证，不宣称 WS UDP 可用。

创建或编辑 sing-box/VLESS 节点时选择 WebSocket，保持单 UUID、空 flow、TLS、明确的 SNI 与证书校验。可以选择正式托管证书或填写自己的两条服务端材料路径；Host 与证书字段相互独立。

- Path 必填，例如 `/vui-ws` 或 `/`；总长 1–256 个 ASCII 字符，以 `/` 开头，只允许字母、数字、`.`、`_`、`~`、`/`、`-`。禁止 `.` / `..` 路径段、query（如 `?ed=2048`）、fragment、百分号转义、空白或 early data。
- Host 可留空；填写时用 ASCII DNS-style 名称，例如 `edge.example.com`，总长不超过 253，每段不超过 63 个字符；每段以字母或数字开头结尾，中间可含 `-`。不要填写 `https://`、端口、路径、尾随点、IP 的方括号形式或空白。
- 可视化编辑器没有 ALPN 输入框。ALPN 限制针对原始 API / 持久化 TLS 配置中的 `tls.alpn`：字段必须省略或恰为 `["http/1.1"]`，不接受空列表、`h2` 或其他列表；WS 可视化编辑会保留已有的受支持值。可选客户端 Chrome fingerprint，其他未验收指纹不在该范围内。

### Host 与 SNI 的区别

节点连接地址决定客户端拨号到哪里；TLS SNI 指定要校验证书的服务器名称；WebSocket Host 只是 HTTP 请求中的客户端路由信息。Host 可以不同于 SNI，不会改变证书验证目标。不要用改 Host 或关闭证书校验处理 CA/SNI 错误。

V-UI 将可选 Host 保存于 `transport.headers.Host` 并保留到客户端导出，生成实际 sing-box 服务端配置时删除这个客户端字段。sing-box 1.14.2 不据此校验请求 Host；Mihomo 和 sing-box 客户端改用另一个格式合法的 Host，直连该服务端仍会被接受。这不是鉴权绕过，因为 Host 从未作为鉴权条件；凭据和 TLS 验证仍须通过。若外部反向代理需要按 Host 路由，必须自行配置，该代理/CDN 部署不在本项目自动部署范围内。

非法 Host 在可视化编译/导出阶段拒绝；格式合法但不同的 Host 接受是另一种行为。服务端仍检查配置的 WS path，错误 path 应无法转发。

### 保存、导出与证书

URI/Base64 的 `type=ws`、path、可选 Host、TLS/SNI、ALPN 和 fingerprint，与 Mihomo `ws-opts`、sing-box `transport` 对应保留；URI 序列化为 query 参数时的必要编码不表示允许在表单 path 中手填百分号转义。Mihomo WS 节点不启用 UDP，sing-box 出站限定 TCP。私钥及服务端材料路径不进入客户端输出。

未知 transport/header 字段、early-data 参数（即使显式为 0）、非空 flow、多用户、不受支持 profile、禁用 TLS 或证书校验都拒绝公开导出，不静默丢字段或改成 DIRECT。原有草稿表单不表示 Xray WS/gRPC 等已通过验收。

编辑回填 path/Host 与证书绑定，UUID 继续留在服务端，取消不保存。已有原始配置若含可视化表单无法表示的 TLS、header 或 early-data 参数，WS 编辑明确拒绝保存，不靠丢弃这些字段来完成修改。托管证书的换绑、TLS 手工解绑路径检查、续期失败保留旧材料与材料/应用状态分离均不改变。手动停止核心时续期不自动启动，保持 `CORE_STOPPED_PENDING_APPLY`，明确启动后再应用。

## 0.4.4 VLESS/gRPC/TLS 基线

本阶段只针对固定官方 sing-box 1.14.2 / VLESS / gRPC / TLS，客户端为 Mihomo 1.19.32 与 sing-box 1.14.2。当前二进制无 `with_grpc`，实际使用 gRPC Lite；[准确主线](VLESS_GRPC_CLOSURE_044.md)已完成独立审查、准确候选/主线八组 CI，最终 11 个 job 和全部步骤成功。首次失败和当时候选状态保留于[历史契约](VLESS_GRPC_044.md)。

### 创建或编辑

选择 sing-box / VLESS 节点和 gRPC 传输，填写 Service Name，保持单 UUID、空 flow、TLS、明确 SNI、正常证书验证；选择正式托管证书或提供自己的服务端证书/私钥路径。

- `service_name` 必填，类型必须为字符串，只允许 1–128 个 ASCII 字母、数字、`.`、`_`、`-`，例如 `vless.grpc_0-4.4`。大小写和字面内容原样保留；`.` 与 `..` 作为完整 service name 也合法，与 WS 禁止 dot path segment 的规则不同。
- 不填写前导 `/`、`/service/Tun`、路径、query、`%` 转义、空白或 Unicode。系统不 trim、强转数字/布尔值或将字面 service name 当路径归一化；客户端名称必须与服务端逐字相同，大小写不同亦不能转发。
- TLS ALPN 必须省略或恰为 `["h2"]`；不接受显式 `null`、空列表、`http/1.1` 或混合列表。编辑器没有 ALPN 输入框，保留已有的受支持值；该限制同时适用于原始 API / 持久化 TLS 配置。
- Chrome fingerprint 独立可选，不要求填写 ALPN 才能选用；默认、仅 Chrome、仅显式 h2、Chrome+h2 四种组合各自有前置真实验证。其他指纹不属于此已验收基线。
- 不提供 authority/Host、自定义 headers、health timer 或 multi-mode。固定服务端不实施 authority/Host 白名单；service_name 也不代替 UUID 或 TLS SNI 验证。

### 保存、导出与证书

三格式分别保留 URI/Base64 `type=grpc` / `serviceName`、Mihomo `grpc-opts.grpc-service-name`、sing-box `transport.service_name`，并保持 UUID、TLS/SNI、可选 ALPN/指纹。URI 参数序列化不代表可以在表单中手填百分号转义。此 gRPC 基线只包含 HTTP/TCP：Mihomo `udp: false`、sing-box 出站 `network: tcp`。服务端材料路径和私钥不进入公开订阅。

已有原始配置若含未支持 transport、TLS、authority/header、timer 或多模式字段，编辑拒绝保存，不通过丢字段完成无关修改。畸形导入的 service_name/flow 不能自动修正为可公开节点；需由用户明确修正受支持字段。已有 gRPC 节点缺失或畸形 UUID 时编辑返回 422，不自动生成替代凭据；省略 flow、fingerprint 或证书校验编辑字段保留原值，不能借省略解除原有拒绝条件。SNI 和相关元数据类型异常也拒绝。未知字段、不支持组合、多用户、非空 flow、禁用 TLS/证书校验的公开导出明确拒绝，不静默 DIRECT。

创建/取消/编辑/刷新/再打开/停机备份恢复沿用同一编译器和编辑器；UUID 只保存在服务端，编辑不重新生成，公开订阅仅包含连接所需凭据。托管证书回填/改绑/解绑、续期失败保留旧材料及材料/应用状态分离继续适用；手动停止核心保持 `CORE_STOPPED_PENDING_APPLY`，续期不擅自启动。

### gRPC Lite 的错误诊断限制

错误 CA/SNI 会阻止实际转发，但当前固定 Lite 客户端可能让请求等待至超时，没有及时把 x509 原因返回调用者。不能把“超时”单独当成 TLS 拒绝证明，也不能承诺立即显示详细 TLS 错误。前置测试仅为负向 sing-box 子进程开启 `GODEBUG=http2debug=1`，取得真实 unknown-CA/wrong-name x509 证据，同时检查目标无请求和无 DIRECT；未改变二进制、信任库、验证策略或生产日志默认值。排错应核对 CA、SNI、service_name 和实际日志，不通过跳过 TLS 验证或更换核心掩盖问题。

## 0.4.5 Hysteria2/TLS 基线

本基线限定 sing-box 1.14.2 服务端/客户端与 Mihomo 1.19.32，单密码、明确验证 SNI、原生 QUIC 默认值。[PR #20/#21 主线验收](HYSTERIA2_CLOSURE_045.md)已完成；前置缺日志、ACME 端口碰撞及首次 master TLS 证据失败仍保留于[历史契约](HYSTERIA2_045.md)。后续变更须重验。

### 创建或编辑

选择 sing-box / Hysteria2，保持 TLS 与证书校验，填写明确 SNI，选择正式托管证书或手工填写证书与私钥路径。节点公网地址与 TLS SNI 仍是不同字段，证书必须覆盖 SNI。

- 可选 `Hysteria2 Password` 留空时，新节点自动生成，已有节点保留原密码；填写非空密码表示明确替换。密码为 1–256 个字面字符，不 trim、不允许全空白、Unicode 控制字符（Cc）或无效 UTF-8 surrogate。
- 编辑器只显示是否已有密码，秘密不回传；取消不保存。已有 users/password 缺失或畸形会拒绝编辑，不借自动生成掩盖导入错误；缺失/禁用 TLS 的无关部分编辑也不能静默变成有效节点。
- 新 HY2 的上传/下载带宽留空、Obfs 关闭、指纹为空；不再默认注入带宽或 Chrome。不要为使用 HY2 照搬 gRPC 的 h2/Chrome 参数；严格公开配置使用固定核心原生 QUIC/ALPN 默认值。
- 原有高级带宽/obfs 草稿可保留可表达的原值及秘密，仍为未验收草稿；表单无法表达的参数必须拒绝编辑，不以静默丢字段保存。公开导出继续拒绝 obfs、port hopping、多用户、带宽/拥塞调优、ALPN（即使 null）/uTLS 覆盖、TUIC 和未知字段。

### QUIC 端口与应用负载

服务器和云/主机防火墙必须允许实际节点端口的 UDP；相同数字的 TCP 规则不会放行 UDP。当前只验 HTTP/TCP 负载通过 QUIC，Mihomo 节点固定 `udp: false`、sing-box 出站 `network: tcp`。UDP socket、QUIC 连接成功或历史 Shadowsocks UDP 证据都不构成 HY2 应用 UDP 通过。

安装器可以显式接受 `--node-udp-port 10443 --node-udp-port 20443`，范围 1024–65535；新安装双栈检查，升级提示人工核对所有权，精确端口/协议确认，不自动启用防火墙。这些声明不持久保存，后续运行须重传，不自动创建节点。详见[安装](INSTALLATION.md#2-端口和防火墙)。

### 保存、导出与证书

标准 `hysteria2://<encoded-password>@<server>:<port>/?sni=<name>&insecure=0#<name>` 对密码和显示名执行必要百分号编码，解析后须还原原密码；Mihomo YAML 与 sing-box JSON 同样保留密码、SNI 与验证状态。URI 只承载标准连接参数，不额外承诺导入客户端的应用 UDP 行为。服务端私钥和材料路径不导出，失败不退 DIRECT。

托管证书绑定/换绑、TLS 手工解绑、续期失败保留旧材料与材料/应用状态分离沿用现有流程。续期不改变密码或传输，手动停止核心保持 `CORE_STOPPED_PENDING_APPLY`，只有主动启动后应用。新浏览器验收覆盖创建/取消/编辑/刷新/再打开/秘密稳定、三格式解析和故意损坏后的停机备份/恢复；这些流程已由上述 HY2 主线验收，后续修改仍须重验。


## 0.4.6 TUIC v5/TLS

固定官方 sing-box 1.14.2 / Mihomo 1.19.32 的限定 TUIC v5 已完成准确候选/主线验收，见[TUIC 收口](TUIC_CLOSURE_046.md)；原候选记录继续作为历史。

### 创建或编辑

选择 sing-box / TUIC，保持 TLS、原生 QUIC、明确 SNI 和正常证书校验，选择正式托管证书或手工证书/私钥路径。服务端 ALPN 必须明确且恰为 `["h3"]`；新建自动写入，不能省略、null 或套用 gRPC 的 h2。拥塞保持默认 cubic、relay 保持 native、零 RTT 关闭，不添加 Chrome/带宽/heartbeat 调优。

- `TUIC UUID`、`TUIC Password` 独立处理：留空新建生成、编辑保留；非空只替换对应凭据，取消不保存
- UUID 为规范的连字符字符串，密码为 1–256 字面字符，不 trim，不允许全空白、Unicode Cc 或无效 UTF-8 surrogate；畸形已有凭据拒绝，不能靠无关编辑重新生成
- 普通列表/创建/更新不返回凭据或原始配置，编辑只显示存在标志；特权编辑可回填手工证书路径字符串，密钥内容不返回
- 导入的高级拥塞/relay/零 RTT/ALPN 参数可保留或在不可表达时拒绝；保存草稿不代表可公开导出。不静默移除未知字段、补齐缺失 h3 或把关闭验证的导入变成合格节点；已有导入缺失 TLS 时，有意修复须明确选择 TLS，而非无关的部分编辑

### 导出与 UDP 边界

三格式为完整 Mihomo YAML、sing-box JSON 和固定 Mihomo 导入器支持的 TUIC URI 约定：`tuic://<encoded-uuid>:<encoded-password>@<server>:<port>?sni=<name>&alpn=h3#<name>`。上游称此约定临时/非官方，不能称官方通用 URI 标准。凭据百分号编码且导入后还原，保留 SNI/h3/验证语义；正常验证与零 RTT 关闭使用固定导入器安全默认值，不附加虚构开关。服务端材料永不导出，失败不退 DIRECT。

仅验收 HTTP/TCP；sing-box 出站 `network: tcp`。固定 Mihomo TUIC adapter 硬编码 UDP 能力，导出省略无效的 `udp: false`，不能声称关闭 UDP。应用 UDP 未验收；QUIC 传输本身仍需要主机/云网络放行节点 UDP 端口。沿用显式可重复的 `--node-udp-port` 安装预检，不改变 TCP/UDP 分离和确认规则。

证书按 SNI 绑定，续期保留 UUID/密码/配置，失败保留旧活动材料及已应用 revision。停止核心保持 `CORE_STOPPED_PENDING_APPLY`；创建/取消/编辑/刷新/再打开/导出、续期新 QUIC 会话和故意损坏后停机恢复均须由当前最终集成提交验证，不能用裸前置替代。


## 0.4.7 REALITY / Vision 集成候选

选择 sing-box / VLESS / REALITY，固定 Direct / TCP、XTLS Vision 与 Chrome。填写独立握手参考端地址及端口、明确的 DNS-style SNI，不从参考地址猜测 SNI。参考服务必须兼容固定核心的 TLS 1.3/X25519 握手；它不是应用转发目标。不要对本测试说明使用真实第三方域名、账户或 CA。

- REALITY UUID 留空新建生成，编辑留空保持；非空只替换 UUID
- Short ID 为规范小写 16 位十六进制；留空新建生成、编辑保持，非空只替换 short ID
- X25519 私钥仅服务器保存，编辑不回显、不提供私钥输入；已有 pair 必须匹配并保持。不要在 Issue 粘贴核心配置或备份
- 显式 SNI 为字面 DNS 名称，无 scheme/port/通配符/首尾空白；Chrome 与精确 Vision flow 固定，不接受 ALPN 覆盖或其他传输/多用户/multiplex
- 不绑定托管证书，不填普通 certificate/key 路径。明确从 TLS 切换后清除绑定；取消不修改数据库，非法请求不改变节点/绑定
- 三格式保留 UUID、flow、SNI、Chrome、公钥和 short ID，不含服务端私钥或参考地址。Mihomo YAML 用 udp:false、sing-box 用 network:tcp；实际 Mihomo URI importer 自带 udp=true，不能声称 URI 强制禁用 UDP。此阶段仅 HTTP/TCP

认证失败时可能观察到参考 TLS 回落或伪装请求，这是 REALITY 设计中的独立流量；应用目标必须零送达且不能 DIRECT。成功 REALITY 验证认证证书，不能套用普通 CA 失败语义。固定核心的真实裸前置通过与最终集成状态分开，见[契约/证据](REALITY_VISION_047.md)。

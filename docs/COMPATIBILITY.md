# 兼容与验证范围

v0.4.2 的[主线验收](MAINLINE_CLOSURE_20261005.md)和 v0.4.3 的[WS 主线验收](VLESS_WS_CLOSURE_043.md)已经完成。当前 v0.4.4 VLESS/gRPC/TLS 为候选，最终独立审查与准确候选/主线八组 CI 尚待完成；以下区分继承基线、已通过的固定二进制前置链路和新集成候选。

## Linux 安装层

0.4.4 候选延续 0.3.1 的安装能力模型；选择依据是实际环境能力，不是发行版名称。

| 层级 | 目标 / 验收 |
| --- | --- |
| CPU | x86_64、ARM64 |
| libc | glibc、musl；目标包在对应原生环境启动 |
| init | systemd、OpenRC |
| Python | 固定便携 CPython 3.12 |
| 核心 | sing-box 1.14.2、Xray 26.3.27，按 CPU/libc 选择固定官方构建 |
| 防火墙 | UFW/firewalld 可在用户明确确认后修改；自定义 nftables/iptables 只提示 |
| 代表性发行版探测 | Debian、Fedora、Arch、openSUSE、Alpine |

“探测成功”不单独等于完整支持。正式支持声明要求普通测试、真实 one-click、portable matrix 和发布门槛同时通过。v0.4.2 的四目标附件已核验；任何 v0.4.4 包须通过本候选自己的验收，不能使用旧套件或前置链路结果替代。

未经适配的 NixOS、runit/s6、其他 CPU、声明式或只读系统不会被假装支持；安装器应给出检测结果并停止。

## 已验证的原生 TCP/TLS 基线

已收口的 v0.4.2 主线包含：

1. **sing-box + VLESS + TCP + TLS**：单用户、空 flow、正常证书校验、明确 SNI；可选 ALPN / Chrome client fingerprint。
2. **sing-box + Trojan + TCP + TLS**：单密码用户、正常证书校验、明确 SNI；可选 ALPN / Chrome client fingerprint。
3. **sing-box + VMess + TCP + TLS**：单 UUID 用户、明确 SNI、正常证书校验；Mihomo YAML、VMess URI/Base64 和 sing-box JSON 严格导出。

以上均有 Mihomo 和 sing-box 客户端配置检查与真实 sing-box 服务端链路。正确凭据连通、错误凭据拒绝、错误 CA/SNI 拒绝和失败不回退 DIRECT 分别验收。VMess 负向 sing-box 客户端使用可达 IP 目标，不能以目标 DNS 不可解析伪造拒绝；CA/SNI 失败包含真实 x509 错误。

[PR #15](https://github.com/ForceMind/V-UI/pull/15) 与 [PR #17](https://github.com/ForceMind/V-UI/pull/17) 保留实现历史；最终通过状态以已记录的 v0.4.2 master 工作流为准。托管正式证书、绑定/续期、浏览器创建/取消/编辑/刷新/再编辑、UUID 保留和三格式导出的证据不扩大到其他 VMess/core/transport 组合。

Mihomo 固定客户端与 ToClash 规则语义继续独立验收。

## 已验证的 Shadowsocks AEAD 基线

[PR #16](https://github.com/ForceMind/V-UI/pull/16) 的实现已合入 v0.4.2 主线，支持 sing-box Shadowsocks 的三种 AEAD cipher：`aes-128-gcm`、`aes-256-gcm`、`chacha20-ietf-poly1305`。三种都有 SIP002/Mihomo/sing-box 严格导出与固定真实客户端配置检查，method 编辑保留服务端密码。

真实 TCP/UDP 链路用例逐一覆盖上述三种 cipher，分别使用 Mihomo 和公开订阅生成的 sing-box 客户端。每种 cipher 的错误密码与错误 method 也由两种客户端验证拒绝，目标不收到数据且不 DIRECT 回退。配置检查和真实转发是分别执行的门槛；此处既有 UDP 证据不延伸至 VLESS/WS 或 VLESS/gRPC。

## v0.4.3 VLESS/WebSocket/TLS 已验证基线

[PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，准确主线八组工作流、11 个 job 和全部步骤成功；详见[WS 收口](VLESS_WS_CLOSURE_043.md)。下述是已验证范围，后续修改仍须重验。

| 项目 | 限定范围 |
| --- | --- |
| 服务端 / 客户端 | sing-box 1.14.2 / Mihomo 1.19.32 与 sing-box 1.14.2 |
| 协议与安全 | sing-box、VLESS、单 UUID、空 flow、WS、TLS、明确 SNI、正常证书校验 |
| path | 1–256 个 ASCII 字符，以 `/` 开头；只允许 `A-Z a-z 0-9 . _ ~ / -`；不含 `.` / `..` 路径段、query、fragment、百分号转义或空白 |
| 可选 Host | ASCII DNS-style 名称，总长不超过 253、各 label 不超过 63；label 以字母/数字开头结尾，中间可有 `-`；不含 scheme、port、尾随点或空白 |
| ALPN / 指纹 | 原始 API / 持久化 `tls.alpn` 省略或恰为 `["http/1.1"]`；可视化编辑器无 ALPN 输入框，保留已有受支持值；可选 Chrome fingerprint，不接受空 ALPN 列表、h2 或其他列表 |
| 导出 | VLESS URI/Base64、完整 Mihomo YAML、sing-box JSON 保留 WS/TLS/凭据；不包含私钥或服务端材料路径 |
| 链路 | HTTP/TCP；Mihomo 的 WS 导出 `udp: false`，sing-box 出站限定 `network: tcp`；不新增 UDP 支持 |

Host 是客户端路由元数据，保存在节点的 `transport.headers.Host` 并传入订阅，实际 sing-box 服务端配置会去除它。sing-box 1.14.2 不据此限制请求 Host；两种真实客户端使用另一个格式合法的 Host 仍应连通。**非法 Host 的输入拒绝与合法但不同 Host 的接受是不同测试，Host 不是访问控制，也不替代 TLS SNI/证书验证。** 如需反向代理的 Host 路由，须在外部代理另行配置；本项目不自动部署代理/CDN。

已完成门槛包括：有/无 Host、独立默认/Chrome/HTTP1.1 组合的正向链路；两种客户端分别错误 UUID、CA、SNI、path 拒绝，目标无请求且无 DIRECT 回退；不同合法 Host 的真实接受行为；浏览器创建/取消/回填/编辑/刷新/再编辑/停机恢复；证书绑定/续期、失败保留旧材料与 `CORE_STOPPED_PENDING_APPLY`。未知字段、其他 headers、early data、未支持 profile 和不满足 TLS 约束的公开导出明确拒绝。WS 可视化编辑遇到原始配置中无法表示的 TLS、header 或 early-data 参数也拒绝保存，不静默清除后导出。

详细参数和操作见[配置说明](CONFIGURATION.md#043-vlesswebsockettls-基线)。

## v0.4.4 VLESS/gRPC/TLS 候选

**最终集成候选未完成验收。** 固定二进制前置提交 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 的[真实链路工作流](https://github.com/ForceMind/V-UI/actions/runs/37288165901)通过 45 项测试；这是后续实现的前提，不能代替最终导出、浏览器、证书、安装或准确候选/主线八组 CI。

| 项目 | 限定范围 |
| --- | --- |
| 实际核心 | 未改变的官方 sing-box 1.14.2，不含 `with_grpc`，使用 gRPC Lite；不重编译或替换核心 |
| 客户端 | Mihomo 1.19.32、sing-box 1.14.2 |
| 协议与安全 | sing-box、VLESS、单 UUID、空 flow、gRPC、TLS、明确 SNI、正常证书校验 |
| service_name | 字面 `[A-Za-z0-9._-]{1,128}`；区分大小写，`.`、`..` 合法；不 trim、类型强转、路径归一化或百分号解码 |
| TLS / ALPN / 指纹 | HTTP/2；ALPN 省略或恰为 `["h2"]`，显式 null 拒绝；Chrome fingerprint 独立可选，四种组合分别验收 |
| 三格式 | URI `type=grpc` / `serviceName`、Mihomo `grpc-opts.grpc-service-name`、sing-box `transport.service_name`；保留 UUID/TLS/SNI/可选 ALPN/指纹，不输出私钥或服务器材料路径 |
| 链路 | 仅 HTTP/TCP，Mihomo `udp: false`、sing-box 出站 `network: tcp` |

前置链路的两种真实客户端均通过四种 Chrome/h2 组合和字面 `.`、`..`、128 字符 service name，实际 TLS 协商 h2。错误 UUID/CA/SNI/service name 和仅大小写差异均不送达可达 IP 目标、不退 DIRECT。首次前置 CI 的两个 sing-box CA/SNI 日志断言失败保留在[阶段记录](VLESS_GRPC_044.md)；后续只对负向测试 sing-box 子进程启用 `GODEBUG=http2debug=1`，取得真实 x509 unknown-CA/wrong-name 证据，未改变二进制或 TLS 校验。

**gRPC Lite 的错误 CA/SNI 可能向调用者表现为超时，不能承诺及时返回 TLS 错误原因。** 诊断日志证明拒绝来源，超时本身不证明正确 TLS 拒绝。实际固定服务端没有 authority/Host allowlist，本候选不建立此访问控制，不新增反向代理。

拒绝空、非字符串、空白、前导 `/`、内嵌 path/query、百分号转义、Unicode service name，以及 authority、headers、health timer、multi-mode 等未验收字段。已有导入配置包含可视化表单不能表示的选项时，编辑应拒绝而不静默丢失；畸形 service/flow 等不能因无关编辑变成可公开配置。共用编辑器保留隐藏 UUID、受支持 ALPN、托管证书与停止待应用状态。

集成验收还须覆盖公开订阅生成的配置、创建/取消/编辑/刷新/再打开/停机恢复和托管证书续期。详细参数见[配置说明](CONFIGURATION.md#044-vlessgrpctls-候选)。

## 尚未完成的协议矩阵

下列协议/组合即使已有表单或生成代码，也不能写成“已完整支持”：

- Xray VLESS WebSocket/gRPC，以及本页候选范围之外的 VLESS gRPC；
- Trojan 的 Xray 实现及 WebSocket/gRPC 等非 TCP 组合；
- Xray Shadowsocks、2022 cipher、插件/obfs，以及未列明的 cipher；
- Xray VMess 及 VMess 非 TCP/TLS 组合；
- Hysteria2、TUIC、REALITY / Vision；
- 除上述 WS 基线和 gRPC 候选外的 WebSocket/gRPC，以及 XHTTP / HTTPUpgrade 全组合；
- 除上述 Shadowsocks AEAD 之外的 UDP 专项；
- sing-box 完整 ToClash 规则迁移。

后续版本对每项都要求：服务端配置、编辑回填、URI/Mihomo/sing-box 导出、真实核心 config check、真实客户端 check、正向连接以及错误凭据/TLS/参数失败路径。

## 证书

已实现 Certbot HTTP-01 单域名申请、测试/正式隔离、自动续期和消费者绑定。WS 基线与 gRPC 候选沿用现有托管证书生命周期和停止待应用语义；gRPC 集成仍须完成本候选验收。不把 WS Host 或 gRPC service_name 当作证书域名。DNS-01、通配符和 DNS provider API 尚未纳入。

## 边界

“支持所有 Linux 发行版”的实现目标是**取消发行版品牌白名单**，依据 CPU/libc/init 等真实能力选择安全路径；不是承诺 Linux 历史上每个内核、CPU、libc 和 init 都可由同一 root 脚本自动修改。

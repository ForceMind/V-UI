# 兼容与验证范围

v0.4.2 已完成准确主线验收，证据见[收口记录](MAINLINE_CLOSURE_20261005.md)。当前 v0.4.3 VLESS/WebSocket/TLS 为候选，最终准确提交八组 CI 尚待完成；以下区分继承基线和新候选范围。

## Linux 安装层

0.4.3 候选延续 0.3.1 的安装能力模型；选择依据是实际环境能力，不是发行版名称。

| 层级 | 目标 / 验收 |
| --- | --- |
| CPU | x86_64、ARM64 |
| libc | glibc、musl；目标包在对应原生环境启动 |
| init | systemd、OpenRC |
| Python | 固定便携 CPython 3.12 |
| 核心 | sing-box 1.14.2、Xray 26.3.27，按 CPU/libc 选择固定官方构建 |
| 防火墙 | UFW/firewalld 可在用户明确确认后修改；自定义 nftables/iptables 只提示 |
| 代表性发行版探测 | Debian、Fedora、Arch、openSUSE、Alpine |

“探测成功”不单独等于完整支持。正式支持声明要求普通测试、真实 one-click、portable matrix 和发布门槛同时通过。v0.4.2 的四目标附件已核验；任何 v0.4.3 包须通过本候选自己的验收。

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

真实 TCP/UDP 链路用例逐一覆盖上述三种 cipher，分别使用 Mihomo 和公开订阅生成的 sing-box 客户端。每种 cipher 的错误密码与错误 method 也由两种客户端验证拒绝，目标不收到数据且不 DIRECT 回退。配置检查和真实转发是分别执行的门槛；此处既有 UDP 证据不延伸至 VLESS/WS。

## v0.4.3 VLESS/WebSocket/TLS 候选

**最终准确提交全组 CI 尚待完成。** 本节是实现边界和待完成的验收门槛，不是提前宣布候选通过。

| 项目 | 限定范围 |
| --- | --- |
| 服务端 / 客户端 | sing-box 1.14.2 / Mihomo 1.19.32 与 sing-box 1.14.2 |
| 协议与安全 | sing-box、VLESS、单 UUID、空 flow、WS、TLS、明确 SNI、正常证书校验 |
| path | 1–256 个 ASCII 字符，以 `/` 开头；只允许 `A-Z a-z 0-9 . _ ~ / -`；不含 `.` / `..` 路径段、query、fragment、百分号转义或空白 |
| 可选 Host | ASCII DNS-style 名称，总长不超过 253、各 label 不超过 63；label 以字母/数字开头结尾，中间可有 `-`；不含 scheme、port、尾随点或空白 |
| ALPN / 指纹 | 原始 API / 持久化 `tls.alpn` 省略或恰为 `["http/1.1"]`；可视化编辑器无 ALPN 输入框，保留已有受支持值；可选 Chrome fingerprint，不接受空 ALPN 列表、h2 或其他列表 |
| 导出 | VLESS URI/Base64、完整 Mihomo YAML、sing-box JSON 保留 WS/TLS/凭据；不包含私钥或服务端材料路径 |
| 链路 | HTTP/TCP；Mihomo 的 WS 导出 `udp: false`，sing-box 出站限定 `network: tcp`；不新增 UDP 支持 |

Host 是客户端路由元数据，保存在节点的 `transport.headers.Host` 并传入订阅，实际 sing-box 服务端配置会去除它。sing-box 1.14.2 不据此限制请求 Host；两种真实客户端使用另一个格式合法的 Host 仍应连通。**非法 Host 的输入拒绝与合法但不同 Host 的接受是不同测试，Host 不是访问控制，也不替代 TLS SNI/证书验证。** 如需反向代理的 Host 路由，须在外部代理另行配置；本候选不自动部署代理/CDN。

候选门槛包括：有/无 Host、默认/可选 Chrome + HTTP1.1 的正向链路；两种客户端分别错误 UUID、CA、SNI、path 拒绝，目标无请求且无 DIRECT 回退；不同合法 Host 的真实接受行为；浏览器创建/取消/回填/编辑/刷新/再编辑/停机恢复；证书绑定/续期、失败保留旧材料与 `CORE_STOPPED_PENDING_APPLY`。未知字段、其他 headers、early data、未支持 profile 和不满足 TLS 约束的公开导出明确拒绝。WS 可视化编辑遇到原始配置中无法表示的 TLS、header 或 early-data 参数也拒绝保存，不静默清除后导出。

详细参数和操作见[配置说明](CONFIGURATION.md#043-vlesswebsockettls-候选)。

## 尚未完成的协议矩阵

下列协议/组合即使已有表单或生成代码，也不能写成“已完整支持”：

- Xray VLESS WebSocket，以及 VLESS gRPC；
- Trojan 的 Xray 实现及 WebSocket/gRPC 等非 TCP 组合；
- Xray Shadowsocks、2022 cipher、插件/obfs，以及未列明的 cipher；
- Xray VMess 及 VMess 非 TCP/TLS 组合；
- Hysteria2、TUIC、REALITY / Vision；
- 除上述候选外的 WebSocket，以及 gRPC / XHTTP / HTTPUpgrade 全组合；
- 除上述 Shadowsocks AEAD 之外的 UDP 专项；
- sing-box 完整 ToClash 规则迁移。

后续版本对每项都要求：服务端配置、编辑回填、URI/Mihomo/sing-box 导出、真实核心 config check、真实客户端 check、正向连接以及错误凭据/TLS/参数失败路径。

## 证书

已实现 Certbot HTTP-01 单域名申请、测试/正式隔离、自动续期和消费者绑定。WS 候选沿用现有托管证书生命周期和停止待应用语义，不把 Host 当作证书域名。DNS-01、通配符和 DNS provider API 尚未纳入。

## 边界

“支持所有 Linux 发行版”的实现目标是**取消发行版品牌白名单**，依据 CPU/libc/init 等真实能力选择安全路径；不是承诺 Linux 历史上每个内核、CPU、libc 和 init 都可由同一 root 脚本自动修改。

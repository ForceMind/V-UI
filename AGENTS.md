# V-UI 开发约束

先读ROADMAP.md、docs/README.md和最近PR。用户已授权完善正式发布、所有文档、一键部署与图形化自动证书；仍不等于授权登录真实VPS、替实际域名同意CA条款或跳过验收公开Release。

- 保持每个阶段独立 PR；PR #14 仍排除。v0.4.2、WS v0.4.3、gRPC v0.4.4 和 HY2 v0.4.5 已正常合并收口。HY2 最终 master 838c66d9974dd9f3a944641a2e9e03cc200e0bbe/tree e12287d8ebbea233142d58191ee5141a0a49a17a；PR #23 安全修复最终 master 8e0d07463e59b52856f55fa33346a760a38b4705/tree fda9f9d8a26dd21bdf0e441ab5d9bbc7ec0dbdd8，两者最终八组/11 jobs/全部步骤 attempt 1 成功。详见 docs/HYSTERIA2_CLOSURE_045.md、docs/INBOUND_RESPONSE_CLOSURE_20261006.md；历史失败保留。
- TUIC v0.4.6 / PR #22 已正常合并至签名 master df8a980beb682a981d72e42760705f1831cacf9b/tree f68098e398cb7ac29e2e1e809b8f741bb3c30533。候选 14f570e8406098aa7570f2daaf24bdbe550a1858 八组/11 jobs/全部步骤 attempt 1 成功；最终 master 八组/11 个最新 jobs/全部步骤成功，其中 deployment 首次上游 GitHub 403 后以不改代码/pin/凭据的 attempt 2 通过，其余七组 attempt 1。链路 75 项、独立 HY2 8 项/TUIC 8 项和 Chromium 完整流程已验收；历史失败及本地 EPERM 保留，见 docs/TUIC_CLOSURE_046.md。
- 当前用户批准阶段为独立 v0.4.7 REALITY / Vision，基于上述 TUIC master，裸前置 a44b5ce20edcaee1ee3b26c34b7c21badfe5bf7b 已通过八组/11 jobs/全部步骤 attempt 1，链路 82 项及独立 HY2 8/TUIC 8；当前推进严格集成。首次 6776950 伪装 HEADERS 断言失败保留；不以源码刻画、config check、默认 skip 或旧主线通过代替。严格导出/编辑/浏览器/恢复集成、独立审查、准确候选八组、协调授权正常 merge 与准确主线八组按序完成，见 docs/REALITY_VISION_047.md。
- 所有最终功能在准确提交重新验证。queued/running/skip不算通过；单元、真实二进制、浏览器、systemd/安装与实际部署必须分开描述。
- 保留用户数据，不删除/改名数据库掩盖升级问题，不回退到无鉴权旧版继续在线运行。
- 当前 TUIC 只使用未改变的官方 sing-box 1.14.2 与 Mihomo 1.19.32、单 UUID/密码对、明确验证 SNI、原生 QUIC/默认拥塞、零 RTT 关闭；服务端 TLS ALPN 必须恰为 h3，不能省略。TUIC 本身不扩大多用户、v4/token 或调优/指纹；REALITY/Vision 仅按独立 v0.4.7 契约推进，不扩大 DNS-01/通配符或容器部署，不改变四目标 Linux、40 项 ToClash；不换 pin、重编译核心或加反代。
- TUIC 三格式为固定客户端支持的非官方 URI 约定、Mihomo YAML、sing-box JSON，不称官方通用 URI 标准。只验 HTTP/TCP；sing-box network:tcp。Mihomo TUIC adapter 硬编码 UDP 能力，省略无效 udp:false，不能宣称它关闭 UDP；QUIC 节点 UDP 通行不等于应用 UDP 验收。
- TUIC UUID/密码各自空输入新建生成、编辑保留，非空仅替换对应项。普通响应必须沿用 PR #23 allowlist，不回传凭据、原始配置或材料路径；特权 /editor 只保留手工证书路径字符串，明确授权导出保留客户端凭据但无服务端材料。高级导入草稿保留或拒绝不可表达项，不静默清除后公开导出。
- REALITY/Vision 仅固定官方 sing-box 1.14.2 服务端/客户端与 Mihomo 1.19.32、VLESS 直连 TCP、精确 xtls-rprx-vision、单 UUID、单规范 16 位 hex short ID、显式字面 SNI、强制 Chrome。X25519 公私钥为解码后 32 字节的无 padding base64url，校验配对并保留；私钥只留服务端。无 ALPN 覆盖、托管证书绑定、额外传输/核心或应用 UDP 扩围。URI importer 硬编码 udp=true，不声称 URI 禁用 UDP。
- REALITY 测试的本地 TLS 1.3/X25519/h2 参考握手端与 HTTP 应用目标必须独立。失败认证允许参考握手/伪装 GET，只断言应用目标零送达且无 DIRECT。假参考证书同时覆盖允许/不允许 SNI；临时 CA 仅经测试子进程 SSL_CERT_FILE 与指向空临时目录的 SSL_CERT_DIR 使用，不改主机信任、不用外部真实站点。错误 UUID/缺 flow 要求真实 unknown UUID/flow mismatch，错误合法公钥/short ID/SNI 要求真实 REALITY verification/authentication 错误，超时不够。
- REALITY 服务端 trace 可能包含派生密钥字节；默认 debug，若诊断确需 trace，仅限假凭据测试且输出须脱敏。普通响应继承 allowlist；编辑器 UUID/私钥/short ID 隐藏、空输入保留，已有未知/高级字段保留或拒绝，不静默清除后解锁公开导出。
- 首次 HY2 6daff89e 日志不足、e1800367 链路 58 项通过但 ACME DNS 端口碰撞、dbf1cfbe 首次 master 的继承 Trojan/gRPC 原因日志失败，以及 PR #21 portable 首次 API 限流/attempt 2 成功全部保留。认证/x509 真实原因、零目标请求、无 DIRECT 断言不得削弱，超时不等于拒绝证据。
- 安装器 --node-udp-port 为显式可重复的 1024–65535 UDP 声明，与 TCP 独立；仅新安装双栈 bind 探测，升级提示人工核对所有权；精确协议/端口确认、不自动启用防火墙。标志不创建节点、不持久保存，后续运行须重传。
- 客户端配置不丢关键字段，不泄露私钥，不在失败/无节点时悄悄改成DIRECT。gRPC service_name 只接受 [A-Za-z0-9._-]{1,128} 字面字符串，不 trim/强转；ALPN 省略或仅 h2，Chrome 独立可选，仅 HTTP/TCP。导入的不支持字段编辑时拒绝，不静默清除。
- gRPC Lite 错误 CA/SNI 可能让调用者超时；测试中仅 sing-box 负向子进程开启 GODEBUG=http2debug=1 取得真实 x509 证据，不宣称及时错误传播，不改生产诊断默认值或 TLS 验证。
- 新证书与应用结果分开，失败保留旧材料；不以跳过TLS校验解决签发/测试错误。
- root安装器只初始化系统，包内程序以非root用户运行；不得擅自停止原网站、修改SSH或防火墙。
- 测试只能使用假凭据、临时目录、临时CA和明确为空的CI主机。真实systemd测试不能在生产实例运行。
- 文件包不得含用户数据、token、私钥、测试CA或字体；第三方许可证和来源随包保留。
- 正式发布走人工gated workflow，精确default HEAD、八组CI最新success、已验收同一套件、无覆盖tag/Release。未实际公开就写“发布准备完成/候选”，不写“已发布”。

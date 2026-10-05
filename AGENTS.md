# V-UI 开发约束

先读ROADMAP.md、docs/README.md和最近PR。用户已授权完善正式发布、所有文档、一键部署与图形化自动证书；仍不等于授权登录真实VPS、替实际域名同意CA条款或跳过验收公开Release。

- 保持每个阶段独立PR。v0.4.2、v0.4.3 WS 和 v0.4.4 gRPC 主线已按正常 merge commit 收口，PR #14 仍排除。gRPC PR #19 准确主线 84729dfc53165003e7d459a5d56621ce89ba497c，候选 240edf23af8a12b2cbd71114fe65c693290e39f2 与合并 tree 均为 200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc；八组准确候选/主线工作流成功，最终主线 11 个 job/全部步骤通过。当前阶段为 v0.4.5 sing-box/Hysteria2/TLS 候选，独立审查、最终准确候选/主线八组仍待完成；不能复用旧主线或仅前置链路结果。历史证据见 docs/MAINLINE_CLOSURE_20261005.md、docs/VLESS_WS_CLOSURE_043.md、docs/VLESS_GRPC_CLOSURE_044.md 与原 docs/VLESS_GRPC_044.md；当前契约见 docs/HYSTERIA2_045.md。
- 所有最终功能在准确提交重新验证。queued/running/skip不算通过；单元、真实二进制、浏览器、systemd/安装与实际部署必须分开描述。
- 保留用户数据，不删除/改名数据库掩盖升级问题，不回退到无鉴权旧版继续在线运行。
- 不超出当前阶段扩大协议、DNS-01/通配符、容器部署或既有四目标 Linux / 40 项 ToClash 矩阵；旧表单不是支持承诺。v0.4.5 仅新增固定官方 sing-box 1.14.2 与 Mihomo 1.19.32 的 Hysteria2/TLS、单密码、明确验证 SNI、原生 QUIC 默认值；不放开 obfs、hopping、带宽/拥塞、ALPN/uTLS 覆盖、多用户、TUIC、REALITY/Vision，不换 pin、重编译核心或增加反代。
- HY2 准确前置 e18003670c6469489c7a63413be0a3f9bd77cf0b 的真实链路 58 项通过，但同提交 ACME 因 DNS TCP/UDP 测试端口碰撞失败；不写成前置八组全绿。保留首次 6daff89e 缺少 x509 原因日志的失败，修复不能削弱真实认证/x509、零目标请求和无 DIRECT 断言。
- HY2 仅验 HTTP/TCP 负载，Mihomo udp:false、sing-box network:tcp；QUIC 节点 UDP 通行不等于应用 UDP 支持。密码留空新建生成/编辑保留，编辑响应不回传秘密；既有高级草稿保留或拒绝不可表达项，严格公开导出拒绝调优字段，不静默清除。
- 安装器 --node-udp-port 为显式可重复的 1024–65535 UDP 声明，与 TCP 独立；仅新安装双栈 bind 探测，升级提示人工核对所有权；精确协议/端口确认、不自动启用防火墙。标志不创建节点、不持久保存，后续运行须重传。
- 客户端配置不丢关键字段，不泄露私钥，不在失败/无节点时悄悄改成DIRECT。gRPC service_name 只接受 [A-Za-z0-9._-]{1,128} 字面字符串，不 trim/强转；ALPN 省略或仅 h2，Chrome 独立可选，仅 HTTP/TCP。导入的不支持字段编辑时拒绝，不静默清除。
- gRPC Lite 错误 CA/SNI 可能让调用者超时；测试中仅 sing-box 负向子进程开启 GODEBUG=http2debug=1 取得真实 x509 证据，不宣称及时错误传播，不改生产诊断默认值或 TLS 验证。
- 新证书与应用结果分开，失败保留旧材料；不以跳过TLS校验解决签发/测试错误。
- root安装器只初始化系统，包内程序以非root用户运行；不得擅自停止原网站、修改SSH或防火墙。
- 测试只能使用假凭据、临时目录、临时CA和明确为空的CI主机。真实systemd测试不能在生产实例运行。
- 文件包不得含用户数据、token、私钥、测试CA或字体；第三方许可证和来源随包保留。
- 正式发布走人工gated workflow，精确default HEAD、八组CI最新success、已验收同一套件、无覆盖tag/Release。未实际公开就写“发布准备完成/候选”，不写“已发布”。

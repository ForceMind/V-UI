# V-UI 开发约束

先读ROADMAP.md、docs/README.md和最近PR。用户已授权完善正式发布、所有文档、一键部署与图形化自动证书；仍不等于授权登录真实VPS、替实际域名同意CA条款或跳过验收公开Release。

- 保持每个阶段独立PR。v0.4.2 和 v0.4.3 WS 主线已按正常 merge commit 收口，PR #14 仍排除；WS 准确主线为 1b3ec40cd3bb640246d12afa104db0aec08ce336，八组工作流/11 个 job/全部步骤通过。当前阶段为 v0.4.4 sing-box/VLESS/gRPC/TLS 候选，最终审查与准确候选/主线验收仍待完成；不能复用旧主线或仅前置链路结果。历史证据见 docs/MAINLINE_CLOSURE_20261005.md、docs/VLESS_WS_CLOSURE_043.md 和 docs/VLESS_GRPC_044.md。
- 所有最终功能在准确提交重新验证。queued/running/skip不算通过；单元、真实二进制、浏览器、systemd/安装与实际部署必须分开描述。
- 保留用户数据，不删除/改名数据库掩盖升级问题，不回退到无鉴权旧版继续在线运行。
- 不超出当前阶段扩大协议、DNS-01/通配符、容器部署或既有四目标 Linux 矩阵；旧表单不是支持承诺。v0.4.4 仅新增固定官方 sing-box 1.14.2 的 VLESS/gRPC Lite/TLS；不包含 Xray WS/gRPC、h2c、authority/Host 访问控制、额外 headers/timers/multi-mode、Hysteria2、TUIC 或 REALITY/Vision，不换 pin、重编译核心或增加反向代理。
- 客户端配置不丢关键字段，不泄露私钥，不在失败/无节点时悄悄改成DIRECT。gRPC service_name 只接受 [A-Za-z0-9._-]{1,128} 字面字符串，不 trim/强转；ALPN 省略或仅 h2，Chrome 独立可选，仅 HTTP/TCP。导入的不支持字段编辑时拒绝，不静默清除。
- gRPC Lite 错误 CA/SNI 可能让调用者超时；测试中仅 sing-box 负向子进程开启 GODEBUG=http2debug=1 取得真实 x509 证据，不宣称及时错误传播，不改生产诊断默认值或 TLS 验证。
- 新证书与应用结果分开，失败保留旧材料；不以跳过TLS校验解决签发/测试错误。
- root安装器只初始化系统，包内程序以非root用户运行；不得擅自停止原网站、修改SSH或防火墙。
- 测试只能使用假凭据、临时目录、临时CA和明确为空的CI主机。真实systemd测试不能在生产实例运行。
- 文件包不得含用户数据、token、私钥、测试CA或字体；第三方许可证和来源随包保留。
- 正式发布走人工gated workflow，精确default HEAD、八组CI最新success、已验收同一套件、无覆盖tag/Release。未实际公开就写“发布准备完成/候选”，不写“已发布”。

# V-UI 0.4.4

当前为 VLESS/gRPC/TLS 集成候选发布说明。最终独立审查、准确候选八组 CI、授权正常合并及准确主线八组 CI 尚待完成；不表示附件已晋升、tag、Draft Release、公开 Release 或部署已经完成。

## 继承的已验证主线

v0.4.2 以 16 个正常合并 PR 收口至 `0225ce4b1301e70068421e54c409b303d45cf812`，PR #14 排除，其八组工作流、11 个 job 与四目标 Linux 附件均已核验。更早[主线记录](MAINLINE_CLOSURE_20261005.md)和历史失败保持原样。

v0.4.3 由 [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`；准确主线八组工作流、11 个 job 和每一步全部成功，成为 VLESS/WS/TLS 已验证基线。详见[WS 收口记录](VLESS_WS_CLOSURE_043.md)。WS Host 为客户端路由元数据，服务端不执行 Host 白名单；TLS SNI/证书验证独立，不因合法但不同的 Host 被接受而失效。

FastAPI/SQLite 轻量运行、40 项 ToClash、四目标 Linux、systemd/OpenRC、完整编辑回填与托管证书保持。VLESS/TCP/TLS、Trojan/TCP/TLS、VMess/TCP/TLS、三种 Shadowsocks AEAD 和 WS 基线继续回归；Shadowsocks 的 TCP/UDP 证据不扩展到 WS 或 gRPC。继承基线未代替新候选验收，也未授权发布/部署。

## VLESS/gRPC/TLS 候选

- 保持官方 sing-box 1.14.2 服务端/客户端、Mihomo 1.19.32。现有 sing-box 没有 `with_grpc`，实际运行 gRPC Lite，不重编译、更换二进制或更改 pin。
- 仅 sing-box/VLESS/gRPC/TLS，单 UUID、空 flow、明确 SNI、正常证书验证；HTTP/2，ALPN 省略或恰为 `["h2"]`，Chrome fingerprint 独立可选。
- service_name 为 `[A-Za-z0-9._-]{1,128}` 的字面字符串，保留大小写，`.`/`..` 合法；不 trim、强转、按路径归一化，不允许前导 `/`、路径/query、百分号转义、空白或 Unicode。
- URI/Base64 `type=grpc` / `serviceName`、Mihomo `grpc-opts.grpc-service-name`、sing-box `transport.service_name` 对应保留 UUID、TLS/SNI 与可选 ALPN/指纹，不包含私钥或服务端材料路径。
- 仅 HTTP/TCP，Mihomo `udp: false`、sing-box 出站 `network: tcp`。未知 transport/TLS/authority/headers/timer/multi-mode 字段、不支持组合、多用户或关闭 TLS/验证明确拒绝；无 DIRECT 回退，无静默字段丢失。
- 沿用编译器、编辑器和托管证书；隐藏并保留 UUID、受支持 ALPN 和绑定；创建/取消/编辑/刷新/再打开/停机恢复纳入最终集成门槛。续期失败保留旧材料，手动停止核心保持 `CORE_STOPPED_PENDING_APPLY`。

## 前置证据与已知限制

准确前置提交 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 的[真实链路 CI](https://github.com/ForceMind/V-UI/actions/runs/37288165901)通过 45 项测试：双客户端四种独立 Chrome/h2 组合、实际 h2、字面 `.`/`..`/128 字符 service，以及错误 UUID/CA/SNI/service/case 拒绝；可达 IP 目标无失败请求、无 DIRECT 回退。

首次前置 `008608a` 的 sing-box CA/SNI 日志断言失败仍保留在[阶段契约](VLESS_GRPC_044.md)。后续仅负向测试 sing-box 子进程开启 `GODEBUG=http2debug=1`，取得真实 x509 unknown-CA/wrong-name 证据；没有改变核心、TLS 校验或系统信任。

实际 gRPC Lite 在错误 CA/SNI 时可能让调用者等到超时，不能保证及时返回 TLS 原因；诊断日志暴露了错误，未修复或声称解决错误传播限制。固定服务端不提供 authority/Host allowlist，本候选也不建立此访问控制。

## 边界与发布

不包含 Xray gRPC/WS、h2c、VMess/Trojan gRPC、REALITY/Vision、Hysteria2、TUIC、UDP 扩展、额外 headers/timers/multi-mode 或反向代理；不引入新框架、依赖、迁移或核心构建。完整限制见[兼容矩阵](COMPATIBILITY.md)和[配置指南](CONFIGURATION.md)。

前置裸核心通过不等于最终公开导出、真实链路、浏览器、证书、安装/恢复与四目标套件通过。本候选仍须按[发布检查](RELEASING.md)完成独立审查及准确候选/主线全部门槛，正式公开另需人工发布。本阶段没有晋升当前附件；历史 v0.4.2 套件不替换、不重建。未发布前不承诺在线版本安装命令可用。

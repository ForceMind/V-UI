# V-UI 0.4.2

当前为候选发布说明；不表示 GitHub Release 已公开，也不表示已部署。

本版维护既有 sing-box VMess/TCP/TLS 候选，继承 VLESS/TCP/TLS、Trojan/TCP/TLS、三种 Shadowsocks AEAD 的验收与 Linux 安装、节点编辑修复；不新增协议或传输组合。

## VMess/TCP/TLS

- strict public export 仅接受单 UUID 用户、原生 TCP、TLS、明确 SNI 与正常证书校验；Mihomo YAML、VMess URI/Base64 和 sing-box JSON 保留 UUID/TLS 参数，服务端材料路径不进入导出。
- 入站表单为 sing-box/VMess/TLS 显示已验证的正式托管证书选择，与现有 API、绑定及续期能力一致；其他 core/protocol 和非 TLS 安全模式不因此放开选择。
- 前端条件/API 正反回归及真实 Vue/Chromium 用例覆盖创建、取消、编辑、刷新、再编辑、UUID 不回显且不改变、三格式导出及停机恢复。
- 固定真实 sing-box 服务端 + Mihomo 与公开订阅 sing-box 客户端正向链路；错误 UUID、错误 CA、错误 SNI 拒绝与无 DIRECT 回退用例分别使用 Mihomo 和 sing-box，CA/SNI 失败要求真实 x509 错误证据。不同证据层级单独记录，不把配置检查当作真实链路。
- 托管证书续期保持核心手动停止状态，不擅自启动；沿用新的材料和应用结果分离语义。

## 已继承的 Shadowsocks 验证

`aes-128-gcm`、`aes-256-gcm`、`chacha20-ietf-poly1305` 均有严格导出、固定真实配置检查，以及两个客户端逐 cipher 的 TCP/UDP 正向与错误密码/method 拒绝测试；失败目标无数据且不 DIRECT 回退。

## 边界

Xray VMess、VMess WebSocket/gRPC、REALITY/Vision、Hysteria2、TUIC、未列明 cipher/传输和其他协议 UDP 专项仍未纳入本候选验证范围。40 项 ToClash 服务目录、四目标 Linux、systemd/OpenRC 范围不变。

最终验收必须以当前准确提交八组工作流为准；正式发布还需要处理前置依赖、默认分支 exact-head 重验与人工发布。未发布前不承诺在线版本安装命令可用。

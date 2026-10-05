# V-UI 0.4.3

当前为 VLESS/WebSocket/TLS 候选发布说明，最终准确提交八组 CI 尚待完成；不表示 Draft Release、版本 tag、公开 Release 或部署已经完成。

## 继承的 v0.4.2 主线

v0.4.2 已以 16 个正常合并 PR 收口至 `master` `0225ce4`（PR #14 排除），八组工作流 / 11 个 job 与四目标 Linux 附件均核验成功，当时未创建 Draft Release、v0.4.2 tag、公开 Release 或部署。[主线收口记录](MAINLINE_CLOSURE_20261005.md)保留准确提交、tree 和 CI 链接；不能复用此结果作为 v0.4.3 通过。

FastAPI/SQLite 轻量运行、40 项 ToClash 服务目录、四目标 Linux、systemd/OpenRC、完整编辑回填与托管证书能力保持。VLESS/TCP/TLS、Trojan/TCP/TLS、VMess/TCP/TLS 和三种 Shadowsocks AEAD 的既有基线继续回归；Shadowsocks 的双客户端 TCP/UDP 证据不扩展至新 WS 候选。

## VLESS/WebSocket/TLS

- 固定 sing-box 1.14.2 服务端，Mihomo 1.19.32 / sing-box 1.14.2 客户端；仅 sing-box / VLESS / WS / TLS / 单 UUID / 空 flow / 明确 SNI / 正常证书校验。
- path 仅接受 1–256 个 ASCII 字符，以 `/` 开头，字符集为字母、数字、`.`、`_`、`~`、`/`、`-`；拒绝 `.` / `..` 路径段、query、fragment、百分号转义与空白。
- 可选 Host 仅接受 ASCII DNS-style 名称，总长 ≤253、各 label ≤63；不含 scheme、port、尾随点。`transport.headers.Host` 是客户端路由元数据，生成实际服务端配置时去除。sing-box 不限制请求 Host，不同合法 Host 会被接受；它不是白名单，不代替 TLS SNI/证书验证。
- ALPN 省略或仅 `http/1.1`，可选 Chrome fingerprint。URI/Base64、Mihomo YAML、sing-box JSON 保留 WS/TLS/凭据，无服务端路径或私钥；未知字段、其他 header、early data 及不支持 profile 明确拒绝。
- 本次只新增 HTTP/TCP 验证：候选门槛覆盖有/无 Host、默认/可选参数正向、两个客户端分别错误 UUID/CA/SNI/path 拒绝及无 DIRECT 回退；不同合法 Host 接受与非法 Host 拒绝分开刻画。
- 浏览器和证书门槛覆盖创建、取消、编辑回填、刷新、再编辑、UUID 保留、三格式导出、停机恢复、绑定及续期失败保护；手动停止核心保持 `CORE_STOPPED_PENDING_APPLY`，续期不擅自启动核心。

## 边界与发布

Xray WS、gRPC、VMess/Trojan WS、REALITY/Vision、Hysteria2、TUIC、early data 和新 UDP 范围不纳入本候选。完整限制见[兼容矩阵](COMPATIBILITY.md)与[配置指南](CONFIGURATION.md)。

配置检查、真实链路、浏览器、证书、安装/恢复与四目标包须分别通过准确提交的门槛。最终验收待本候选 exact-head 八组工作流完成；正式发布另需默认分支准确 HEAD 重验与人工发布，不能凭版本字符串或部分测试通过提前宣布。未发布前不承诺在线版本安装命令可用。

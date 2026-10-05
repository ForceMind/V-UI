# V-UI 0.4.1

当前为候选发布说明；不表示 GitHub Release 已公开，也不表示已部署。

本版继承 VLESS/TCP/TLS、Trojan/TCP/TLS 与已验收的 Linux 安装和托管证书编辑修复，维护既有 sing-box Shadowsocks AEAD 候选，不增加其他协议。

## Shadowsocks 的准确验证范围

- strict public export 支持 `aes-128-gcm`、`aes-256-gcm`、`chacha20-ietf-poly1305`；SIP002 URI、Mihomo YAML 和 sing-box JSON 保留 method/密码，拒绝未知字段、未知 method、空密码及 transport/TLS 残留。
- 三种 method 均有固定 Mihomo / sing-box 的真实配置检查；编辑 method 时保留服务端密码，不回传浏览器。
- 真实链路用例逐一覆盖上述三种 method：sing-box 服务端 + Mihomo 和公开订阅 sing-box 客户端的 TCP/UDP 转发；错误密码与错误 method 均拒绝，目标收不到数据且不 DIRECT 回退。
- 每种 method 的错误密码和错误 method 均由两个真实客户端分别验证 TCP/UDP 拒绝；最终证据必须来自本候选准确提交的八组验收。

## 边界

Xray Shadowsocks、2022 cipher、插件/obfs、VMess、Hysteria2、TUIC、REALITY/Vision 和未列明传输组合仍未纳入本候选的已验证链路范围。Shadowsocks UDP 证据不扩展到其他协议。

保留 40 项 ToClash 服务目录、x86_64/ARM64 × glibc/musl、systemd/OpenRC 范围。正式 Release 必须等待依赖按序处理、默认分支 exact-head 八组验收及人工发布，不将候选版本号当作可用下载承诺。

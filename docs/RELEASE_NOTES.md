# V-UI 0.4.0

当前为候选发布说明；不表示 GitHub Release 已公开，也不表示已部署。

本版在 0.3.1 Linux 可移植安装和 0.3.2 节点可逆编辑基础上，新增首个第二协议公开验证矩阵：**sing-box Trojan/TCP/TLS**。

## 新增

- Trojan URI/Base64、完整 Mihomo YAML、sing-box 客户端连接 JSON。
- 单密码用户 + TLS/SNI + 正常证书验证 strict export。
- 托管证书选择、证书页面绑定和自动续期支持 Trojan/TLS。
- 固定真实 sing-box 服务端 + Mihomo 客户端验证：正确密码连通；错误密码、错误 CA、错误 SNI 均拒绝且不得回退 DIRECT。

## 仍然明确拒绝

Xray Trojan、Trojan WebSocket/gRPC、Shadowsocks、VMess、Hysteria2、TUIC、REALITY/Vision 等尚未完成各自真实矩阵的组合不会因为表单或草稿生成器存在就被宣称支持。

Linux 安装仍按 x86_64/ARM64 × glibc/musl、systemd/OpenRC 的 0.3.1 验收范围执行。正式 Release 只有 exact-head 全套工作流成功后才可由人工发布流程晋升。

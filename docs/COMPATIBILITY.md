# 兼容与验证范围

## Linux 安装层

0.4.0 延续 0.3.1 的安装选择依据是实际环境能力，不是发行版名称。

| 层级 | 目标 / 验收 |
| --- | --- |
| CPU | x86_64、ARM64 |
| libc | glibc、musl；目标包在对应原生环境启动 |
| init | systemd、OpenRC |
| Python | 固定便携 CPython 3.12 |
| 核心 | sing-box 1.14.2、Xray 26.3.27，按 CPU/libc 选择固定官方构建 |
| 防火墙 | UFW/firewalld 可在用户明确确认后修改；自定义 nftables/iptables 只提示 |
| 代表性发行版探测 | Debian、Fedora、Arch、openSUSE、Alpine |

“探测成功”不单独等于完整支持。正式支持声明要求普通测试、真实 one-click、portable matrix 和发布门槛同时通过。

未经适配的 NixOS、runit/s6、其他 CPU、声明式或只读系统不会被假装支持；安装器应给出检测结果并停止。

## 已验证代理基线

当前公开导出与真实端到端连接已验证两条配置：

1. **sing-box + VLESS + TCP + TLS**：单用户、空 flow、正常证书校验、明确 SNI；可选 ALPN / Chrome client fingerprint 已验证。
2. **sing-box + Trojan + TCP + TLS**：单密码用户、正常证书校验、明确 SNI；可选 ALPN / Chrome client fingerprint 已验证。

两者均通过 Mihomo 和 sing-box 客户端配置检查。真实 loopback 验证正确凭据连通、错误凭据拒绝、错误 CA/SNI 拒绝，并确认失败不会回退 DIRECT。

Mihomo 固定客户端与 ToClash 规则语义继续独立验收。

## 尚未完成的协议矩阵

下列协议/组合即使已有表单或生成代码，也不能写成“已完整支持”：

- Trojan 的 Xray 实现及 WebSocket/gRPC 等非 TCP 组合；
- Shadowsocks；
- VMess；
- Hysteria2；
- TUIC；
- REALITY / Vision；
- WebSocket / gRPC / XHTTP / HTTPUpgrade 全组合；
- UDP 实际转发；
- sing-box 完整 ToClash 规则迁移。

后续版本对每项都要求：服务端配置、编辑回填、URI/Mihomo/sing-box 导出、真实核心 config check、真实客户端 check、正向连接以及错误凭据/TLS/参数失败路径。

## 证书

已实现 Certbot HTTP-01 单域名申请、测试/正式隔离、自动续期和消费者绑定。DNS-01、通配符和 DNS provider API 尚未纳入。

## 边界

“支持所有 Linux 发行版”的实现目标是**取消发行版品牌白名单**，依据 CPU/libc/init 等真实能力选择安全路径；不是承诺 Linux 历史上每个内核、CPU、libc 和 init 都可由同一 root 脚本自动修改。

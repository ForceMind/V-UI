# V-UI 开发路线

## v0.3.0 基线

认证、只读订阅、安全核心应用/恢复、ToClash 分流、真实 VLESS/TCP/TLS 链路、Certbot HTTP-01 生命周期和首版 systemd 一键安装已经形成基线。

## v0.3.1 — Linux 可移植安装与发布

PR #12。

交付门槛：

1. 发行版、CPU、libc、init、package manager 自动检测。
2. x86_64/ARM64 × glibc/musl 四目标固定运行包。
3. sing-box/Xray 按目标选择固定官方二进制。
4. systemd/OpenRC 服务后端。
5. TCP 80、面板、默认节点端口占用检查。
6. UFW/firewalld 仅在用户明确 `yes` 后开放；自定义 nftables/iptables 与云安全组等待人工确认。
7. Ubuntu/systemd 真实 sudo 一键安装与升级回归。
8. ARM64、musl 原生构建/启动，Debian/Fedora/Arch/openSUSE/Alpine 代表性探测。
9. 正式 Release 聚合同一 exact-head 已验收的四个目标包；在线安装脚本自动选择 target。

## v0.3.2 — 节点完整编辑与回填

PR #13。

- 从现有 `settings / stream_settings / _vui` 反解统一 visual profile。
- 新建/编辑共用 schema 与编译器。
- UUID、密码、Reality 私钥、Obfs 密码等秘密不因编辑而重新生成，也不为编辑便利回传浏览器。
- 核心/协议迁移与普通编辑分离。
- 证书绑定状态回填。
- 浏览器验收覆盖：创建 → 编辑 → 刷新 → 再编辑 → 导出。

## v0.4.x — 协议矩阵逐项完成

顺序：

1. Trojan + TLS — **v0.4.0 / PR #15**：sing-box TCP/TLS strict export、真实 Mihomo/sing-box 链路与证书续期；
2. Shadowsocks — 下一版本；
3. VMess + TLS；
4. VLESS WebSocket / gRPC；
5. Hysteria2；
6. TUIC；
7. REALITY / Vision；
8. XHTTP / HTTPUpgrade；
9. UDP / DNS 专项。

每一个版本都必须同时完成服务端、编辑 UI、URI/Mihomo/sing-box 导出、真实核心检查、真实客户端检查、正向连接和错误凭据/TLS/参数失败路径。

## 后续

DNS-01/通配符、核心自动升级/回滚、任意 YAML 导入合并、容器专用部署、NixOS/其他声明式系统专用模块继续作为独立版本。

所有状态继续区分：代码已写、单测通过、真实二进制通过、真实链路通过、已发布、已部署。

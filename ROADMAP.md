# V-UI 开发路线

## 当前状态

v0.4.2 已在 2026-10-05 以 16 个正常 merge commit 收口到 `master` 的 `0225ce4`，PR #14 明确排除。准确主线的八组工作流、11 个 job 和四目标 Linux 附件均已核验；当时未创建 Draft Release、版本 tag、公开 Release 或部署。完整提交、tree 和验收链接见[主线收口记录](docs/MAINLINE_CLOSURE_20261005.md)。

当前独立版本为 **v0.4.3 VLESS/WebSocket/TLS 候选**，最终 exact-head 全组 CI 尚待完成；旧主线通过不自动认可后续源码或附件。

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
2. Shadowsocks — **v0.4.1 / PR #16（已合并至 v0.4.2 主线）**：三种 AEAD 导出/真实配置检查，逐 cipher 双客户端 TCP/UDP 链路与负向拒绝；
3. VMess + TLS — **v0.4.2 / PR #17（主线验收已完成）**：strict export、真实 Mihomo/sing-box 链路、错误 UUID/CA/SNI；
4. VLESS WebSocket — **v0.4.3（当前候选，最终 CI 待完成）**；gRPC 另阶段验收，不随 WS 放开；
5. Hysteria2；
6. TUIC；
7. REALITY / Vision；
8. XHTTP / HTTPUpgrade；
9. UDP / DNS 专项。

每一个版本都必须同时完成服务端、编辑 UI、URI/Mihomo/sing-box 导出、真实核心检查、真实客户端检查、正向连接和错误凭据/TLS/参数失败路径。

### v0.4.3 的边界与门槛

- 固定 sing-box 1.14.2 服务端，Mihomo 1.19.32 / sing-box 1.14.2 客户端；VLESS、单 UUID、空 flow、TLS、明确 SNI、正常证书校验。
- WebSocket path 为 1–256 个 ASCII 字符、以 `/` 开头，只接受字母、数字、`.`、`_`、`~`、`/`、`-`；禁止 `.` / `..` 路径段、query、fragment、百分号转义、空白与 early data。
- Host 可省略；填写时只接受最长 253 字符、每段最长 63 字符的 ASCII DNS-style 名称，不含 scheme、port 或尾随点。它是保存在 `transport.headers.Host` 的客户端路由信息，生成实际 sing-box 服务端配置时去除；服务端不校验请求 Host。TLS SNI/证书验证保持独立。
- ALPN 省略或仅 `http/1.1`，可选 Chrome fingerprint；只新增 HTTP/TCP 验证，不扩大 UDP。
- 三格式保留 WS/TLS/凭据；未知字段、early data、不支持 profile 明确拒绝，不能静默 DIRECT 或泄露私钥/服务端材料路径。
- 双客户端分别验正确链路与错误 UUID/CA/SNI/path 拒绝；不同合法 Host 可连通是实际核心行为，不能把它写成拒绝用例；非法 Host 应在输入/导出校验阶段拒绝。
- 浏览器覆盖创建、取消、编辑回填、刷新、再编辑、恢复及 UUID 不变；证书覆盖绑定、续期、失败保留旧材料和 `CORE_STOPPED_PENDING_APPLY`。既有 FastAPI/SQLite、40 项 ToClash、四目标 Linux 与发布边界保留。
- Xray WS、gRPC、Hysteria2、TUIC、REALITY/Vision 等不纳入本阶段。所有验收以新候选准确提交为准，不预先宣告通过。

## 后续

DNS-01/通配符、核心自动升级/回滚、任意 YAML 导入合并、容器专用部署、NixOS/其他声明式系统专用模块继续作为独立版本。

所有状态继续区分：代码已写、单测通过、真实二进制通过、真实链路通过、已发布、已部署。

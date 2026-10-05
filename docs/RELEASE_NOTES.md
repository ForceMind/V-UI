# V-UI 0.4.5

当前为 Hysteria2/TLS 集成候选发布说明，[Draft PR #20](https://github.com/ForceMind/V-UI/pull/20)。最终独立审查、准确候选八组 CI、授权正常合并及准确主线八组 CI 尚待完成；不表示附件晋升、tag、Draft Release、公开 Release 或部署已经完成。

## 继承的已验证主线

v0.4.2 以 16 个正常合并 PR 收口至 `0225ce4b1301e70068421e54c409b303d45cf812`，PR #14 排除；八组工作流、11 个 job 和四目标 Linux 附件已核验。[原记录](MAINLINE_CLOSURE_20261005.md)与历史失败保持原样。

v0.4.3 的 [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，八组工作流、11 个 job 和每一步通过，成为 [WS 已验证基线](VLESS_WS_CLOSURE_043.md)。Host 仍只是客户端路由信息，不是服务端白名单，也不代替 TLS SNI/证书验证。

v0.4.4 的 [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并至 `84729dfc53165003e7d459a5d56621ce89ba497c`；准确候选 `240edf23af8a12b2cbd71114fe65c693290e39f2` 与合并 tree 均为 `200f8b61ac6decc4fb11384c5d8d162f1f1bdcdc`。独立审查、八组 exact-head 和八组 exact-master 成功，最终 11 个 job 和每一步均通过，成为 [gRPC Lite 已验证基线](VLESS_GRPC_CLOSURE_044.md)。首次失败、前置和候选历史保留在[原阶段契约](VLESS_GRPC_044.md)。错误 CA/SNI 的 Lite 调用者仍可能超时，真实 x509 诊断不等于及时错误传播。

FastAPI/SQLite、40 项 ToClash、四目标 Linux、systemd/OpenRC、完整编辑回填与托管证书保持。TCP/TLS、三种 Shadowsocks AEAD、WS 和 gRPC 基线继续回归；其通过不代替 HY2 新候选验收，也不授权发布/部署。

## Hysteria2/TLS 候选

- 不改变官方 sing-box 1.14.2 服务端/客户端、Mihomo 1.19.32 的 pin、摘要或构建。
- 仅 sing-box/Hysteria2/TLS、单密码、明确 SNI、正常证书验证、原生 QUIC 默认值；没有 obfs、hopping、多用户、带宽/拥塞、ALPN/uTLS 覆盖、零 RTT 扩展或 TUIC。
- 标准 `hysteria2://` URI 使用百分号编码密码与 `sni` / `insecure=0`；Mihomo YAML 和 sing-box JSON 对应保留密码及 TLS 校验，不含私钥或服务端材料路径。
- 仅 HTTP/TCP 负载，Mihomo `udp: false`、sing-box `network: tcp`。QUIC 要求节点 UDP 端口可达，不能由 TCP 放行替代；这不承诺应用 UDP 转发。
- 新建 HY2 不默认写入带宽或 Chrome。可选 `Hysteria2 Password` 空输入新建生成/编辑保留，非空明确替换；编辑响应不回传秘密。
- 高级调优/obfs 遗留草稿保留或在不可表达时拒绝；严格公开导出继续拒绝，不通过静默丢字段使其变成“可用”，失败不退 DIRECT。
- 共用正式托管证书与编辑器；续期保留凭据/配置，失败保留旧活动材料，停止核心保持 `CORE_STOPPED_PENDING_APPLY`。真实 Chromium 创建/取消/编辑/刷新/再打开/导出/故意损坏后的停机恢复是待验收的新门槛。

## 显式 UDP 安装声明

新增可重复 `--node-udp-port 10443 --node-udp-port 20443`，值为 1024–65535，独立于现有 `--node-port` TCP。新安装检查 IPv4 与可用 IPv6 UDP bind 冲突；既有安装/升级跳过此占用探测并提示人工核对所有权，不能当作端口空闲证明。

UFW/firewalld 与云/自定义规则确认按精确端口/协议列出，不自动启用主机防火墙。声明不持久保存，后续安装/升级如需检查必须重传；不会创建节点或开启应用 UDP。四 Linux 目标和原 TCP 路径保持，见[安装指南](INSTALLATION.md)。

## 前置证据与失败历史

准确前置 `e18003670c6469489c7a63413be0a3f9bd77cf0b` 的[真实链路 CI](https://github.com/ForceMind/V-UI/actions/runs/37293701022)通过 58 项：双客户端实际 HTTP 转发、错误密码/CA/SNI、真实 `authentication failed` 与 x509、可达 IP 正向控制、负向零目标送达和无 DIRECT。

首次 `6daff89e562c93c77b797aa27131b1a438e654a9` 因 Mihomo HY2 错误 CA、继承 gRPC 错误 SNI 原因日志缺失而失败，仍保留于[HY2 契约](HYSTERIA2_045.md)。修复只在有界观察期内重试失败请求，每次仍要求零送达，最后必须出现真实原因，没有换核、跳过 TLS 或削弱断言。

第二前置的 [ACME 工作流](https://github.com/ForceMind/V-UI/actions/runs/37293701064)仍因继承 DNS TCP/UDP 测试端口碰撞失败，夹具预留修复已通过 4 项本地回归，准确候选 ACME 重跑仍待完成；**不声称前置八组全绿，也不声称最终集成通过**。公开导出/真实链路、证书、浏览器、安装和四目标套件都须在最终准确提交重验。

固定 Mihomo 的错误密码请求可到 3 秒期限后向调用者超时，同时日志有真实 `authentication failed`；不承诺立即返回结构化认证错误，也不把超时本身当作拒绝证据。

## 发布边界

本候选须按[发布检查](RELEASING.md)完成独立审查、准确候选/主线全部门槛，正式公开另需人工发布。当前源码、CI 原始附件与发布准备是不同状态；本阶段未晋升附件、创建 tag/Release 或部署。历史套件不替换、不重建。版本安装 URL 只有确实公开发布后才可用。

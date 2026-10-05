# v0.4.5 Hysteria2/TLS 验收契约

## 当前状态

当前为独立 [Draft PR #20](https://github.com/ForceMind/V-UI/pull/20) 的 Hysteria2/TLS 集成候选。继承基线为 [PR #19](https://github.com/ForceMind/V-UI/pull/19) 正常合并后的 `84729dfc53165003e7d459a5d56621ce89ba497c`；该 gRPC 准确主线八组工作流、11 个 job 和全部步骤已通过，详见[gRPC 收口](VLESS_GRPC_CLOSURE_044.md)。这些结果不构成 HY2 证据。

固定二进制前置 `e18003670c6469489c7a63413be0a3f9bd77cf0b` 的[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37293701022)通过全部 58 项测试。两种客户端都实际通过原生 QUIC 转发 HTTP，错误密码/CA/SNI 各自拒绝，保留真实 `authentication failed` / x509 证据、可达 IP 正向控制、零目标请求及无 DIRECT 断言。此结果仅证明该准确前置提交的真实链路。

同一第二次前置的 [ACME 运行](https://github.com/ForceMind/V-UI/actions/runs/37293701064)因继承测试夹具的 DNS TCP/UDP 端口碰撞失败，夹具预留修复已通过 4 项本地回归，准确候选 ACME 重跑仍待完成；**不能声称该前置八组全绿**。公开导出、编辑器、证书和 UDP 安装预检正在集成；最终独立审查、准确集成候选八组 CI、授权正常合并和准确主线八组 CI 仍待完成。未创建 tag、Draft Release、公开 Release、生产部署或晋升当前附件。

## 限定公开契约

- 固定官方 sing-box 1.14.2 服务端/客户端及 Mihomo 1.19.32，原 pin、摘要、构建不变；不通过换核、重编译或反代掩盖不兼容
- 仅 sing-box、Hysteria2、单密码、显式 SNI、正常证书验证、原生 QUIC 默认值；不加入 ALPN/uTLS 指纹、带宽或拥塞覆盖
- 密码是 1–256 个字面字符，不能全为空白、含 Unicode 控制字符（Cc）或不能编码为 UTF-8 的 surrogate；保留特殊字符并在 URI 序列化时百分号编码，不 trim 或丢失凭据
- 标准 `hysteria2://<encoded-password>@<server>:<port>/?sni=<name>&insecure=0#<name>`，Mihomo YAML 和 sing-box JSON 对应保留密码、SNI 与验证语义；不输出服务器私钥、证书路径或密钥路径
- 仅 HTTP/TCP 应用负载通过 QUIC；Mihomo `udp: false`，sing-box 出站 `network: tcp`。URI 遵循标准格式，但不额外承诺导入客户端的应用 UDP 行为
- QUIC 自身要求节点 UDP 监听与主机/云防火墙通行；TCP 放行不能代替 UDP，UDP socket 也不能证明应用 UDP 转发通过
- 不含 obfs、port hopping、多用户、masquerade、realm、TUIC、额外带宽/拥塞调优或零 RTT 扩展；公开导出拒绝这些和未知字段，不静默丢参数或退 DIRECT
- 既有高级调优/obfs 草稿可在可表达范围内保留，不因此成为已验收公开节点；无法由编辑器表达的导入字段拒绝编辑，不能静默抹去秘密或改变原值
- 四目标 Linux、40 项 ToClash、轻量 FastAPI/SQLite、依赖及核心来源不变；无真实 CA 账户、生产凭据或实际主机防火墙操作

官方协议参考：[URI 规范](https://v2.hysteria.network/docs/developers/URI-Scheme/)、[sing-box 服务端](https://sing-box.sagernet.org/configuration/inbound/hysteria2/)、[sing-box 客户端](https://sing-box.sagernet.org/configuration/outbound/hysteria2/)、[Mihomo Hysteria2](https://wiki.metacubex.one/en/config/proxies/hysteria2/)。文档只作设计依据，支持边界以固定二进制实际测试为准。

## 已观测的错误呈现限制

固定 Mihomo 错误密码测试中，调用者可在 3 秒请求期限后得到超时，同时真实核心日志记录 `authentication failed`。这证明密码被拒绝，不承诺立即向调用者返回结构化认证错误；同样不能只靠超时或零目标请求推断拒绝原因。每次负向仍须实际原因日志、零目标送达和无 DIRECT。

## 编辑器与托管证书

新建 HY2 默认不注入 `up_mbps`、`down_mbps` 或 Chrome 元数据。可选 `Hysteria2 Password` 留空时，新节点自动生成密码；已有节点保留原密码。非空输入明确替换密码；编辑响应只返回是否已有密码和空输入，不回传秘密。已有 users/password 缺失或畸形不能靠编辑自动生成替代；缺失/禁用 TLS 的无关部分编辑不能静默修复原始导入。不要把“空字符串留空”写成“任意空白字符会自动生成”，全空白非空输入应拒绝。

共用正式托管证书选择/绑定和安全应用流程，依据 TLS SNI 校验证书。续期保持密码和节点配置，失败保留旧活动材料与已应用 revision；手动停止核心后仍是 `CORE_STOPPED_PENDING_APPLY`，不得自动启动。浏览器门槛新增创建、取消、修改、刷新、再打开/回填、三格式解析导出、隐藏且稳定的密码、绑定和故意损坏后的停机备份恢复。上述集成门槛尚待最终准确提交验证。

## 显式 UDP 安装预检

`--node-udp-port 10443 --node-udp-port 20443` 可重复声明本次安装要检查的节点 UDP/QUIC 端口，每个值为 1024–65535。默认不推导 UDP 端口，不因 `--node-port` 指定 TCP 而放行同号 UDP；TCP/UDP 可以使用同一数字但分别检查。

新安装在任何账号、服务或防火墙写入前进行 IPv4 和可用 IPv6 UDP bind 探测，碰撞则停止，不结束占用进程。既有安装/升级为避免与运行中的自身节点冲突会跳过 UDP bind 探测并明确提示人工核对端口所有权；不能把它写成升级已证明端口空闲。

规则提示按 `10443/tcp`、`10443/udp` 分开列出。只有明确 `yes` 或操作者明确选择 `--open-firewall yes` 才可更改受支持 UFW/firewalld 的已核实入口规则，不自动启用防火墙。云安全组、自定义规则、无法确定的入口仍须人工确认；`--assume-external-ports-open` 是操作者声明，不是连通证明。

此标志不会创建 HY2 节点、分配密码、打开应用 UDP 转发或持久保存 UDP 声明；后续安装/升级如需同样预检和防火墙检查，必须重新传入。实际节点仍由面板另行创建，详见[安装指南](INSTALLATION.md)。

## 集成门槛

- `test_hysteria2_preflight_loopback.py` 保留裸固定核心前提；使用临时 CA、假密码、隔离监听器和可达 HTTP IP，逐客户端正向及错误密码/CA/SNI，真实认证/x509 原因，零目标请求、无 DIRECT
- `test_hysteria2_profile.py`：严格字段、密码特殊字符/三格式映射、默认值、秘密隐藏、编辑保留与不支持原始字段拒绝；`test_export_real.py` 用真实固定客户端检查导出
- `test_hysteria2_loopback.py`：应用编译与无 cookie 公开订阅生成配置后的双客户端真实链路，和裸前置证据分开报告
- `test_hysteria2_managed_certificate.py`：绑定/改绑/编辑/续期、失败材料保护、停止待应用及实际 QUIC 新连接验证；`test_inbound_editor_browser.py`：真实 Chromium 完整流程与损坏后恢复
- `test_hysteria2_installation.py` / `test_firewall_support.py`：显式可重复 UDP 端口、双栈冲突、TCP/UDP 分离、精确确认和既有升级语义；测试不得修改真实主机防火墙
- `test_acme_fixture.py`：DNS 测试夹具的 TCP/UDP 同端口配对，保留 ACME 失败历史，不通过 skip 或削弱 ACME 断言绕过碰撞
- 最终独立审查、八组 exact-head、授权正常 merge 和八组 exact-master 分别核查；queued/running/skipped、前置通过和旧主线绿灯均不能代替最终成功

## 历史：集成前状态

首次阶段文档记录：“配置检查已本地通过；实际链路仍待前置 CI。未开启 Hysteria2 公开导出，也不声称完整支持。”这是初始前置状态；当前链路结果与集成待完成门槛见本页开头。

## 首次前置失败记录（保留）

首个前置提交 `6daff89e562c93c77b797aa27131b1a438e654a9` 的[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37292930480)证明两种客户端均可通过 QUIC 转发 HTTP，但 Mihomo 的一例错误 CA 没有取得 x509 日志，继承的 gRPC 前置一例 Mihomo 错误 SNI 也没有原因日志，故整组失败。不能将其记作通过。

后续测试仅在限定观察期内重复失败请求，每次仍必须零目标送达，最后仍必须出现真实 x509 原因。密码证据收紧为 `authentication failed`，避免将本地 mixed listener 的 `Auth success` 当作 HY2 拒绝证明。未换二进制、跳过 TLS、改协议或加 DIRECT；结果待新准确提交 CI。

此处最后的“结果待新准确提交 CI”是首次修复时的状态。后续准确提交 `e18003670c6469489c7a63413be0a3f9bd77cf0b` 已由上述真实链路运行证明 58 项通过；ACME 仍失败，最终集成验收仍待完成。修复仅扩大有界失败请求观察，保留每次零目标请求和最终真实原因断言，未改变固定核心或安全契约。

## PR #20 合并后的首次主线验收（2026-10-05）

[PR #20](https://github.com/ForceMind/V-UI/pull/20) 的候选 `c9d45aac7b8e6951e231d3ca41a9eb8507638510` 已完成独立审查与八组准确候选工作流、11 个 job 和全部步骤。真实链路 63 项及另行激活的托管证书 8 项均通过，实际 Chromium 完成 HY2 创建/取消/编辑/刷新/重新打开/三格式导出/故意损坏后的停机恢复。

随后正常合并至 `dbf1cfbe7e3799481d6b8bffec3e028f7367219b`，两个父提交、签名、来源祖先关系与候选 tree `30a850391d11e72d9fa42cc1bec4db2b859bcda2` 一致，来源分支保留。首次主线七组成功，但[真实链路运行](https://github.com/ForceMind/V-UI/actions/runs/37351940554)失败三项继承断言：Trojan/Mihomo 错误 CA，以及公开 gRPC/Mihomo 的两个错误 SNI 组合缺少真实原因日志。HY2 前置/公开链路用例通过；由于第一条 discovery 失败，该运行后续托管证书命令未执行，不能计作主线通过。

后续独立测试修复只在限定观察期内重复已断言拒绝的请求，仍必须取得 x509 和具体 CA/SNI 原因；固定目标计数从第一次请求前开始保留，不能重置或把晚到数据重新当作基线。超时、一般 TLS 日志、成功响应和任何目标送达都不能通过。`test_tls_rejection_evidence.py` 明确回归这些失败路径。没有更改产品配置、协议、二进制、TLS 校验、CI gate 或 skip；修复自己的准确候选/主线门槛仍待完成。本次合并不表示已发布、附件已晋升或已部署。

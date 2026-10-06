# v0.4.8 XHTTP 能力刻画前置

## 状态与决定

本阶段从 [REALITY/Vision 已验收 master](REALITY_VISION_CLOSURE_047.md) `6b049262457b259c602c5e74be296ef52780c462`、tree `316f0159e3ae3d7362022d9c9ac61bc0afa867c1` 的独立分支 `iteration/v0.4.8-xhttp-preflight` 推进，限测试与文档。**产品 `VERSION` 仍为 `0.4.7`；XHTTP/HTTPUpgrade 公开导出保持阻断。** 本文的 0.4.8 是路线阶段标识，不是新版本发布或公共支持声明。

固定官方核心下没有 XHTTP 的三格式/双客户端公共契约：Xray 26.3.27 有 XHTTP 服务端；Mihomo 1.19.32 有原生 YAML 与实际 URI importer；sing-box 1.14.2 的 inbound/outbound transport decoder 不支持 XHTTP。不得改 pin、重编译核心、新增 Xray 客户端导出，或改称 HTTPUpgrade、WS、HTTP、h2 来绕过缺口。

2026-10-06 已有实际本地 parser 证据和固定源码核对；其后本地运行在未升级权限、未改环境或安全设置的情况下取得独立的 Xray 服务端 TLS 1.3/h2 探测、Mihomo 原生 YAML 与真实 URI provider 的预期 loopback HTTP 正文和无 DIRECT；六类负向均有真实拒绝原因、零应用送达与无 DIRECT。首次完整本地前置因独立 HTTPUpgrade 诊断的两项 netlink EPERM 子测试失败而失败；其后首个准确候选 CI 已完成七组、链路组仅因要求一个固定客户端不输出的错误日志而失败，详见下方保留记录。修正后的准确提交验收仍待完成。此前 socket/netlink/Chromium EPERM 保留为历史环境限制，既不算通过，也不能泛化为当前所有本地运行阻断。当前本地结果、测试代码、配置可载入及历史主线绿色均不能替代本阶段的准确提交验收。

## 固定官方来源

沿用仓库已有下载来源、归档 pin 与构建。本表 SHA-256 为本次实际检查的本地二进制，不是四目标通用摘要：

| 核心 | 固定 tag 对应源码提交 | 已检查二进制 SHA-256 |
| --- | --- | --- |
| Xray 26.3.27 | `d2758a023cd7f4174a5a5fa4ff66e487d4342ba0` | `8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed` |
| sing-box 1.14.2 | `af6e64c3b69e6132ebaee0e1a3d24e93903f6709` | `07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793` |
| Mihomo 1.19.32 | `88dcbf7f1614a67c3b36b848ee3592dfa92ada36` | `3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8` |

关键固定源码：

- [sing-box transport discriminator](https://github.com/SagerNet/sing-box/blob/v1.14.2/option/v2ray_transport.go)：没有 XHTTP 类型
- Xray [配置/mode](https://github.com/XTLS/Xray-core/blob/v26.3.27/infra/conf/transport_internet.go)、[XHTTP 请求处理](https://github.com/XTLS/Xray-core/blob/v26.3.27/transport/internet/splithttp/hub.go)、[path 规范化](https://github.com/XTLS/Xray-core/blob/v26.3.27/transport/internet/splithttp/config.go)、[Host 匹配](https://github.com/XTLS/Xray-core/blob/v26.3.27/transport/internet/internet.go)、[VLESS UUID 原因](https://github.com/XTLS/Xray-core/blob/v26.3.27/proxy/vless/encoding/encoding.go)
- Mihomo [VLESS/native XHTTP](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/vless.go)、[XHTTP 默认值](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/transport/xhttp/config.go)、[ALPN/mode](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/transport/xhttp/client.go)
- Mihomo [URI 转换](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/convert/v.go)、[真实 provider 流程](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/provider/provider.go)、[adapter parser](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/parser.go)

源码说明实现边界，不能单独证明二进制或链路运行通过。

## 唯一 XHTTP 前置 profile

- Xray 26.3.27 VLESS/TLS/XHTTP 服务端 → Mihomo 1.19.32 原生 YAML 与实际 file URI provider；两条导入路径使用同一个固定客户端，不写成两种客户端
- 一个假 UUID、空 VLESS flow、`encryption/decryption:none`，正常验证 TLS
- 明确字面 SNI `vpn.example.test`、Host `vpn.example.test`、ASCII path `/vui-xhttp/`，不靠推断或默认补齐
- 显式 `mode:stream-one`、ALPN `[h2]`、Chrome fingerprint；其他 mode 只作为 parser 或单项负向刻画，不扩张正向 profile
- 仅 HTTP/TCP 应用；原生 Mihomo `udp:false`。URI importer 硬编码 `udp:true` 并默认 xudp，不能声称 URI 禁用 UDP；应用 UDP/XUDP 不在验收范围
- 不含 query/fragment/手工百分号 path、额外 headers、padding/XMUX/reuse 调优、独立 download、REALITY、h3、early data、反代或 CDN

| 表达 | 有界字段 |
| --- | --- |
| Xray 服务端 | `streamSettings.network:xhttp`、`security:tls`、`tlsSettings` 的服务端证书/私钥与 `[h2]`、`xhttpSettings:{path,host,mode}`；`settings.clients[0].id`、`decryption:none` |
| Mihomo 原生 YAML | `network:xhttp`、`xhttp-opts:{path,host,mode}`、`uuid`、`tls:true`、`servername`、`skip-cert-verify:false`、`alpn:[h2]`、`client-fingerprint:chrome` |
| VLESS URI 夹具 | `security=tls&type=xhttp&encryption=none&sni=...&fp=chrome&alpn=h2&path=...&host=...&mode=stream-one`，正常 query 编码；使用真实 file-provider → ConvertsV2Ray → handleVShareLink → adapter.ParseProxy |
| sing-box JSON | 不可表达；必须拒绝，不能省略或替换 transport |

以上仅用于假数据前置，不是 V-UI 新增的公共导出格式或上线说明。服务器材料不进入客户端输出。

## 已观察 parser 结果

2026-10-06 对上述固定二进制的实际命令结果如下；这些是配置检查，不是转发成功：

| 检查 | 结果 |
| --- | --- |
| Xray `run -test`，分别 stream-one/stream-up/packet-up/auto | 四项 exit 0，Configuration OK |
| Mihomo 原生 `-t`，同四种 mode | 四项 exit 0 |
| Xray invalid-mode | exit 23，`unsupported mode: invalid-mode` |
| Mihomo 原生 invalid-mode | exit 1，`xhttp mode invalid-mode is not implemented yet` |
| sing-box `transport.type:xhttp` | exit 1，`unknown transport type: xhttp` |
| Mihomo file-provider 的 stream-one URI 与 invalid-mode URI，分别 `-t` | 两项均 exit 0，说明 `-t` 未验证 provider payload 的实际导入语义 |

最后一项是必须保留的限制：provider 外壳 parser 成功不能证明 URI payload 有效。只有实际启动 provider、固定核心真正选中代理并转发，才构成该路径的运行证据。

## 真实 CI 负向证据契约

每条 YAML/provider 路径先证明正确 HTTP 响应正文与固定应用 IP 可达，单独记录 h2。每个负向使用新客户端会话/日志边界，只改变一项；服务端与独立应用目标不变。整个有界观察窗口固定基线、零应用请求、无 DIRECT；不得重置计数来掩盖晚到流量。

| 单项变化 | 必需真实拒绝原因与限制 |
| --- | --- |
| 错误合法 UUID | Xray `invalid request user id` |
| 错误 CA | 客户端真实 x509 unknown-authority/证书链错误，正常校验保持 |
| 错误 SNI | 客户端真实 x509 名称不匹配，HTTP Host 保持正确以分离 TLS 与路由层 |
| 错误 path | 不相关前缀；真实 Xray `failed to validate path`；固定源码该分支返回 HTTP 404（未抓取上游状态） |
| 错误 Host | 不相关的合法主机名；SNI 保持正确，真实 Xray `failed to validate host`；固定源码该分支返回 HTTP 404（未抓取上游状态） |
| 错误但合法 mode | 服务端保持 stream-one、客户端 packet-up；真实 Xray `packet-up mode is not allowed`；固定源码该分支返回 HTTP 400（未抓取上游状态） |

运行夹具要求上述真实服务端原因、固定应用零送达和无 DIRECT；外层代理响应仅检查状态 `>=400`，或记录连接失败。表中的上游 XHTTP 404/400 是固定源码对应分支的行为，不是夹具抓取到的上游 HTTP 状态证据；不能把外层代理错误码写成上游状态捕获。

Xray 给 path 补首尾 `/`，实际校验前缀而非完全相等；同前缀变化不必失败。Host 忽略大小写并去除请求端口，大小写差异不是拒绝用例。mode 兼容有方向，例如 stream-up 服务端也允许 stream-one，auto 更宽；不能断言任意不相等 mode 都拒绝。非法 mode 的 parser 拒绝是另一门槛，不代替合法错误 mode 的运行测试。

超时、EOF、单独零送达或旧日志都不足以证明拒绝。CA 只通过假测试子进程 `SSL_CERT_FILE` 与存在的空临时目录 `SSL_CERT_DIR` 使用，不修改主机信任或关闭校验。继承 REALITY master 的 VMess 被动 x509 日志轮询首次失败与 unchanged-code 重跑通过继续保留，不能借本阶段无关成功宣称永久修复。

## HTTPUpgrade 独立缺口

HTTPUpgrade 是不同的真实传输。固定 sing-box 1.14.2 服务端/JSON 客户端与 Mihomo 1.19.32 原生 YAML 有可表达交集；最小 profile 为 VLESS、单假 UUID、空 flow、普通验证 TLS、显式 SNI、`http/1.1`，无 early data/fast-open。它不扩张 XHTTP 范围，也未因此获得 V-UI 公共支持。

- sing-box 使用 `transport:{type:httpupgrade,host,path}`，见[服务端](https://github.com/SagerNet/sing-box/blob/v1.14.2/transport/v2rayhttpupgrade/server.go)和[客户端](https://github.com/SagerNet/sing-box/blob/v1.14.2/transport/v2rayhttpupgrade/client.go)
- Mihomo 必须使用 **`network:ws` + `ws-opts.v2ray-http-upgrade:true`**，同时设置 `ws-opts.path` 与 `ws-opts.headers.Host`；该 flag 改变实际 wire protocol，并非普通 WebSocket
- sing-box HTTPUpgrade 服务端检查字面 Host/path（含其缺少前导 `/` 时的规范化），拒绝真实 WebSocket 的 `Sec-WebSocket-Key`，使用 GET/HTTP1.1 upgrade 后的裸流
- 固定 Mihomo URI `type=httpupgrade` converter 保留 `network:httpupgrade`、生成 `ws-opts` 却不添加 upgrade flag；provider/adapter parser 不做必要的 `ws`+upgrade 规范化，VLESS adapter 的未知 network 默认走普通 TCP/TLS
- 实际 parser 检查中，原生 `network:httpupgrade` 和 `network:not-a-transport` 都返回成功；这不证明 HTTPUpgrade 能力。URI `type=ws` 同样无法编码必需的 upgrade flag，不能偷偷加 provider override 后声称原 URI 无损

单独的运行诊断保留三条直接链路：canonical YAML → 固定 sing-box HTTPUpgrade 应成功；原样 URI → 同一 HTTPUpgrade 服务端应失败且零应用送达；原样 URI → 独立普通 TCP 服务端应成功。**最后一项只用于证明 URI 错落 TCP，绝不计作 HTTPUpgrade 通过。** 首次 CI 已观察到这些直接链路行为，但真实原因日志断言失败，整组没有通过。

固定 Mihomo 的 VLESS `recvResponse()` 可产生 `unexpected response version`，但这个发生在初始 dial/write 成功之后的 relay read。正常 tunnel 不保证输出该后期错误；继续延长日志轮询不能建立所需证据。源码见 [VLESS 读响应](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/transport/vless/conn.go)、[tunnel 错误记录边界](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/tunnel/tunnel.go)与[handleSocket](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/tunnel/connection.go)。controller delay 路径也可能将该失败改成一般错误，不能作为替代证明。

修正后的原因证据是独立的真实观察，不改写 URI、不替换直接核心链路：

1. 独立 loopback TLS recorder 使用同一临时证书/CA、正确 SNI 与 `http/1.1`，不转发、不充当反代，也不编造协议响应。分别由固定 Mihomo canonical YAML 与原样 URI 发起新会话
2. canonical YAML 必须产生完整 `GET /vui-xhttp/ HTTP/1.1`、正确 Host、Connection/Upgrade headers 且无 `Sec-WebSocket-Key`；URI 必须产生 VLESS version 0、精确假 UUID、零 addons、TCP command、精确 loopback 目标 IP/port，后接完整预期 HTTP 请求。每条观察不超过 4096 字节，EOF、超时、截断、未知 bytes 或错误 TLS/SNI/ALPN 都不能通过；recorder 流量不能记作应用送达
3. 将 recorder 从真实 URI 客户端取得的同一段完整 bytes **不改任何字节**，经另一条正常验证证书/SNI/ALPN 的 TLS 会话送到实际固定 sing-box HTTPUpgrade 服务端。必须读取实际 `HTTP/1.1 400 Bad Request` 及精确正文 `400 Bad Request`，并保持应用计数不变；一般错误、超时、EOF 不够
4. 直接的真实 URI → 实际 HTTPUpgrade 服务端仍必须失败、应用增量为零且无 DIRECT。上述 replay 的 HTTP400 单独标记，不能说成直接 URI 会话的抓包；canonical 实际成功与普通 TCP 错误传输对照也仍为必需

已检查 sing-box 自身 `version` 输出的 Go 构建为 `go1.26.8`，不是从另一客户端推断。该标准库 [request parser](https://github.com/golang/go/blob/go1.26.8/src/net/http/request.go)拒绝前导 NUL 的非法 method，[server](https://github.com/golang/go/blob/go1.26.8/src/net/http/server.go)返回 HTTP400 而不输出详细原因；这解释源码行为，修正后的实际 replay 响应仍须真实 CI 验证。临时观察只记录协议类型/结果，不公开原始 UUID/header bytes 或 TLS 密钥。

## 首次前置本地执行结果（2026-10-06）

- 默认全量测试：487 项，48.304 秒，`OK (skipped=120)`；实际执行 367 项，120 项环境 skip 不计作 runtime 通过
- 独立 XHTTP contract 单元：7 项通过；选取的固定 parser 方法：3 项通过，1.584 秒
- 最终工作树选取的 10 个方法（3 parser + 1 XHTTP 正向 + 6 负向）重验全部通过，25.939 秒、无 skip。明确仅排除已在完整执行中 netlink 阻断的 HTTPUpgrade-gap 方法；这个选择性通过不改写完整 11 方法组的两项失败，也不替代待完成的准确 CI
- 独立正向探测：1 项通过，0.585 秒，覆盖原生 YAML 和实际 URI provider 的预期 HTTP 正文/应用送达/无 DIRECT
- 完整 focused preflight：11 个测试方法，24.529 秒，其中 10 个方法通过；HTTPUpgrade URI-gap 方法的两个服务端子测试失败，最终 `FAILED (failures=2)`，没有 skip。不能将该整组写成通过
- 通过的 XHTTP 运行方法含上述两条路径的正向与六类独立负向：错误 UUID、CA、SNI、path、Host、mode；每次均记录真实 VLESS/x509/XHTTP 原因、`application delta=0` 和无 DIRECT
- h2 证据来自独立且正常验证证书的服务端 TLS 探测（TLS 1.3 / h2），不是客户端流量抓包；实际转发客户端明确配置 h2，二者分别记录
- 两项失败均为 sing-box 服务启动时的 `create netlink socket: operation not permitted`：一项 HTTPUpgrade 服务端、一项独立普通 TCP 对照服务端。发生在任何 HTTPUpgrade/TCP 客户端握手之前，不能推断真实 URI 握手行为已验证

以上为首个候选前的本地工作树证据。历史 EPERM 与 XHTTP 实际成功分别保留，既不把环境失败写成通过，也不以历史限制抹去成功。

## 首次准确候选 CI 失败与观察修正

候选 [`77c264ab35a0b7a6217638cf9decccae178fd7ea`](https://github.com/ForceMind/V-UI/commit/77c264ab35a0b7a6217638cf9decccae178fd7ea)、tree `0428097bd71c140b620ea79018df51b3884f06e3` 的[链路 run 37486293192 / job 112347034954](https://github.com/ForceMind/V-UI/actions/runs/37486293192/job/112347034954) attempt 1 执行了 100 项链路测试，只有 HTTPUpgrade URI-gap 方法因未观察到 `unexpected response version` 日志而失败。其余七组已成功；失败的链路组不算通过，后续独立 managed-certificate 命令也不能从该次失败推导已运行。

同次 CI 的 canonical YAML HTTPUpgrade、独立普通 TCP 错误传输对照与全部 XHTTP 正向/六类负向实际通过；原样 URI 对 HTTPUpgrade 目标没有应用送达，却只留下真实已路由连接日志。首次断言保持红色，没有用超时、EOF、零送达或无关成功掩盖原因缺失；完整日志保留。修正仅换用上节已审阅的 recorder/原样 replay 观察，未改变 pin、核心、TLS 校验、URI importer、公共导出或其他认证断言。

修正后的本地结果单独记录：

- 独立真实 recorder 方法通过（6.738 秒）：canonical YAML 与原样 URI 分别观察到完整 HTTPUpgrade / raw VLESS bytes，正确 SNI、正常 TLS 和 http/1.1，应用计数不变
- 默认全量 496 项，41.919 秒，`OK (skipped=121)`：375 项实际执行；15 项 focused 单元回归通过。模拟 recorder 的单元不输出真实 TLS/核心成功消息
- 完整修正前置 12 个方法，38.761 秒：11 个方法通过；HTTPUpgrade 核心/replay 方法仍因两个 sing-box 服务启动 netlink EPERM 子测试失败，整组 `FAILED (failures=2)`、无 skip。独立 recorder、三个 parser 方法、全部 XHTTP 正向/六类负向通过，不等于该整组通过

真实 fixed-server HTTP400 replay 及完整修正候选八组须准确提交 CI，不进行盲目 unchanged-code 重跑；原首个候选失败和两轮本地环境失败都保留。

## 测试入口与不变门槛

- `tests/test_xhttp_contract.py`：默认单元边界、夹具字段和拒绝原因检查，包含公共导出仍拒绝；不代表 runtime
- `tests/test_xhttp_preflight_loopback.py`：固定真实 parser 与前置链路，使用已有 `VUI_TEST_CORES`、`VUI_TEST_MIHOMO` 激活，由既有 loopback glob 纳入；默认 skip 不算通过
- `tests/xhttp_helpers.py`：仅假数据/临时目录/临时 CA 夹具；不进入生产应用路径
- `python scripts/check_docs.py`：版本、文档链接及安装 shell 语法；与实际核心、浏览器、systemd/安装、部署门槛分别报告

准确候选必须保留全部八组：Documents and release contracts、Test V-UI、Real loopback proxy and DNS chain、ToClash reference and export verification、ACME certificate acceptance、One-command installation acceptance、Portable Linux runtime matrix、Selected release deployment gates。queued/running/skip、旧 SHA 或本地 parser 成功不等于该候选八组通过。

四目标 Linux、40 项 ToClash、PR #23 普通响应 allowlist、已验收协议、秘密安全编辑、证书 desired/applied 与 `CORE_STOPPED_PENDING_APPLY`、备份恢复均保持。高级草稿保留或拒绝，不能静默归一化后解锁公开导出。无新生产依赖、迁移、反代、核心 pin/构建变化、tag/Release、附件晋升、真实生产部署、CA/账户或主机信任/防火墙变更。

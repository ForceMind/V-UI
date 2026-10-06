# v0.4.7 REALITY / Vision 阶段契约与历史

## 已验收收口

REALITY/Vision v0.4.7 已由 [PR #24](https://github.com/ForceMind/V-UI/pull/24) 正常合并至签名 master `6b049262457b259c602c5e74be296ef52780c462`，最终候选 `70cc2f4f7dc4349c7ecffbc33fc088f51b8651fa` 与主线 tree 均为 `316f0159e3ae3d7362022d9c9ac61bc0afa867c1`。独立审查完成，准确候选八组/11 jobs/全部步骤 attempt 1 成功；准确主线八组/11 个最新 jobs/全部步骤成功，其中链路首次继承 VMess/Mihomo CA 用例缺少必需 x509 日志，unchanged-code attempt 2 取得真实原因后通过，其余七组 attempt 1。该重跑不表示旧被动日志轮询不稳定性已永久修复。真实链路 89 项、独立 HY2 8/TUIC 8 和 Chromium 完整流程已分别验收，详见[收口记录](REALITY_VISION_CLOSURE_047.md)。

以下保留本阶段裸前置、集成和审查时的原始门槛与失败事实；其中“当前进行中”“待完成”指当时状态，不覆盖上述最终收口。后续修改仍须在自己的准确提交重验。

## 原集成阶段状态

当前获准按路线推进独立 REALITY/Vision 阶段，起点为 [TUIC v0.4.6 已验收 master](TUIC_CLOSURE_046.md) `df8a980beb682a981d72e42760705f1831cacf9b`，tree `f68098e398cb7ac29e2e1e809b8f741bb3c30533`。本阶段先做官方固定核心的裸前置；**真实 CI 前置通过前，公开 REALITY/Vision 支持保持阻断**。源码支持、配置可载入、测试代码存在和既有 TUIC 主线通过都不等于 REALITY 链路已验收。

准确裸前置 `a44b5ce20edcaee1ee3b26c34b7c21badfe5bf7b` 已核对八组/11 jobs/全部步骤 attempt 1 成功，tree `13dd71f1c27f33695b7e5b5dcd77171e783df581`。真实链路 82 项与独立 HY2 8/TUIC 8 通过，当前进入公共导出/编辑集成候选，最终准确候选/主线验收仍待完成。开发环境 socket/netlink EPERM 只能记录为环境阻断，不作为本地 runtime 或浏览器成功。本页集成门槛须在最终代码提交重验；当前版本号 0.4.7 不代表正式发布或生产部署。

## 固定源码与实际实现

保持官方 sing-box **1.14.2** 服务端/客户端与 Mihomo **1.19.32** 二进制、归档 SHA-256 pin 和构建不变，不换核心、不重编译、不增加反代。源码判断固定到相应 tag：

| 固定来源 | 本阶段需要保留的事实 |
| --- | --- |
| sing-box [go.mod](https://github.com/SagerNet/sing-box/blob/v1.14.2/go.mod) | REALITY 依赖 MetaCubeX/utls v1.8.7，VLESS/Vision 依赖 sing-vmess v0.2.8 |
| sing-box [reality_server.go](https://github.com/SagerNet/sing-box/blob/v1.14.2/common/tls/reality_server.go) | 独立握手参考地址、字面 SNI allowlist、32 字节 raw-base64url 私钥与 short ID；REALITY 拒绝证书/私钥材料与 ACME 绑定；诊断桥接到 trace |
| sing-box [reality_client.go](https://github.com/SagerNet/sing-box/blob/v1.14.2/common/tls/reality_client.go) | 公开密钥与 short ID 校验、uTLS、REALITY 验证及失败后的伪装 GET |
| sing-vmess [service.go](https://github.com/SagerNet/sing-vmess/blob/v0.2.8/vless/service.go)、[client.go](https://github.com/SagerNet/sing-vmess/blob/v0.2.8/vless/client.go)、[vision_utls.go](https://github.com/SagerNet/sing-vmess/blob/v0.2.8/vless/vision_utls.go) | VLESS UUID 和 flow 分开校验，Vision 走实际 uTLS 连接适配；空 flow 与 Vision 不可互换 |
| Mihomo [vless.go](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/vless.go)、[outbound/reality.go](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/reality.go) | 实际 VLESS/Vision 参数路径和 REALITY 公钥/short ID 解析 |
| Mihomo [component/tls/reality.go](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/component/tls/reality.go)、[transport/vmess/tls.go](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/transport/vmess/tls.go) | 必需 uTLS fingerprint，REALITY 认证与伪装 GET；REALITY 分支没有传递普通 TLS 的 ALPN 覆盖 |
| Mihomo [common/convert/v.go](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/convert/v.go) | 实际 VLESS URI 导入字段；importer 硬编码 `udp=true`，URI 不能宣称禁用应用 UDP |
| MetaCubeX/utls [reality.go](https://github.com/MetaCubeX/utls/blob/v1.8.7/reality.go) | 先访问握手参考端，失败连接可能向它转发；trace 包含派生密钥字节，须限制测试日志 |

上述是源码刻画与测试设计依据，不替代固定二进制实际配置检查或连接证据。

## 有界公开契约

下列是已通过裸前置、当前实施并等待最终集成验收的严格公开范围：

- sing-box 服务端、VLESS 直连 TCP、精确 `xtls-rprx-vision`、单个规范 UUID，不增加其他 flow、WebSocket/gRPC/XHTTP/HTTPUpgrade、multiplex 或 Xray REALITY 支持
- 单个规范 16 位十六进制 short ID（8 字节，规范小写），显式字面 SNI；不依赖空值、推断、通配符或不同名称的替换
- Chrome fingerprint 必需；不同于已有 WS/gRPC 的可选 Chrome 契约。不开放额外 fingerprint 调优
- X25519 公私钥均为解码后 32 字节的无 padding raw-base64url 值；校验编码、长度和公私钥配对，已有正确配对必须保留，不因普通编辑重新生成。private key 仅服务端，public key 进入明确授权的客户端导出
- 不允许 ALPN 覆盖。固定 Mihomo 的 REALITY 路径不传递普通 TLS 的 ALPN 选项，不能接受后静默丢弃或声称三格式保留它。测试参考端的 h2 能力不等于产品新增 ALPN 开关
- 不绑定托管证书，不导入服务端普通证书/私钥材料，不以忽略 TLS/REALITY 校验解决错误。已有 TLS 节点切换须明确处理绑定，不能保留隐藏的冲突状态
- 仅验 HTTP/TCP 应用负载。sing-box 完整出站使用 `network: tcp`；Mihomo 完整配置与 URI 导入的 UDP 语义分开记录。固定 URI importer 写入 `udp=true`，不得声称 URI 可强制关闭 UDP；应用 UDP、XUDP 与 DNS 专项继续后置
- 不新增依赖、后端、数据库迁移或反代；既有 FastAPI/SQLite、四目标 Linux、40 项 ToClash 与安装/发布边界不变

## 本地假参考端与拒绝证据

裸前置使用两个相互独立的本地端点：

1. REALITY 握手参考端：临时 CA 签发的假证书、TLS 1.3、X25519 与 h2；它不是应用转发目标，不使用任何真实外部参考站点
2. HTTP 应用目标：独立监听器和固定请求计数，用来证明正确代理送达以及失败情况下零应用送达

服务端在认证前连接参考端，以及客户端认证失败后的伪装 GET，是固定源码允许的行为。**失败用例要求 HTTP 应用目标零送达，不要求参考端零连接或零 GET。** 不能将参考流量算成成功应用转发，也不能把合法伪装流量误报为 DIRECT。

伪装 GET 在客户端 goroutine 中启动，失败拨号的调用方可以先关闭同一连接，因而不能要求每次出现完整 HTTP HEADERS。参考端分别记录 ClientHello、完成的 TLS 1.3/h2 握手与实际 HTTP HEADERS；key/short ID/SNI 失败必须观察到参考 TLS 回落，HEADERS 仅按实际数记录。真实认证错误、应用零送达和无 DIRECT 的断言不变。

假参考证书同时覆盖允许 SNI 与测试用不允许 SNI，两种客户端使用同一边界；这样错误 SNI 验的是 REALITY 服务端 allowlist，不是证书名称不匹配。临时 CA 只通过假测试子进程的 `SSL_CERT_FILE` 与指向空临时目录的 `SSL_CERT_DIR` 注入；不修改主机信任、不关闭验证，不使用真实凭据/真实 CA。空目录必须存在，不能用未指定系统信任目录替代。

每种客户端分别先证明正确 HTTP 代理及目标 IP 可达，再只改变一项进行负向验证：

| 改动 | 必需的真实原因 |
| --- | --- |
| 合法但错误的 UUID | 服务端真实 `unknown UUID` |
| 缺少 Vision flow | 服务端真实 `flow mismatch`，明确预期 Vision、收到 none |
| 合法但不匹配的 X25519 public key | sing-box `reality verification failed` 或 Mihomo `REALITY authentication failed` |
| 合法但错误的 16 位 short ID | 同上，真实 REALITY 验证/认证失败 |
| 证书本身覆盖、但 allowlist 不允许的 SNI | 同上，真实 REALITY 验证/认证失败 |

畸形配置被 parser 拒绝要单独记录，不能代替上述合法错误值的运行时认证测试。原因必须来自本次会话的真实固定核心日志；超时、EOF、单独零目标请求或一般 TLS 日志不够。负向开始前固定应用计数，整个有界观察窗口内不得重置或放松；仍要检查无 DIRECT 与晚到请求。

默认使用 debug 级日志。若为假测试诊断开启 sing-box 服务端 trace，须限定到该假凭据子进程，并在日志输出、失败报告和 artifact 前脱敏派生密钥、私钥等材料。不能将包含 `AuthKey` 字节的 trace 作为可公开原始日志；也不改变生产默认诊断或官方核心。

## 当前应用集成门槛

1. 共享 schema/编译器严格校验上述边界；原始导入的未知/高级字段保留或拒绝，不能借编辑清除后解锁公开导出
2. UUID/private key/short ID 的编辑输入隐藏，空输入保留；校验已有公私钥配对和状态，不靠新生成凭据掩盖畸形导入。新建生成、明确替换与普通编辑分别测试
3. 普通列表/创建/更新响应继续沿用 [PR #23 allowlist](INBOUND_RESPONSE_CLOSURE_20261006.md)，无凭据、原始配置或服务端材料；特权编辑器与明确授权客户端导出分开，客户端仅含 UUID、public key、short ID 等必要参数，绝不含 private key
4. VLESS URI、完整 Mihomo YAML 和 sing-box JSON 保留 flow、SNI、Chrome、public key、short ID；真实客户端配置检查与固定 Mihomo 实际 URI provider/converter 导入分别验收，不用手工 parser 替代
5. 应用编译后经匿名无 cookie 公开订阅生成配置，双客户端重验正确 HTTP、分别错误 UUID/public key/short ID/SNI/missing flow、真实原因、应用目标零送达和无 DIRECT
6. 真实 Chromium 覆盖创建、取消、编辑回填、刷新、再打开、隐藏凭据稳定、明确替换、三格式导出、故意损坏后的停机备份/恢复；恢复后保持原 UUID、key pair、short ID 与服务状态。默认 skip 不算通过
7. 保留既有协议、HY2/TUIC 独立托管证书门槛、PR #23 普通响应保护、ACME、协议区分 UDP 安装预检、四目标 Linux 与 40 项 ToClash

## 准确提交与八组 CI 门槛

### 首次前置失败保留

首个裸前置 [`67769509401804fbbdd617acd01d047424035f0b`](https://github.com/ForceMind/V-UI/commit/67769509401804fbbdd617acd01d047424035f0b) 的 [真实链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/37470333699) 在 9 个 key/short ID/SNI 子场景中，因测试错误地强求伪装 HTTP HEADERS 大于零而失败。两种客户端及实际 URI importer 的正向 HTTP、错误 UUID/flow、真实 REALITY verification/authentication 原因和应用零送达已分别观察到；这些局部结果不能改写整组失败。修正只将非保证的 GET 要求改为实际参考 TLS 回落证据，并继续独立计数 ClientHello/完整握手/HEADERS，不换核心、跳过验证或放松应用送达与认证原因门槛。

裸前置已通过；集成与最终候选/主线仍为 pending，只能追加已核验准确提交的结果及链接。不能复用旧 TUIC 的绿色运行，也不能从本地配置检查推断 CI 或 runtime 成功。

| 阶段 | 必需结果 | 当前状态 |
| --- | --- | --- |
| 固定核心裸前置 | 双客户端正确链路和全部独立负向原因；完整八组 CI | a44b5ce 已完成；最终集成仍待验收 |
| 应用集成与独立源码审查 | 严格导出/编辑/allowlist/恢复及固定核心真实证据 | 当前进行中 |
| 最终 exact-head | 八组工作流、11 个最新 job、全部步骤成功 | 待完成 |
| 正常合并 | 父任务协调授权正常 merge，保留已有历史 | 待完成 |
| 最终 exact-master | 新主线八组工作流、11 个最新 job、全部步骤成功 | 待完成 |

八组分别为 Documents and release contracts、Test V-UI、Real loopback proxy and DNS chain、ToClash reference and export verification、ACME certificate acceptance、One-command installation acceptance、Portable Linux runtime matrix、Selected release deployment gates。queued、running、skip 或旧 SHA 成功均不算通过；如有重跑，保留首次失败、原因与 attempt，不改写为首次成功。

本阶段无 tag、Draft Release、公开 Release、附件晋升、真实生产部署、真实 CA/账户、生产凭据或实际主机防火墙变更。临时 CI deployment gate 与生产部署分开记录。


### 已验收裸前置的准确工作流

以下均对应 `a44b5ce`，11 个最新 job 与全部步骤 attempt 1 成功；不替代后续代码：

- [文档](https://github.com/ForceMind/V-UI/actions/runs/37471617674)、[Test/Chromium](https://github.com/ForceMind/V-UI/actions/runs/37471617623)、[链路](https://github.com/ForceMind/V-UI/actions/runs/37471617751)、[ToClash](https://github.com/ForceMind/V-UI/actions/runs/37471617595)
- [ACME](https://github.com/ForceMind/V-UI/actions/runs/37471617726)、[安装](https://github.com/ForceMind/V-UI/actions/runs/37471617724)、[四目标](https://github.com/ForceMind/V-UI/actions/runs/37471617643)、[临时部署门槛](https://github.com/ForceMind/V-UI/actions/runs/37471617671)

链路 job 112296276163 中，各次正常客户端 HTTP 的应用计数 1、参考 ClientHello 1、完整参考 TLS 0。key/short ID/SNI 负向的 Mihomo YAML 和 URI importer 各观察 10 次参考 ClientHello/完整 TLS，sing-box 各 1 次；均应用零送达、真实 REALITY 错误。HEADERS 实际均为 0，按事实记录，不解释为没有参考回落。UUID/flow 负向为真实 unknown UUID/flow mismatch、应用零送达。

### 证书排除修正的复现边界

在新严格集成前使用真实隔离 API/SQLite 与合成签发器复现：REALITY bind 返回 409 TLS_NODE_REQUIRED，却留下未应用 binding/error 行；合法 legacy raw TLS→REALITY 保存后仍有旧 applied binding。之后 apply_existing 返回 TLS_NODE_REQUIRED，REALITY 数据未变且核心应用未被调用。没有观察到 REALITY 被覆盖，也不声称真实事故。

回归先行修正仅处理上述 REALITY 边界：非法绑定在持久化前拒绝，合法 legacy 切换保存后解绑，非法切换保持节点/绑定；普通有效 TLS 的 desired/applied、失败保护和停止待应用保持。


现代 PUT 另有已复现的显式转换边界：已有 REALITY 节点仅提交 certificate_id（包括空 profile 或未指定 security）时，旧代码会合成 TLS 并改变节点/绑定。v0.4.7 要求明确 profile.security=tls 才能转换并绑定；未明确请求在读取证书材料前拒绝，节点/绑定保持不变。显式 REALITY→TLS 正向回归保留 UUID。此事实与上述旧绑定后续 reapply 未覆盖 REALITY 的复现是两个不同路径，均只使用隔离假数据。

### 集成源码审查阻断与修复

集成 `243434aff287afb6ceecfd1eb9f3de41b12e39c3` / tree `3fa79b4775f20bb3165dd69b55594ba6c578f1e5` 的八组 CI、11 jobs、全部步骤 attempt 1 成功，但独立源码审查仍发现阻断：已导入非直连 REALITY 草稿可在切换普通 TLS 时绕过 direct-only 旧状态检查，丢弃未知参数并变为可导出。该提交不因 CI 全绿而获准合并。

修复在任何旧 REALITY 草稿编辑前检查原始持久化状态，早于 generic ensure_credentials：未知嵌套字段、缺 UUID/key/short ID 均拒绝，不能生成替代凭据。已知 WS/gRPC/HTTPUpgrade 草稿继续与公开支持分离；省略的 path/Host/service/SNI/指纹和秘密保持，不借部分编辑重置。回归包含原 WS/max_time_difference 复现、三类草稿缺秘密、各嵌套未知字段、真实字段省略以及既有 gRPC 迁移。修正后的准确候选须重新独立复核和通过完整八组。

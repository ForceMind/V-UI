# 双开关 M3：面板、分层诊断与严格导出

日期：2026-10-15。承接 [合同](DUAL_PROXY_RELAY_CONTRACT.md)、[M2 运行事务](DUAL_PROXY_RELAY_M2.md)。这是候选实现说明；独立浏览器、真实诊断、严格导出链路及准确候选/主线 CI 尚须分别验收，不能从本文授予运行资格。

## 面板路径与操作

`/proxy` 使用独立同源 CSP、本地脚本/样式及现有管理员会话；主界面增加入口。两个开关仍独立且默认关闭，任一草稿开关开启时显示上游选择，不删除已保存内容。保持普通节点页与普通导出。

导入支持粘贴/UTF-8 小文件、明确选择格式、脱敏候选、逐项确认不导入区块及明确选择节点。三种输入格式不是全协议支持；只转换合同已冻结的 VLESS TCP TLS / REALITY-Vision。原文、密码和文件内容不写入 localStorage、URL、浏览器日志或普通响应；取消、保存成功及页面离开清除敏感输入。

保存草稿不应用。界面区分草稿修订、活动修订、活动固定 IP/解析时间、未完成事务与错误。应用前须对当前保存修订完成 `/check`，显示开关组合、上游摘要、受影响入口、本机 loopback、DNS/UDP 边界和短暂中断提示，再明确确认。编辑、导入、保存、刷新或版本冲突使旧审核失效。关闭活动中转明确提示节点恢复普通服务器出口。

预检不证明线路可达；最终应用仍重新读取全部入口/自身地址并安全检查。当前 `base_revision` 是代理设置版本，节点库独立变更不改变它，预检覆盖表不是锁住节点库的承诺。最终活动状态只认服务器提交结果；网络结果不确定时保留原幂等键，明确核查/重试，不自动生成新键反复应用。

## 诊断 API 与数据

- `POST /api/proxy/diagnostics`：严格 `{upstream_id, base_revision}`，202 返回任务。只接受已保存上游及准确草稿修订，不接受 URL、网卡、命令、端口、provider 或任意目标。
- `GET /api/proxy/diagnostics/{job_id}`：只允许同一管理员读取，其他所有者得到 404。
- `DELETE /api/proxy/diagnostics/{job_id}`：请求取消；清理未结束时仍显示 running，不能提前表示取消完成。
- POST 原始请求体最多 2 KiB，重复 JSON 字段/未知字段/额外 query 拒绝。沿用管理员、同源及 CSRF 检查；错误仅固定代码。
- 公共结果只保留任务/上游 ID、修订、目录版本、状态/时间、阶段观察、固定地址、出口 IP 与 stale 标记；不保存或回传上游 UUID、REALITY 材料、本机密码、原配置或核心日志。
- 每任务包括子进程启动、I/O 和清理最长 30 秒；每管理员最多一项、全局两项、管理员启动间隔至少 30 秒。结果有效期 300 秒，缓存最多 128 项；草稿变化后旧通过结果过期。

`proxy_diagnostics.py` 管理任务和固定输出允许列表；`proxy_diagnostic_worker.py` 使用私有管道和临时私有配置启动固定核心。父进程退出、超时、取消和异常均清理进程组并回收子进程；不修改已生效核心或持久设置。CPU、文件大小、描述符和输出受限；固定 Go 核心需要大虚拟地址预留，因此没有伪造 128 MiB 核心地址空间上限，也不从该功能推出 512 MiB 整机容量资格。

## 分层结果的精确含义

| 项目 | 可证明范围 |
| --- | --- |
| configuration | 当前固定核心接受临时配置，未证明网络 |
| tcp | 固定 IP/端口 TCP 建连 |
| tls_or_reality | 普通 TLS 检查 SNI/CA；REALITY 仅在验证 HTTPS 业务成功后表示该次认证得到证明 |
| proxy_forwarding | 验证 HTTPS 请求实际通过代理，和 HTTPS/出口观察共用一次证据 |
| https_exit | 固定 HTTPS 目标精确 200、合法有界 JSON，以及该请求报告的出口 IP |
| dns | 编译路径绑定受控 DoH，业务成功仍不足以独立观察完整 DNS 查询；保持 insufficient_evidence，不涂绿 |
| external_client | 无独立外部观察者，保持 not_tested |

固定目录仍是 Cloudflare DoH `1.1.1.1:443` / `cloudflare-dns.com` 与 ipify `https://api64.ipify.org?format=json`。面板明确告知后者能看到出口 IP 和请求元数据。正常 TLS 校验、禁止重定向，不发送用户 cookie/header、节点凭据或访问历史。上游全部 bootstrap 结果/自身地址与业务 DNS 全答案检查、原 SNI、禁止 metadata/私网和失败无 DIRECT 均继承 M2。

这里不把普通 HTTP/SOCKS 说成 ICMP、TUN、全机接管；终端示例是单次 curl / Git HTTPS 显式代理。密码通过交互/凭据机制提供，不内嵌到完整命令行。示例清除该次命令的 NO_PROXY 绕过，不更改系统环境。

## 与普通配置并列的严格中转导出

新增明确的管理端下载，普通 URI/Base64、订阅、ToClash 分流、已有协议能力完全保留：

- `POST /api/proxy/exports/mihomo.yaml`
- `POST /api/proxy/exports/sing-box.json`

请求为严格 `{base_revision, inbound_id, server}`。`base_revision` 必须匹配已提交且正在运行的中转活动版本，不能导出草稿；持有同一管理锁检查全部入口资格、运行/配置 hash、待决事务和残留其他核心。`server` 首期必须是明确公网 IP 字面量，TLS SNI 来自该入口的已验证配置；不猜 Host、不隐式解析服务器域名。该限制只针对新增严格完整配置，不删除普通域名节点导出。

文件只包含管理员明确选定的国内入口现有凭据，没有海外 UUID、原配置、REALITY 出口材料或服务端证书私钥。响应是 no-store 附件，带活动 ETag。新文件只配置客户端 loopback 监听，不启 TUN 或改系统 DNS。唯一代理/选择组不提供 DIRECT 候选，业务 DNS 经代理，应用 UDP 拒绝；不将传输层 QUIC/HTTPS 与应用 UDP 混为一谈。

固定 Mihomo 1.19.32 的 IP DoH URL 使用 `name-cert-verify` 验证 Cloudflare 证书名，但该字段不改 SNI；sing-box 1.14.2 明确设置 DoH SNI。这是客户端实现边界，不降低验证。通过 CONNECT/SOCKS 提交的业务主机名必须保留至国内中转的完整答案检查，不能先在客户端解析成某一个 IP 而绕过混合答案拒绝。应用自行解析后只提交 IP 时只能按该 IP 检查，不能声称恢复不存在的原始主机名或实现全机 DNS 接管。

## 验收、失败与限制

- 服务/API/资源限制测试使用临时私有 store、假凭据、受控 worker；真实核心 parser 与真实转发分别记录。
- 真实诊断测试使用固定假 `api64.ipify.org`、临时 CA 和独立本机 TLS/REALITY 上游；只有测试进程内替换 loopback 目录/非特权端口，生产 API、环境变量及持久配置没有这种覆盖入口。不请求公开 ipify，不替用户测试真实海外线路。
- 诊断和严格导出负例必须保留实际 TLS/协议原因、目标零送达、DNS 路径及无 DIRECT；连接在 TLS/HTTP 解析前即纳入跟踪，清理后还要审计迟到请求，不能在计数仍在变化时结束。
- Chromium 覆盖导入、明确选择、双开关四态、取消/刷新/后退、过期会话、CAS 冲突、幂等和同版本应用复核。模拟 API 的 UI 测试明确只证明交互；真实配置和实际链路由独立测试证明。
- 已保留本地 Chromium/socket、核心 netlink 权限失败，以及初次 fixture parser/诊断计数缺陷；不得把环境失败改为通过或删掉安全断言。当前仍以独立 PR 的准确 head/master 八组、具体 workflow/job/step/attempt 为最终事实。

公开版本仍为 0.4.8，核心 pins 不变。本阶段不创建 Release/tag、不部署真实服务器；实际外部设备、海外出口地理位置、真实小型 VPS/24 小时稳定性另记未测试。

### 首次 M3 CI 的 HTTP 夹具关闭修订

首候选 `a3e6e0d53ab0cc422896b2734bf1100be5952067` 的普通测试 [run 38794061939](https://github.com/ForceMind/V-UI/actions/runs/38794061939) 在 1,672 项中有 1 个失败（176 skip），浏览器步骤因此未运行：Python HTTP handler 把未完成 headers 的 EOF 当作结束，在关闭已接收连接时产生一次晚到 `do_GET`。零送达断言保留了此真实失败。

诊断假目标现在只对带真实空 header 终止行的 HTTP/1.1 请求执行应用处理；不清空计数、不删除已接收连接、不绕过最终 drain/Counter 审计。新增确定性 EOF 负例及完整 HTTPS 请求正例，完整请求关闭后仍保留实际计数；四种生命周期用例各重复 100 次。这里的零送达指零完整 HTTP 应用请求，不是目标未收到 TCP、TLS 或部分 HTTP 字节。修订只限测试假目标，生产 worker、协议/TLS 原因、路由和禁止 DIRECT 断言不变。新候选须重新完成全套及实际浏览器/诊断/导出门槛。

同一首候选 loopback [run 38794061714](https://github.com/ForceMind/V-UI/actions/runs/38794061714) 的策略 4、双跳 26、事务 4 项均通过；诊断 17 方法中另有同一 EOF 夹具失败及 1 个 DoH 失败原因匹配错误，严格导出步骤未运行。真实固定核心是 `dns: lookup failed for api64.ipify.org: (exchange4: servfail | exchange6: servfail)`，不是旧期待 `server failure`。测试改为同一行同时要求准确目标名、DNS lookup failed 和两族明确 SERVFAIL，次序可交换；新增错域名/缺族/分行/泛超时拒绝回归。生产路径不变，零完整 HTTP 应用请求及全 DNS/final drain 断言保留。

### M3 Chromium 登录依赖修订

候选 `5331a2f` 的 [Test run 38795984444 / job 116632375614](https://github.com/ForceMind/V-UI/actions/runs/38795984444/job/116632375614) 普通 full suite 通过；代理浏览器步骤 11 项中 8 项失败，均在严格 pageerror 断言记录 `axios is not defined`。这些是真实失败，不能用普通测试通过替代。

原因是浏览器真实登录先落到 `/ui/`：源码 dashboard 引用开发 CDN，而 M3 测试正确阻断外网后仍执行了依赖 Axios 的本地 `app.js`。独立 `/proxy` 使用原生 fetch，不依赖 Axios。测试现在复用既有打包 `prepare_frontend.py` 和 `vendor_frontend.py` 固定 checksum 资产，在隔离 payload 中提供完整同源 dashboard，再进入 M3 页面；CI 的同一次资产准备移到 M3 浏览器步骤前，节点编辑器继续复用。没有修改登录目的地、生产脚本或依赖 pins。

每次真实登录均等待 dashboard 菜单挂载并验证 Axios/Vue 已加载，外网请求和 pageerror 记录从登录起完整保留，不再清空旧的外网记录；缺资产直接失败，不使用 CDN 或假库兜底。新增静态打包与缺 Axios 负例只证明准备路径，不能代替 Chromium。本地已知 socket EPERM 不变，修订后的 8 项真实浏览器流程仍需准确新候选 CI 验收。

独立复核还发现既有双标签用例只监听第一页的 pageerror/console。现于创建任何页面前注册 context 的 page 事件，让每个新页共享完整错误和 console 审计，外网 guard 仍覆盖整个 context；双页事件回归只验证观察器，不冒充 Chromium 的真实双页流程。

### 严格导出 DoH 证书名负例的证据修订

后续 loopback [run 38795984798 / job 116632376318](https://github.com/ForceMind/V-UI/actions/runs/38795984798/job/116632376318) 的诊断 20 项通过；严格导出 7 个方法中 1 个失败。失败发生在 sing-box 显式 DNS 的错误证书名子例：新日志出现本次 `bad-dns-sing-box.example.test` 查询及连接受控 DoH 的 VLESS 信息，但 12 秒内未取得要求的同条 `x509` / `certificate` 原因，随后 Mihomo 对应负例尚未执行。保留该失败，不能把调用者超时、零 DNS 应用查询或旧日志解释为 TLS 拒绝证据。

固定源码说明原因记录边界：[sing-box 1.14.2 的 DNS 异步路径](https://github.com/SagerNet/sing-box/blob/v1.14.2/dns/router.go)将非 legacy 交换错误交给 callback；[UDP DNS handler](https://github.com/SagerNet/sing-box/blob/v1.14.2/protocol/dns/handle.go)取消请求，不保证普通 debug logger 输出 TLS 原因。[DoH HTTP/2 transport](https://github.com/SagerNet/sing-box/blob/v1.14.2/dns/transport/https_transport.go)使用固定依赖 [x/net v0.57.0](https://github.com/golang/net/blob/v0.57.0/http2/transport_common.go)，其诊断日志可记录实际 TLS dial 错误。

修订仅为错误 DoH 证书名的 sing-box 测试子进程增加 `GODEBUG=http2debug=1`，不改变生产配置、普通子进程、TLS 校验、核心 pins、请求次数或观测期限。拒绝断言收紧为同条新日志同时包含 `x509`、`certificate`、错误名和原证书名；全生命周期目标/DNS 计数、普通 DNS 零使用、无 DIRECT 和最终 drain 均保留。重复 client label 原本已使用递增独立路径，没有日志覆盖，新增回归固定此事实。

独立临时 CA/DoH 夹具实测正确名 TLS 握手通过、错误名产生真实 hostname-mismatch 且没有 DNS 应用查询；这只是夹具控制，不替代固定客户端负例。25 个相关单元/夹具及两固定客户端的 4 个 parser 子例通过。本地完整负例在上游核心启动时仍报 netlink EPERM，未执行到 DoH 场景且未转 skip；改后真实拒绝结果及准确候选/主线资格仍待 CI 验证。

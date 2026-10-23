# 开发与验证

## 2026-10-16 UDP / DNS 与上游导入门槛

下一有界阶段按 [D0 合同](docs/UDP_DNS_CAPABILITY_GATES.md)实施，不改普通节点默认或放开双跳 UDP。`test_upstream_import_capabilities.py` 用真实导出函数验证可保真输入与明确拒绝；`test_proxy_browser.py` 新增真实 UI 能力提示、禁选/重复激活、取消和刷新后状态不变。`test_udp_dns_capability_loopback.py` 通过既有 loopback glob 运行固定核心 Shadowsocks UDP/DNS 与双开关拒绝测试；helper 单元、真实子例数和 skip 分开记录。没有真实核心开关时明确 skip，打开开关后缺核心/权限或失败原因不足均 fail。

固定版本、唯一假数据、可达控制、fresh 实际拒绝原因、全窗口目标账本和最终清理 drain 是硬门槛；不得把 timeout、parser 成功、DNS listener 或 QUIC socket 记成应用 UDP 成功。准确候选/主线和四平台实际包继续验收；本阶段不发布或部署。

先读[主计划](ROADMAP.md)和[开发约束](AGENTS.md)。保持一个版本一个目标、独立PR、保留用户数据；不要把临时实验直接放进master或把未验证协议标成可用。

## 环境

使用Python3.12。Node仅供开发对照/语法检查，不是运行面板所需常驻服务。参考验证固定ToClash版本，不让远端规则自动改变生产行为。

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
python -m compileall -q app tests scripts main.py
node --check web/js/app.js
node --check web/js/certificates.js
bash -n install.sh
python -m unittest discover -s tests -v
```

环境依赖的测试默认skip，必须由对应专门任务覆盖；不能把skip当作成功。实际证据以最终提交的CI为准。

| CI | 验证 |
| --- | --- |
| Test V-UI | API、鉴权、数据库、状态、纯函数、原浏览器操作流程和托管证书节点编辑/恢复 |
| ToClash reference and export verification | 固定独立参考、100场景、40项目录、客户端配置 |
| Real loopback proxy and DNS chain | 实际 VLESS/TCP/TLS、Trojan、Shadowsocks、VMess，已验证 VLESS/WS/TLS、VLESS/gRPC/TLS，已验证 Hysteria2/TLS 及已验收 TUIC v5/TLS，已验收 REALITY/Vision 的双客户端链路与失败路径；XHTTP 刻画须独立标记且不得新增公共支持；DNS、拒绝/停启 |
| Selected release deployment gates | 实际离线包、HTTPS、完整Vue面板、备份恢复/回滚 |
| ACME certificate acceptance | Certbot/Pebble真实HTTP-01、续期/失败、Chromium证书页 |
| One-command installation acceptance | 仅临时CI主机上的实际sudo/systemd/socket安装、升级、重启 |
| Portable Linux matrix | x86_64/ARM64 × glibc/musl 对应原生环境、固定运行包与目标清单 |
| Documents and release contracts | 文档链接、版本、shell/Python语法、发布阻断规则 |

## 本机测试原则

全部测试使用临时目录、假账号、示例域名和临时CA；不得用测试脚本操作真实VPS数据或生产证书。Pebble测试不能启用always-valid绕过，也不能全局关闭证书校验。证书测试CA只通过测试构造器/子进程使用，不写主机信任库。

`VUI_SYSTEM_INSTALL_TEST=1`会真正操作systemd、账号和固定路径，仅能在明确为空的临时Ubuntu runner中运行；脚本先拒绝已有实例。不要在个人开发主机或生产VPS随意运行此环境开关。

## 修改与交付

核心/协议修改必须固定真实二进制并测试失败路径；仅检查生成JSON不等于连通。新增配置字段需同步前端、API、导出过滤、文档和测试；未知字段不得静默丢失。格式转换必须把凭据、SNI、TLS和传输参数当作完整性边界。

代码中不得提交用户token、数据库、私钥、运行时输出或未加密备份。每个PR报告准确提交、实际通过/失败/未测项目、兼容边界与回滚说明；未合并、未发布、未部署三者分别说明。

## 托管证书编辑回归

`python -m unittest discover -s tests -p 'test_inbound*.py' -v` 覆盖编辑模型、API 和实际 app.js payload；浏览器门槛默认明确 skip。专门运行时先用 `scripts/fetch_test_cores.py` 与 `scripts/vendor_frontend.py` 获取校验固定的核心/前端资产，再设置 `VUI_NODE_BROWSER=1`、`VUI_TEST_CORES` 和 `VUI_FRONTEND_ASSETS` 运行 `test_inbound_editor_browser.py`。可用 `VUI_TEST_CHROMIUM` 显式指定本机 Chromium。

浏览器使用隔离临时目录、真实 Uvicorn/核心进程和仅测试构造器注入的临时 CA，不会访问公网 CA、修改主机信任或防火墙。它覆盖托管 TLS 创建、取消、TLS 手工解绑拒绝、none/REALITY 切换、刷新/再编辑、恢复 TLS 后的受支持导出及停机备份恢复；发布包 HTTPS/升级/回滚仍由独立 deployment gate 验证。

## v0.4.3 WebSocket 基线回归

sing-box 1.14.2 / VLESS / WS / TLS 已由 PR #18 完成准确主线八组验收，见[WS 收口](docs/VLESS_WS_CLOSURE_043.md)。更早 v0.4.2 证据见[主线记录](docs/MAINLINE_CLOSURE_20261005.md)；基线通过不能代替后续提交自己的重验。

- `python -m unittest discover -s tests -p 'test_vless_ws_profile.py' -v`：共享 path/Host 校验、编辑往返、未知字段和 early data 拒绝、三格式无损导出、服务端去除客户端 Host、材料不泄露。
- `test_export_real.py`：固定 Mihomo 1.19.32 / sing-box 1.14.2 配置检查；配置能载入不等于链路能转发。
- `test_vless_ws_loopback.py`：设置 `VUI_TEST_CORES` 与 `VUI_TEST_MIHOMO` 才运行真实二进制门槛；两种客户端分别覆盖 Host 有/无、默认与可选 Chrome/HTTP1.1、错误 UUID/CA/SNI/path、无 DIRECT 回退与目标无请求。CA/SNI 失败应有真实 TLS 错误，不能用不可达目标伪造拒绝。
- 不同合法 Host 被实际 sing-box 服务端接受是必须保留的行为刻画；格式非法 Host 的拒绝由编译/API/导出验证，不把两者混为“Host 校验失败”。
- `test_inbound_editor_browser.py`：创建、取消、回填、修改 path/Host、刷新/再编辑、UUID 保留、三格式导出与停机恢复；证书绑定/续期和停止待应用单独验收。
- `python scripts/check_docs.py`：当前版本、链接与安装示例；历史文档保留当时的版本和失败记录，不批量改写历史。

范围限 HTTP/TCP，不宣称新增 UDP；TLS 必须验证明确 SNI，Host 不代替 SNI。后续改动必须在最终提交重新执行对应门槛，并分别报告未运行、失败和通过。

## v0.4.4 gRPC 基线回归

固定官方 sing-box 1.14.2 无 `with_grpc`，实际 gRPC Lite；Mihomo 固定 1.19.32。不得为获得标准 gRPC 重编译核心、换 pin、新增反代、依赖或后端。[PR #19 的准确主线](docs/VLESS_GRPC_CLOSURE_044.md) `84729dfc53165003e7d459a5d56621ce89ba497c` 已完成独立审查与准确候选/主线八组 CI，最终 11 个 job 和每一步全部成功。前置 45 项与最终主线真实链路 53 项分开记录；首次失败和已知限制仍见[原阶段契约](docs/VLESS_GRPC_044.md)。

- `python -m unittest discover -s tests -p 'test_vless_grpc_profile.py' -v`：严格 `[A-Za-z0-9._-]{1,128}` 字面 service name，不 trim/强转；flow、ALPN（含显式 null 拒绝）、未知字段、三格式映射、编辑往返、秘密和已导入字段保护。
- `test_vless_grpc_credentials.py`：已有 gRPC 节点缺失/畸形 UUID 的编辑返回 422，不生成替代；省略 flow/fingerprint/skip 字段保留原值，不使畸形导入变得可公开。
- `python -m unittest discover -s tests -p 'test_vless_grpc_managed_certificate.py' -v`：托管证书绑定、严格编辑和原子失败；`test_certificates.py` 覆盖实际应用/续期及停止待应用状态。
- `test_export_real.py`：真实 Mihomo/sing-box 检查公开导出，四种独立 Chrome/h2 组合；不能把 config check 写成真实连通。
- `test_vless_grpc_preflight_loopback.py`：保留裸核心互通前提及负向诊断；`test_vless_grpc_loopback.py`：编译后通过公开订阅生成配置的集成链路。二者设置 `VUI_TEST_CORES` 和 `VUI_TEST_MIHOMO` 才运行，不把默认环境 skip 当作通过。
- 实际 h2、四种指纹/ALPN组合、字面 `.`/`..`/128 字符 service、两种客户端错误 UUID/CA/SNI/service/case 均覆盖；先证明目标 IP 可达，失败目标不得收到请求且不得 DIRECT。
- 仅负向测试 sing-box 子进程加 `GODEBUG=http2debug=1` 获取真实 x509 CA/SNI 证据。实际 Lite 调用者仍可能超时，不能声称及时错误传播；保留第一次缺少 x509 日志的失败，不改 TLS 校验/系统信任或固定二进制。
- 激活 `test_inbound_editor_browser.py`，验创建/取消/编辑/刷新/再打开、UUID 保留、证书回填、三格式与停机备份恢复；续期不得启动手动停止的核心，保持 `CORE_STOPPED_PENDING_APPLY`。

仅 sing-box/VLESS/gRPC/TLS、单 UUID、空 flow、明确验证 SNI、ALPN 省略或 `["h2"]`、Chrome 独立可选、HTTP/TCP。Mihomo `udp: false`、sing-box `network: tcp`；不扩大到 UDP、h2c、Xray gRPC、authority/Host enforcement、额外 headers/timers/multi-mode、HY2/TUIC 或发布/部署。失败、环境阻断、未运行、前置通过和最终集成通过要分别报告。

## v0.4.5 Hysteria2 基线回归

[PR #20/#21](docs/HYSTERIA2_CLOSURE_045.md)已完成 HY2 最终主线八组/11 jobs/全部步骤 attempt 1 验收。原前置缺日志、ACME DNS 端口碰撞、首次 master 继承 Trojan/gRPC TLS 证据失败与 portable 首次 API rate limit 仍保留于[原契约](docs/HYSTERIA2_045.md)；以下是后续修改必须保持的基线回归。

- `python -m unittest discover -s tests -p 'test_hysteria2_profile.py' -v`：严格密码/SNI/默认值、标准 URI 编码、完整 Mihomo/sing-box 输出、拒绝调优/未知字段，编辑隐藏秘密、空值保留与遗留高级草稿保护。
- `test_hysteria2_preflight_loopback.py`：裸配置固定核心；`test_hysteria2_loopback.py`：应用编译、匿名无 cookie 公开订阅生成配置的双客户端链路。使用 `VUI_TEST_CORES` / `VUI_TEST_MIHOMO` 激活真实门槛，不能将默认 skip 写成运行通过。
- 双客户端每次只改变密码、CA 或 SNI 一项，先证明目标 IP 可达；错误密码需要真实 `authentication failed`，CA/SNI 需要实际 x509。有限观察窗口内可重复失败请求，但每次都必须零目标送达、无 DIRECT；本地 mixed listener 的 `Auth success`、超时或零送达本身不是上游拒绝原因。
- `test_export_real.py`：真实客户端 config check；与实际 HTTP/TCP 转发分开报告。Mihomo `udp: false`、sing-box `network: tcp`；QUIC 的 UDP socket 不代表应用 UDP 已验收。
- `test_hysteria2_managed_certificate.py`：API/编辑/绑定、续期后新的真实 QUIC 会话、失败保留旧材料和已应用 revision、停止核心 `CORE_STOPPED_PENDING_APPLY`。原证书门槛继续回归。
- 激活 `test_inbound_editor_browser.py`，覆盖创建/取消/编辑/刷新/再打开、密码隐藏/稳定、证书回填、三格式解析及故意损坏后的停机备份恢复。
- `test_hysteria2_installation.py` / `test_firewall_support.py`：可重复显式 UDP 端口、1024–65535、独立于 TCP、IPv4/可用 IPv6 冲突、精确确认和升级人工所有权提示；不修改实际主机防火墙，不自动启用它。
- `test_acme_fixture.py`：测试 DNS TCP/UDP 同端口配对的防碰撞回归；不得通过跳过 ACME 或降低真实证书断言掩盖夹具错误。
- `python scripts/check_docs.py` 与最终 diff 检查必须通过；实际浏览器、真实配置、链路、ACME、安装和四目标包在准确候选各自验收。前置成功不自动解锁合并或公开 Release。

完整边界与首次失败见[HY2 阶段契约](docs/HYSTERIA2_045.md)。HY2 基线不放开 obfs/hopping、带宽/ALPN/uTLS 覆盖、多用户、应用 UDP、其他核心或生产部署；TUIC 按下节独立契约验收；原固定 pins、四 Linux 目标和 40 项 ToClash 不变。


## v0.4.6 TUIC v5 已验收回归

TUIC v0.4.6 已由 [PR #22](https://github.com/ForceMind/V-UI/pull/22) 正常合并至 `df8a980beb682a981d72e42760705f1831cacf9b`，候选/主线 tree `f68098e398cb7ac29e2e1e809b8f741bb3c30533` 相同。准确候选八组/11 jobs/全部步骤 attempt 1 成功；准确主线八组/11 jobs/全部步骤成功，其中 deployment 首次上游 403 后 unchanged-code attempt 2 通过，其余七组 attempt 1。真实链路 75 项、独立 HY2 8/TUIC 8 与 Chromium 完整流程分别通过，详见[收口记录](docs/TUIC_CLOSURE_046.md)。

- `python -m unittest discover -s tests -p 'test_tuic_profile.py' -v`：服务端 ALPN 恰为 h3（不可省略）、单 UUID/密码对、独立空输入保留、普通响应 allowlist、未知/高级导入草稿保护与三格式映射
- `test_export_real.py`：固定客户端 config check；`test_tuic_loopback.py`：真实 Mihomo URI provider/converter 导入和转发。TUIC URI 为非官方客户端约定，不称官方通用标准
- `test_tuic_preflight_loopback.py` / `test_tuic_loopback.py`：分别裸前置与应用编译/匿名公开订阅双客户端链路。使用 `VUI_TEST_CORES` / `VUI_TEST_MIHOMO` 激活；分别只改变 UUID、密码、CA 或 SNI，要求真实 unknown user/token mismatch/x509 原因、固定目标零送达和无 DIRECT，超时不够
- `test_tuic_rejection_evidence.py` 验证凭据原因及新会话日志边界；`test_tls_rejection_evidence.py` 要求 x509 与具体 CA/SNI 原因来自同一日志行。共用有界失败观察不重置目标基线，不掩盖晚到请求
- `test_tuic_managed_certificate.py`：绑定/改绑/编辑/续期新 QUIC 会话、失败保留材料和已应用 revision、停止核心 `CORE_STOPPED_PENDING_APPLY`
- 激活 `test_inbound_editor_browser.py`：创建/取消/回填/编辑/刷新/再打开/三格式导出、UUID 与密码分别稳定、损坏后停机恢复；普通响应无秘密及证书卡 allowlist 继续覆盖
- `test_hysteria2_installation.py` / `test_firewall_support.py`、ACME、40 项 ToClash、四目标 Linux 与完整八组不减；`python scripts/check_docs.py` 校验所有当前版本入口，不改写历史版本

仅 HTTP/TCP；sing-box `network: tcp`，Mihomo TUIC adapter 硬编码 UDP 能力，省略无效 `udp: false`，不称关闭 UDP。QUIC 需要节点 UDP 可达，应用 UDP 未验收。固定 pin/构建不变，无真实 CA/防火墙变更、tag/Release、附件晋升或部署；完整边界见[TUIC 契约](docs/TUIC_046.md)。


## VMess TLS 拒绝取证回归（2026-10-12 候选）

历史 [f509 链路 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/38193879566/job/114632226906)
缺少必需 x509/unknown-authority 日志，unchanged-code attempt 2 才通过；
[原件与边界](docs/MANAGED_OPERATIONS_CLOSURE_20261012.md#首次失败保持原判)不改判。
本候选修复既有测试的一次请求后被动 2 秒等待，不改变产品、固定核心、TLS 校验或公开协议。

- 四条实际 VMess 调用（Mihomo/sing-box × CA/SNI）复用既有有界取证 helper。
  首个请求前捕获独立客户端日志边界，只接受起点位于该边界之后的同一日志行同时包含 x509 与指定原因；
  旧行未完成的追加后缀、分散在两行的片段、超时或普通 TLS 启动日志均不够。
- 使用 helper 原有 12 秒重试启动窗口；过期后不再启动请求。在期限内启动的请求
  可以稍后返回真实原因，因此不称整个测试严格 12 秒完成；单次 HTTP 请求仍有原 3 秒超时，
  客户端 stop 保留原有 TERM 等待 5 秒和必要 KILL 等待 3 秒。
- 所有重试共享固定零应用送达基线，不因重试或晚到请求重新计数；成功或异常都停止客户端，
  stop 自身报错仍检查最终计数。原请求/取证异常优先保留，清理异常以 cause 链保留，
  不让清理检查覆盖原始失败。成功输出选同一条具体原因行。
- `test_vmess_rejection_evidence.py` 直接调用四个实际测试方法，以确定性假时钟、假请求及临时日志
  复现等待、旧日志、送达和清理边界；它不启动真实核心，也不证明原 CI 网络失败的唯一原因。
  `test_tls_rejection_evidence.py` 另验共享 helper 的新行边界；原 TUIC/REALITY/XHTTP 负例保持。
- 本地入口：`PYTHONPATH=.:tests python -m unittest test_vmess_rejection_evidence test_tls_rejection_evidence test_tuic_rejection_evidence test_reality_rejection_evidence test_xhttp_contract -v`。
  实际二进制链路仍由准确最终提交的原 loopback CI 验证；默认 skip、确定性单测和历史真实链路分别记录，
  不把一次绿色 CI 或重跑声称为任意调度/网络下永久无失败。本候选结果另记对应 PR，不预填未来 head。

## v0.4.7 REALITY / Vision 已验收回归

最终候选 `70cc2f4` 与正常签名 master `6b049262` 已完成独立审查及准确八组验收，详见[收口](docs/REALITY_VISION_CLOSURE_047.md)。首次 master 链路继承 VMess/Mihomo CA 测试的被动 2 秒轮询未捕获必需 x509 原因，整组失败保留；unchanged-code attempt 2 成功不证明此不稳定性已永久修复。

- test_reality_profile.py / test_reality_export.py：严格存储/输入校验、密钥配对、隐藏/空保留、独立替换、未知导入保护、三格式和无服务端材料
- test_reality_certificate_boundary.py：真实隔离 API/SQLite 的回归先行，拒绝绑定不持久化、合法 legacy 保存后解绑、非法更新不变；不更改有效 TLS 的 desired/applied 失败语义
- test_reality_preflight_loopback.py：已通过固定核心裸前置；test_reality_loopback.py：编译器与匿名公开订阅集成。三种实际路径为 Mihomo YAML/sing-box JSON/Mihomo URI importer，五类负向保留实际原因、应用零送达、无 DIRECT
- reality_helpers.py 使用临时 CA、TLS 1.3/X25519/h2 本地参考；ClientHello、完整 TLS 与伪装 HEADERS 独立计数。异步 GET 不保证完成，不能强求 HEADERS>0。CA 只注入子进程，不改系统信任
- test_reality_rejection_evidence.py：超时/EOF/旧日志不够，应用请求不能用参考计数掩盖；startup/missing-evidence 失败路径也脱敏并抑制原异常上下文
- test_export_real.py 的服务器与双客户端真实配置检查，与 test_inbound_editor_browser.py 的创建/取消/编辑/刷新/再打开/独立替换/导出/故意损坏后停机恢复分别验收

最终八组与独立源码审查已经完成，后续修改仍不能用旧结果或裸前置替代。四 Linux 目标、40 项 ToClash、所有已验收协议/证书/安装门槛不减少，完整历史见[REALITY/Vision 契约](docs/REALITY_VISION_047.md)。

## v0.4.8 XHTTP 刻画前置

从 REALITY 准确 master `6b049262` 独立分支完成的 PR #25 已合并至 `66dbe70c`，候选 `f7996c5` 与主线各八组/11 jobs/全部步骤 attempt 1 成功，详见[收口记录](docs/XHTTP_CHARACTERIZATION_CLOSURE_048.md)。仅刻画测试/文档，`VERSION` 保持 `0.4.7`，不增加公开支持、生产参数、UI 或客户端导出。后续最终文档提交仍须自己的准确候选/主线验收及附件核验。完整固定来源、映射、负向证据与 HTTPUpgrade URI 缺口见[刻画契约](docs/XHTTP_CHARACTERIZATION_048.md)。

- `python -m unittest discover -s tests -p 'test_xhttp_contract.py' -v`：默认单元边界、夹具字段、原因检查与公开导出拒绝回归
- `test_xhttp_preflight_loopback.py`：通过现有 `VUI_TEST_CORES` / `VUI_TEST_MIHOMO` 激活固定 parser/真实前置，由既有 loopback glob 纳入；`xhttp_helpers.py` 仅用于测试夹具，不进入生产路径
- 官方 Xray 26.3.27 → Mihomo 1.19.32 原生 YAML 与真实 URI provider；显式 `stream-one`/TLS/h2/Chrome、单假 UUID/空 flow、SNI/Host/path，仅 HTTP/TCP
- sing-box 1.14.2 `transport.type:xhttp` 必须取得实际 `unknown transport type: xhttp` parser 拒绝；公开三格式拒绝回归不能省略
- 原生 mode parser 检查与真实转发分别运行；Mihomo `-t` 对非法 URI provider payload 也成功，不能代替 provider 启动、真实导入/h2/HTTP 转发
- 每条 YAML/provider 路径分别只改变 UUID/CA/SNI/path/Host/mode；先证明固定应用目标可达，每个新客户端会话独立日志边界，整个窗口零应用请求、无 DIRECT，并取得明确 UUID/x509/服务端 path、Host、mode 拒绝原因；上游 XHTTP 404/400 为固定源码行为，外层代理 `>=400` 不等于捕获上游状态
- wrong path 使用不相关前缀，wrong Host 使用不相关合法名称并保持 SNI，wrong mode 固定服务端 `stream-one`、客户端 `packet-up`；不把任意 path/Host/mode 差异当作拒绝
- 临时 CA 仅通过子进程 `SSL_CERT_FILE` 与空临时目录 `SSL_CERT_DIR`；不改主机信任、pin/构建、反代、生产诊断或凭据
- 默认 skip、历史本地 socket/netlink/Chromium EPERM、当前本地实际运行、配置检查和准确 CI 结果分别报告；不以 timeout/EOF/旧日志代替原因，也不将重跑当作已修复继承轮询不稳定性
- 保留完整八组、四目标 Linux、40 项 ToClash、PR #23 普通响应 allowlist、协议/编辑/证书/备份语义；无 tag/Release、附件晋升、真实部署、CA/账户或实际防火墙变更

首次前置本地执行：默认 487 项中实际 367 项通过、120 项明确环境 skip；XHTTP contract 7 项与选取 parser 3 项通过；最终工作树选取 10 方法（3 parser + 1 XHTTP 正向 + 6 负向）重验全部通过、无 skip，仅明确排除已有 netlink 阻断的 HTTPUpgrade-gap 方法。完整前置 11 方法中 10 方法通过，HTTPUpgrade 方法两项子测试在 sing-box 启动时报 netlink EPERM，整组 `FAILED (failures=2)`。XHTTP 两条路径正向/六类负向的通过与此阻断分别记录，独立服务端 TLS 1.3/h2 探测不称客户端抓包；这些本地结果不替代后续已完成的准确候选/主线 CI，详见[执行结果](docs/XHTTP_CHARACTERIZATION_048.md#首次前置本地执行结果2026-10-06)。

HTTPUpgrade 原样 URI 的晚期 VLESS read 错误不会可靠出现在固定 Mihomo 日志；首次候选 77c264ab 链路 100 项中该原因断言失败已保留。修正仅使用独立验证 TLS 的协议 recorder 和同 bytes 对真实固定 sing-box 的 replay：实际 GET Upgrade 与 raw VLESS header/假 UUID/TCP/目标必须明确区分，真实 HTTP400/正文、直接 URI 零送达、canonical 成功和普通 TCP 错误传输对照分别必需。recorder 不转发、不计作应用送达；EOF/超时/截断/错误 UUID/command/目标/TLS 或一般错误都不得通过。默认单元中的 mock recorder 不输出真实 TLS/核心通过消息。完整原因、首次失败与修正门槛见[刻画记录](docs/XHTTP_CHARACTERIZATION_048.md#首次准确候选-ci-失败与观察修正)。


### HTTPUpgrade recorder 的有界观察与收尾

`test_httpupgrade_recorder_evidence.py` 用确定性调用者回归和实际临时 TLS socket 分别验证记录器生命周期；合成字节不能称为真实核心证据。原生 YAML 和原样 URI 共用一个应用计数基线，最多三次请求启动、相隔至少四秒、十二秒观察窗口；第三次后仍被动观察到窗口结束。已接受但未完成的连接不追加请求，任何完整记录出现即停止重发；错误记录不能被后续成功覆盖。请求自身的既有三秒无响应读取可能越过重发启动窗口，不能把十二秒说成整个用例总时限。

记录器在启动线程前登记连接，最多接受十六个；TLS 握手与后续分片读取共用四秒绝对预算。先停止客户端，再释放已完成观察、停止接受并最多五秒等待全部 handler 收尾；线程启动、异常退出、关闭失败、溢出和排空超时都保留为失败。最终快照和固定应用计数必须在收尾后校验；异常路径同样检查送达，并保留原失败及嵌套清理原因。此处仍不转发、不生成协议响应，也不替代实际 sing-box HTTP400/正文、原样 URI 零送达、canonical 转发及普通 TCP 对照。

2026-10-12 的 [abbe489 链路首轮](https://github.com/ForceMind/V-UI/actions/runs/38207674929/attempts/1)在第二次 recorder 调用未观察到协议 bytes；同代码重跑成功分别保留。上述确定性负例证明旧观察/清理边界的不足，不证明那次 CI 网络时序的唯一原因，也不把重跑成功当永久修复。

### 仪表盘状态读取失败与恢复

`tests/dashboard_status_dom.js` 使用固定 Vue 的真实响应式/模板编译与未替换的 app.js，覆盖首次待读/失败、独立端点失败、合成超时、缺失/畸形成功数据、正常 false/0 和恢复；HTTP、时钟与 renderer host 是确定性双件，不称原生浏览器证据。`test_dashboard_status.py` 无固定资产时明确跳过，提供资产后严格要求全部控制通过，禁止下载或回退。

既有代理 Chromium gate 的 `test_mocked_dashboard_status_unavailable_and_recovery` 也调用严格 wrapper，再使用真实组件、合成 GET 回复和原有 3秒/10秒轮询验证首次 Unknown、失败后隐藏旧指标、两个端点独立恢复及有效 false/0。它不触发重启或其他写 API，不通过注入控制器/定时器绕过页面；合成状态不表示真实宿主指标、核心运行或用户环境已验。本地已知受限浏览器不重试，准确 CI 独立验收。

### 仪表盘窄屏布局与原生旧样式对照

`tests/dashboard_layout_browser.py` 在既有代理 Chromium 门禁内检查真正挂载的 `/ui/`，而不是离开仪表盘后的独立代理页。390 / 768 / 1440px 分别检查页面、页头、四张卡片及 CPU/Memory 仪表的实际几何；卡片内部被裁切或横向滚动也算失败，不能仅凭元素 visible 或页面没有横向滚动通过。正常场景使用真实只读状态 API，保留现有导航，测试不触发核心重启或其他写操作。另将经过真实鉴权的 GET 回复明确替换为长 OS/用户名及 Unknown，专门检查这些展示状态，不称真实账户或宿主数据。

`tests/fixtures/dashboard_pre_responsive.css` 保存准确旧样式 bytes，来源提交与摘要由 helper 和普通完整性控制核验。原生场景先仅替换这个同源 CSS 回复、保持同一模板/控制器/固定组件，取得明确的旧布局几何反例，再移除替换并重新加载候选样式验证正向。替换保持安全响应头，不增减策略；现有 `/ui/` 由 StaticFiles 提供且没有 CSP 头，这一事实不得改称 CSP 保护，导航后的 `/proxy` 仍核其既有 same-source CSP。缺库、HTTP/页面错误、超时和任意异常都不是旧布局失败证据。没有运行前只能称静态约束与待验修复，不能提前声称已在浏览器复现；本地受限浏览器不重试，准确 CI 的几何验收也不代表手机实机、触控或移动网络资格。

### 节点编辑器保存控件回归

`tests/editor_saving_controls_dom.js` 使用现有 checksum-pinned Vue / Element Plus 的真实禁用计算、产品模板和控制器，区分继承禁用与显式 false 覆盖，并覆盖六处控件及 create/edit 保存成功/拒绝。`tests/test_editor_saving_controls.py` 仅在未提供 `VUI_FRONTEND_ASSETS` 时明确跳过；提供资产后必须完整通过，不下载回退资源。既有代理 Chromium gate 的对应控件场景也调用此严格 wrapper，确保 CI 下载固定资产之后实际执行这些控制。Chromium 使用明确模拟的保存响应验证组件锁定，不替代实际 API/核心应用或用户环境验收。

## 陈旧节点编辑/删除意图回归（2026-10-23 候选）

`test_inbound_instance_preconditions.py` 使用真实模型/API 函数、临时磁盘 SQLite 与独立写连接，覆盖自然 ID 重用、同实例凭据保持、严格条件、事务锁和副作用前拒绝；没有 HTTP/鉴权/浏览器/核心资格。`test_inbound_instance_frontend.py` 执行未替换的 app.js 与确定性 API/确认框 doubles，检查快照捕获、失败关闭、草稿和生命周期；不把双件当真实网络。

既有 `VUI_PROXY_BROWSER=1` 门禁中的 `test_actual_inbound_instance_conflicts_preserve_target_and_draft` 调用 `inbound_instance_browser.py`，以固定前端资产、真实登录/API/临时 SQLite 和 Chromium 验证旧编辑器/确认框跨自然删建冲突，以及明确刷新后重试。该隔离浏览器服务故意没有核心，成功 DB 写入明确核对 `saved:true/applied:false`，不能称核心应用或用户实机已验。原完整门禁、CSP、断言和工作流不削弱，本地受限浏览器不重试绕过，准确 CI 独立验收。

### 严格导出 DNS 夹具端口回归

`tests/test_strict_export_fixture_ports.py` 直接执行现有 strict-export 客户端构造方法，以假进程 / socket / 系统观察验证 DNS 端口使用既有自动区间外双协议分配器，普通 mixed 端口仍沿用原分配。两格式、错误 DNS 身份、已占 UDP、前一个客户端、错误传播及原精确 TCP+UDP / loopback listener 断言均须保留。运行 `PYTHONPATH=.:tests python -m unittest test_strict_export_fixture_ports -v` 不启动核心或真实监听，不替代准确提交的原 strict-export loopback CI。

这里只读内核区间和非特权下限，分配后不预留、启动失败后不重选；显式竞争和固定端口重启冲突仍应失败。PR66 主线首次 DNS UDP bind 失败的历史占用者未知，确定性模型证明的是旧分配机制缺口，不是已还原该次事件，也不证明消除了全部端口竞争。共享分配器与生产导出协议均不变，受限 UDP D2 不在此范围。

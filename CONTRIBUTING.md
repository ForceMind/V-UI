# 开发与验证

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
| Real loopback proxy and DNS chain | 实际 VLESS/TCP/TLS、Trojan、Shadowsocks、VMess，已验证 VLESS/WS/TLS、VLESS/gRPC/TLS，已验证 Hysteria2/TLS 及候选 TUIC v5/TLS 的双客户端链路与失败路径；DNS、拒绝/停启 |
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


## v0.4.6 TUIC v5 候选验收

当前 [Draft PR #22](https://github.com/ForceMind/V-UI/pull/22)先通过固定官方二进制裸前置（准确 `21cb0bc7`，八组/11 jobs/全部步骤 attempt 1，链路 69 项），再正常 merge-forward 继承 [PR #23 安全修复](docs/INBOUND_RESPONSE_CLOSURE_20261006.md)。当前集成重建，不复用丢失的本地证据；最终独立审查、八组 exact-head、授权正常 merge 和八组 exact-master 仍待完成。

- `python -m unittest discover -s tests -p 'test_tuic_profile.py' -v`：服务端 ALPN 恰为 h3（不可省略）、单 UUID/密码对、独立空输入保留、普通响应 allowlist、未知/高级导入草稿保护与三格式映射
- `test_export_real.py`：固定客户端 config check；`test_tuic_loopback.py`：真实 Mihomo URI provider/converter 导入和转发。TUIC URI 为非官方客户端约定，不称官方通用标准
- `test_tuic_preflight_loopback.py` / `test_tuic_loopback.py`：分别裸前置与应用编译/匿名公开订阅双客户端链路。使用 `VUI_TEST_CORES` / `VUI_TEST_MIHOMO` 激活；分别只改变 UUID、密码、CA 或 SNI，要求真实 unknown user/token mismatch/x509 原因、固定目标零送达和无 DIRECT，超时不够
- `test_tuic_rejection_evidence.py` 验证凭据原因及新会话日志边界；`test_tls_rejection_evidence.py` 要求 x509 与具体 CA/SNI 原因来自同一日志行。共用有界失败观察不重置目标基线，不掩盖晚到请求
- `test_tuic_managed_certificate.py`：绑定/改绑/编辑/续期新 QUIC 会话、失败保留材料和已应用 revision、停止核心 `CORE_STOPPED_PENDING_APPLY`
- 激活 `test_inbound_editor_browser.py`：创建/取消/回填/编辑/刷新/再打开/三格式导出、UUID 与密码分别稳定、损坏后停机恢复；普通响应无秘密及证书卡 allowlist 继续覆盖
- `test_hysteria2_installation.py` / `test_firewall_support.py`、ACME、40 项 ToClash、四目标 Linux 与完整八组不减；`python scripts/check_docs.py` 校验所有当前版本入口，不改写历史版本

仅 HTTP/TCP；sing-box `network: tcp`，Mihomo TUIC adapter 硬编码 UDP 能力，省略无效 `udp: false`，不称关闭 UDP。QUIC 需要节点 UDP 可达，应用 UDP 未验收。固定 pin/构建不变，无真实 CA/防火墙变更、tag/Release、附件晋升或部署；完整边界见[TUIC 契约](docs/TUIC_046.md)。

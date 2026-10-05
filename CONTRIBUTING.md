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
| Real loopback proxy and DNS chain | 实际 VLESS/TCP/TLS、Trojan、Shadowsocks、VMess，已验证 VLESS/WS/TLS，以及候选 VLESS/gRPC/TLS 的双客户端链路与失败路径；DNS、拒绝/停启 |
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

## v0.4.4 gRPC 候选验收

固定官方 sing-box 1.14.2 无 `with_grpc`，实际 gRPC Lite；Mihomo 固定 1.19.32。不得为获得标准 gRPC 重编译核心、换 pin、新增反代、依赖或后端。前置 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 的真实链路 45 项测试通过，集成候选的最终独立审查/准确候选和主线八组 CI 尚待完成。详细链接、首次失败和已知限制见[阶段契约](docs/VLESS_GRPC_044.md)。

- `python -m unittest discover -s tests -p 'test_vless_grpc_profile.py' -v`：严格 `[A-Za-z0-9._-]{1,128}` 字面 service name，不 trim/强转；flow、ALPN（含显式 null 拒绝）、未知字段、三格式映射、编辑往返、秘密和已导入字段保护。
- `test_vless_grpc_credentials.py`：已有 gRPC 节点缺失/畸形 UUID 的编辑返回 422，不生成替代；省略 flow/fingerprint/skip 字段保留原值，不使畸形导入变得可公开。
- `python -m unittest discover -s tests -p 'test_vless_grpc_managed_certificate.py' -v`：托管证书绑定、严格编辑和原子失败；`test_certificates.py` 覆盖实际应用/续期及停止待应用状态。
- `test_export_real.py`：真实 Mihomo/sing-box 检查公开导出，四种独立 Chrome/h2 组合；不能把 config check 写成真实连通。
- `test_vless_grpc_preflight_loopback.py`：保留裸核心互通前提及负向诊断；`test_vless_grpc_loopback.py`：编译后通过公开订阅生成配置的集成链路。二者设置 `VUI_TEST_CORES` 和 `VUI_TEST_MIHOMO` 才运行，不把默认环境 skip 当作通过。
- 实际 h2、四种指纹/ALPN组合、字面 `.`/`..`/128 字符 service、两种客户端错误 UUID/CA/SNI/service/case 均覆盖；先证明目标 IP 可达，失败目标不得收到请求且不得 DIRECT。
- 仅负向测试 sing-box 子进程加 `GODEBUG=http2debug=1` 获取真实 x509 CA/SNI 证据。实际 Lite 调用者仍可能超时，不能声称及时错误传播；保留第一次缺少 x509 日志的失败，不改 TLS 校验/系统信任或固定二进制。
- 激活 `test_inbound_editor_browser.py`，验创建/取消/编辑/刷新/再打开、UUID 保留、证书回填、三格式与停机备份恢复；续期不得启动手动停止的核心，保持 `CORE_STOPPED_PENDING_APPLY`。

仅 sing-box/VLESS/gRPC/TLS、单 UUID、空 flow、明确验证 SNI、ALPN 省略或 `["h2"]`、Chrome 独立可选、HTTP/TCP。Mihomo `udp: false`、sing-box `network: tcp`；不扩大到 UDP、h2c、Xray gRPC、authority/Host enforcement、额外 headers/timers/multi-mode、HY2/TUIC 或发布/部署。失败、环境阻断、未运行、前置通过和最终集成通过要分别报告。

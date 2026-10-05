# 变更日志

## 0.4.3 — VLESS/WebSocket/TLS（候选，最终验收待完成）

- 从已收口的 v0.4.2 主线开始独立 WS 版本；不改变 FastAPI/SQLite、40 项 ToClash、四目标 Linux、托管证书编辑和发布边界。
- 新增 sing-box 1.14.2 / VLESS / WebSocket / TLS 的严格参数和 URI/Base64、Mihomo、sing-box 导出；单 UUID、空 flow、明确 SNI、正常证书校验，ALPN 省略或仅 `http/1.1`，可选 Chrome fingerprint。
- path 与可选 DNS-style Host 使用共享严格校验，拒绝 query、fragment、百分号转义、dot segments、early data 和未知字段。Host 保存在客户端元数据，实际服务端配置去除；不是 Host 白名单，也不代替 TLS SNI/证书验证。
- 验收门槛包括两种固定客户端的正向 HTTP/TCP、错误 UUID/CA/SNI/path 拒绝、不同合法 Host 接受，以及浏览器创建/取消/编辑/刷新/恢复和证书生命周期；不新增 UDP 声明，不放开其他 WS/gRPC/core 组合。
- 最终准确提交八组 CI 尚待完成。未创建本候选的 Draft Release、tag、公开 Release 或部署；不能把下述 v0.4.2 主线结果当作本候选通过。

## 2026-10-05 — v0.4.2 主线收口

- PR #1–#13、#15–#17 共 16 个 PR 正常合并至 `master` `0225ce4b1301e70068421e54c409b303d45cf812`，tree `1a642d38bbd55d8cd12ebfabdbd421624a7a08f3`；PR #14 排除。
- 最终主线八组最新工作流及 11 个 job 成功，四目标 Linux 附件摘要/清单/源码与版本核验完成。没有 Draft Release、新版本 tag、公开 Release 或部署。
- 这是候选阶段之后的状态更新；以下历史条目保留当时的候选描述与验收边界。来源见[主线收口记录](docs/MAINLINE_CLOSURE_20261005.md)。

## 0.4.2 — VMess/TCP/TLS（候选）

- 新增 sing-box VMess/TCP/TLS strict public export：Mihomo YAML、VMess URI/Base64、sing-box JSON。
- 仅接受单 UUID 用户、TCP、TLS、证书校验与明确 SNI；未知字段、非 TCP、关闭 TLS 明确拒绝。
- 新增真实 sing-box VMess 服务端 + Mihomo/sing-box 客户端 loopback，覆盖正确 UUID、错误 UUID、错误 CA/SNI 与 DIRECT 失败回退检查。
- 正常 merge-forward 继承 PR16/PR15/PR13/PR12 修复与三 cipher 双客户端 Shadowsocks TCP/UDP 验收。
- 修复入站编辑和证书卡片两处 VMess/TLS 托管证书选择器缺失，新增前端/API 正反回归和真实浏览器创建、取消、编辑、刷新、UUID 保留、三格式导出与停机恢复。
- 既有 VMess 负向用例补齐 sing-box 客户端的错误 UUID/CA/SNI；要求目标无请求、无 DIRECT 回退，两个客户端均保留真实 x509 CA/SNI 失败证据。
- 当前入口文档对齐 v0.4.2 候选，最终结果以 exact-head 全组 CI 为准，未公开发布。

## 0.4.1 — Shadowsocks AEAD 候选

- 正常 merge-forward 继承 PR15/PR13/PR12 已验收修复，当前文档和安装示例对齐候选版本。
- 保留三种 AEAD cipher 的 strict 导出和固定真实客户端配置检查，以及 method 编辑时的密码保护。
- 补齐三种既有 cipher 的双客户端真实 TCP/UDP 正向及错误密码/错误 method 拒绝用例，要求目标不收到失败请求且不 DIRECT 回退。

## 0.4.0 — Trojan/TCP/TLS 候选

- 补齐合入后的协议默认安全模式回归：Trojan 省略/空/null security 仍按 TLS 保护续期；只有替换两条手工材料路径才可解除绑定。VLESS none/REALITY 与空 profile 语义保留。
- 正常合入已验收的 PR #13 节点编辑与 PR #12 Linux 安装修复；当前安装、发布和文档索引示例统一跟随 VERSION，并增加防漂移回归。
- 新增 sing-box Trojan/TCP/TLS strict public export：Mihomo YAML、Trojan URI/Base64、sing-box JSON。
- 单密码用户、TLS/SNI/证书校验、可选 ALPN/Chrome fingerprint 经过固定客户端验证。
- 真实 sing-box 服务端 + Mihomo 客户端 loopback 验证正确密码连通、错误密码拒绝、错误 CA/SNI 拒绝且不回退 DIRECT。
- 托管证书、自动续期和证书页面绑定扩展到 Trojan/TLS 节点。
- 未验证的 Xray Trojan、WS/gRPC Trojan 等继续明确拒绝，不随本版本放开。

## 0.3.2 — 节点编辑修复候选

- 合入已验收的 PR #12 Linux 安装修复，保留依赖 PR 链与发布边界。
- 修复托管 TLS 节点切换 `none` / `REALITY` 后被隐藏证书选择改回 TLS，以及非 TLS 解绑错误要求证书路径的问题。
- 保留 TLS 手工解绑保护、秘密保留和核心/协议锁定；空 profile 不会意外解除续期。
- 增加实际 Vue/Chromium、固定核心与临时 CA 的创建、编辑、取消、刷新、再编辑、TLS 导出和停机恢复回归；该编辑测试不扩大协议支持范围。

## 0.3.1 — Linux 可移植安装与发布链

- Linux 安装回归修复：OpenRC HTTP-01 在 bind 前设置 IPv6-only；新安装双栈预检 HTTP-01/默认节点端口；firewalld 仅处理明确的活动接口 zone，歧义时保留现有规则并等待人工核对。

- 安装器从 Ubuntu 24.04/amd64 白名单改为检测发行版、CPU、libc、init、包管理器和防火墙能力。
- 支持 x86_64 / ARM64 与 glibc / musl 四种目标运行包，运行服务使用固定便携 CPython 3.12 和 hash-locked wheels。
- sing-box 按 glibc/musl 选择官方对应构建；Xray 按 CPU 架构选择固定官方构建。
- systemd 与 OpenRC 分别使用受管服务后端；主面板保持非 root。
- 安装前检查 TCP 80、面板端口和默认节点端口。UFW/firewalld 只有用户明确确认后才修改；自定义 nftables/iptables 和云安全组仅提示人工处理。
- 在线 `install.sh --version` 自动检测目标并下载对应 Release 包。
- 正式发布门槛增加 portable Linux matrix；四个目标包必须来自同一 exact-head 提交的已验收 artifact，发布阶段不重新编译。


## 0.3.0 — 正式发布准备

本条记录代码目标版本，公开发布日期由正式Release动作确定，不把候选CI完成时间写成已公开发布。

### 新增

- 单管理员认证、会话撤销、持久限流、默认管理鉴权与独立只读订阅。
- 双核心安全配置应用、候选校验、手动停启与恢复状态。
- ToClash固定参考集成，完整Mihomo配置、40项服务目录、自定义DNS/规则与持久分流工作区。
- Certbot HTTP-01图形化签发、测试/正式隔离、自动续期、到期与任务状态、面板热更新、TLS节点绑定。
- Ubuntu24.04 amd64一键受管安装，非root服务、systemd自启和HTTP-01 socket、首次证书和管理员引导。
- 固定依赖离线套件、完整清单/摘要、备份恢复、升级失败保护及人工受门槛约束的正式发布流程。
- 用户、证书、运维、API、兼容、安全、贡献与发布文档。

### 修复与收口

- 不再使用mock登录或公开管理/订阅凭据出口。
- 不再把无节点/未知组合静默导出成DIRECT，也不把生成器测试当成真实兼容性证明。
- 移除被隔离站点导入时写入安装目录的副作用；主机防火墙操作不再假报成功。
- 核心固定版本字段经实际二进制核验；旧笼统REALITY兼容声明不作为支持承诺。
- 证书签发与消费者应用状态分离，续期失败不覆盖旧材料，不自动启动手动停止的核心。

### 已知边界

公开导出与实际链路首轮仅覆盖sing-box/VLESS/TCP/TLS单用户、空flow、验证证书。其他协议、UDP、DNS-01/通配符、ACME泛化provider、Docker/ARM64和任意YAML导入不在本版本验收范围。

历史各阶段提交与CI见[迭代记录](docs/ITERATIONS.md)。

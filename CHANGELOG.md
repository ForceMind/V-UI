# 变更日志

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

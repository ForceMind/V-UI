# V-TS-02：默认关闭的只读状态候选

日期：2026-10-21。基线 master `66f7ff74247741b1fe3e5f1610e1d33d52d42f58`，关联 [Issue53](https://github.com/ForceMind/V-UI/issues/53)。本阶段只实现可选状态读取与假数据/临时子进程测试，不安装、启动或认证 Tailscale，不改网络、权限、监听、证书、核心、VERSION 或工作流。受限 UDP D2 不恢复；[共存与回滚矩阵](TAILSCALE_MANAGEMENT_ACCEPTANCE.md)仍全部独立待验。

## 固定读取边界

- 默认关闭。仅已有部署由管理员另行设置 `VUI_TAILSCALE_STATUS_ENABLED=1` 才允许读取；安装器不设置它。关闭时 API 返回 `enabled=false`、页面卡片隐藏、不查找或执行程序。
- 托管 systemd/OpenRC launcher 只向 panel 模式转交此单一显式环境项，且只接受字面 `0` / `1`；省略保持关闭，非法值在 exec 前拒绝且不回显输入。HTTP-01 两种模式不继承该项；认证材料、程序/socket 覆盖、loader/Python 注入仍被过滤。不增加 service.json 字段或安装器设置，不写真实服务环境、不启用读取。
- 独立管理员 GET `/api/management/tailscale` 继承现有会话和 Host 检查；不接受查询参数、请求体、目标、命令或刷新绕过。普通系统状态、节点和订阅不含此数据。
- 固定 argv：`/usr/bin/tailscale --socket=/var/run/tailscale/tailscaled.sock status --json --peers=false`。无 shell、无 PATH 搜索、无调用方/环境 socket 覆盖；最小固定环境，stdin/stderr 为 DEVNULL。不使用 sudo，不申请 socket/组权限，不安装、登录、重启、修改或重试命令。
- 单进程、单次采样全局上限：2 秒执行预算，最多 65536 stdout bytes，JSON 最多 1024 节点及 16 层。错误、截断、超限、无权限或未知模式都只输出固定未知原因，不保留或记录原始 bytes/异常。
- 在独立进程组执行，用 Linux waitid/WNOWAIT 观察退出并保留进程身份，先终止组再回收 leader；超时/溢出终止并最多 0.5 秒等待回收；启动系统调用和内核不可中断状态不承诺硬实时。回收不确定时熔断后续采样，不能堆积后台进程。无后台轮询或进程常驻。
- 成功与失败均缓存 30 秒，自上次采样开始至少间隔 30 秒，缓存有效期从采样开始算起。并发请求不排队或启动第二进程；过期/正在采样只显示未知，绝不把旧在线显示为当前。刷新不能绕过限制。

## 白名单与诚实状态

API 只提供 `enabled`、`state`（online/offline/unknown）、固定 `reason`、`observed_at`、`stale`、`valid_for_seconds`。没有 tailnet 地址、设备/账户/peer 标识、认证 URL、密钥、原始 JSON、原始错误或任意上游字符串。缓存只保存投影结果。

源码核对基于官方 [v1.104.2 status CLI](https://github.com/tailscale/tailscale/blob/v1.104.2/cmd/tailscale/cli/status.go)、[Status/PeerStatus](https://github.com/tailscale/tailscale/blob/v1.104.2/ipn/ipnstate/ipnstate.go)与[版本格式](https://github.com/tailscale/tailscale/blob/v1.104.2/version/version.go)。只接受 `1.104.2` 或其 release `-t<6–40 hex>` / `-t<6–40 hex>-g<6–40 hex>` 长版本；开发版、未知版本/字段类型/状态均未知。版本识别不是二进制来源或完整性证明，不自动安装或升级到此版本。

`Running` 且 `Self.Online=true` 仅表示本机连接控制平面；`Running` 且 false 或 `Stopped` 显示离线；NeedsLogin/NeedsMachineAuth/Starting/NoState 显示未知，不把设备可见性当成登录成功。任何状态都不证明浏览器经过 tailnet、HTTPS/登录可达、业务中转或共存/恢复通过。没有可靠目标 peer 数据，所以不显示链路类型或管理 URL。

## 页面与验收

在既有代理页增加默认隐藏的独立只读卡片，不增加动作型设置。手动刷新单飞；没有浏览器自动网络轮询。读取失败/过期立即撤销在线显示，离开/恢复页面、隐藏后返回和延迟响应均不得复活旧在线状态。只用 textContent，固定文案，不显示服务端未列入白名单的内容。

必须覆盖：默认关闭零执行、所有鉴权与 Host 边界、不支持的方法/参数、固定 argv/环境、缺程序/权限、timeout、溢出、非零退出、无效/重复字段/过深 JSON、未知版本、敏感字段过滤、并发/缓存/限频、异常清理/熔断、页面重复点击/过期/失败/返回。真实临时 Python 子进程只能证明采集器生命周期，不称 Tailscale 或真实核心资格。原八组 CI 与准确提交独立审查仍是合并门槛；发布和生产部署不在本期。

## 证据记录

- 基线只读复核：PR52 已合并；上述准确 master 的八个既有 push workflows / 11 jobs 均 success，无旧 head 重跑。
- 已实现候选包含固定采集器、独立管理员 API、隐藏卡片、29 项聚焦控制和既有 browser gate 中的一个新场景。纯测试覆盖临时 Python 子进程，不运行 Tailscale。
- 设计独审确认资源/权限边界；代码独审指出返回页面后不可刷新、先回收 PID 再发进程组信号的竞争及清理错误优先级，已修正并加入回归。最终审查与准确提交 CI 结果在对应 PR 更新。
- 首次本地宽扫：1994 tests，1788 passed、206 explicit skips、零失败，181.394 秒；后续最终代码另行重验。skip 不授真实资格。
- 本地真实 Chromium 尝试：1 test / 1 error，浏览器启动发生 `socket() failed: Operation not permitted` / SIGABRT，场景尚未执行。保留失败，不改系统权限；正常候选 CI 中的实际 browser 结果另记，不以 DOM 控制冒充通过。
- Tailscale 未安装、未执行；真实共存、URL/身份恢复、四目标上运行 Tailscale、1 vCPU/512 MiB 整机与 24h 均未测，不继承基线资格。

## PR54 后的 launcher 集成修正

[PR54](https://github.com/ForceMind/V-UI/pull/54) 候选 `cd2025f8136f5af711397fe8b750491a29f16761`、tree `6b8943907811b7edbf1f12eca98b6c24f6962f5b` 的八组 CI / 11 jobs / 126 steps attempt 1 成功后，正常合并至 `7512051dfa192a8f623a3cebe3cb43d37f7f42e0`。候选 Test 中真实 Chromium proxy gate 21/21 通过（含合成状态场景），不替代真实 Tailscale。准确候选本地宽扫 2000 tests / 1794 passed / 206 explicit skips；上面的本地浏览器启动失败仍保留。

合并后读到[晚到的 P1 审查](https://github.com/ForceMind/V-UI/pull/54#discussion_r4339410372)：托管 launcher 原严格过滤器遗漏可选状态环境项，导致标准安装即使外部显式设置也仍关闭。合并前只检查 review 状态、未读取新 inline 内容，是门禁检查遗漏；原绿灯没有覆盖这一集成缺口，不能称其完整可用。独立修正只增加上面的 panel 白名单，覆盖四目标、三种启动模式、默认/开/关/非法值、敏感项过滤，以及 fake exec → 真实观察器 + fake collector 的状态控制。没有执行 Tailscale 或修改真实服务设置。

修正必须独立审查、准确 head 全套 CI，并在合并前重新读取最新 review 和 inline 内容；合并后准确 master 单列核验。既有安装器正常从已验证包提取 launcher，源码修正不自动更新旧安装，也不授权现在部署或打开选项。

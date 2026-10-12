# V-UI 0.4.7

## 2026-10-12 集成候选更新（未发布）

本候选在产品 `0.4.7` 上沿历史继承 PR #28–33 的低资源、后台与菜单工作，详见[完整矩阵](INTEGRATED_OPERATIONS_CANDIDATE.md)。它们尚未合并，当前主线仍为 `a6aa84d`；本次未创建 tag/Draft Release/公开 Release，未向 Release 上传或晋升资产，也未操作用户主机。

### 已实现的集成范围

- 低资源：流式完整读取、私有文件缓存生命周期、GNU allocator、状态轮询及恢复保护，保留完整摘要/成员/路径/权限与数据保全检查
- 网页后台：不支持的主机安全控制改为明确的只读说明，覆盖键盘、重复点击/导航及会话过期；不把说明入口视为实际封禁能力
- 包内服务用户菜单：只读状态、安装记录、明确选择后的完整校验、可信本地候选准备，不自动提权
- 全局受管菜单：管理员须从完整独立可信 kit [显式安装 root-owned 控制组件](MANAGED_OPERATIONS.md)，再运行 `sudo /usr/local/sbin/v-ui`；默认 `install.sh` 不添加全局入口。systemd 支持启停/重启、停服备份、恢复、候选激活/代码回退和中断恢复；OpenRC 只查询与纯候选准备，拒绝启停及依赖停服的数据写动作。单独切换 CURRENT 不更新 root-owned 控制组件、launcher、安装记录或服务模板
- VMess 测试：两客户端 CA/SNI 负向改用既有有界拒绝证据收集器，移除被动 2 秒轮询；仍要求同一新日志行中的真实 x509/指定原因、零应用送达、无 DIRECT、停进程后的送达检查及失败清理。本地合成 RED 只证明旧调用方的契约缺口，不能唯一归因历史真实网络失败

### 准确证据与仍待完成的门槛

[PR #33](https://github.com/ForceMind/V-UI/pull/33) 的文档基线 `09f2cf4ec39adfbb345d8aa40b94558c64c93936` 已完成普通八组、11 jobs、119 steps 全部 attempt 1，并核验 9 个原始 artifact。真实受管 root/systemd 资格仍准确属于 `f50942f1b399c7c397df532d04f9143e2527aaa8` 的 [run 38203515936](https://github.com/ForceMind/V-UI/actions/runs/38203515936)：空白临时 Ubuntu 24.04 x86_64/systemd/cgroup v2、11 个必需阶段（含 cleanup）及自有资源清理确认；没有在 09f2 重跑 root。本次含测试修复的新 head 尚待独立审查、自己的普通八组 CI 和同提交原件核验，不能继承旧提交的通过结论或预填未来 SHA。

[f509 收口](MANAGED_OPERATIONS_CLOSURE_20261012.md)保留首次 VMess x509 原因缺失、unchanged-code attempt 2、旧 root harness 失败及清理未确认事实；后续通过不抹去它们。普通四目标包消费与真实 root/init 分开记录，合成同代码 A→B→A 不等于真实跨版本/schema 迁移，构造中断恢复不等于真实断电实验。

[低资源收口](LOW_RESOURCE_CLOSURE_20261011.md)仍只支持有界软件结论：历史大数据用例已到 512 MiB、零余量，短 cgroup smoke 不能拼成真实 1 vCPU/512 MiB 整机或连续 24 小时完整负载验收；160 MiB 完整空载/320 MiB 安装升级预算、其他平台真实 root/init 仍未达证。协议、固定 pin、四 Linux 目标和 SNI/网络范围不扩展，UDP/DNS 等后续阶段未实现，XHTTP/HTTPUpgrade 公开导出仍阻断。

[人工发布流程](RELEASING.md)现按发布脚本聚合 19 项资产；默认 `publish=false` 也会创建 Draft Release 并上传资产，绝非 dry-run。候选验收不授权合并、tag、Release、附件晋升或用户主机部署。

## 2026-10-06 协议发布准备记录（历史）

以下保留当时状态和各阶段证据；其中“本次/当前”指原记录，不替代上述新候选的准确验收。

REALITY/Vision 已由 [PR #24](https://github.com/ForceMind/V-UI/pull/24) 完成独立审查、准确候选八组、正常合并和准确主线八组，见[收口记录](REALITY_VISION_CLOSURE_047.md)。本文件为发布准备说明；截至 2026-10-06 本次检查，没有创建正式 Release、附件晋升或生产部署。[PR #25 XHTTP 刻画](XHTTP_CHARACTERIZATION_CLOSURE_048.md)也已完成准确候选/主线验收，不改变产品版本 0.4.7 或公共支持。产品范围冻结；本次最终文档提交的 CI 与同提交四目标附件核验以对应 PR/发布准备证据为准，本文不预先声明通过，也不能以旧提交的结果替代。

## 继承基线

[v0.4.2](MAINLINE_CLOSURE_20261005.md)、[WS](VLESS_WS_CLOSURE_043.md)、[gRPC](VLESS_GRPC_CLOSURE_044.md)、[HY2](HYSTERIA2_CLOSURE_045.md)、[PR #23 普通响应安全修复](INBOUND_RESPONSE_CLOSURE_20261006.md)与 [TUIC v0.4.6](TUIC_CLOSURE_046.md)分别保持已验收边界，PR #14 仍排除。TUIC master `df8a980b` 八组/11 jobs/全部步骤成功，deployment 首次上游 403 后 unchanged-code attempt 2 通过，其余七组 attempt 1。旧[候选发布说明](RELEASE_NOTES_046.md)保留为历史。

## 已验收范围

- sing-box 1.14.2 服务端/客户端与 Mihomo 1.19.32，官方 pin/摘要/构建不变；VLESS、direct TCP、精确 `xtls-rprx-vision`、一个 UUID、一个规范 16 位 hex short ID、匹配 X25519 公私钥、显式 SNI、Chrome
- VLESS URI、Mihomo YAML、sing-box JSON 保留必要客户端参数；私钥及参考握手地址仅在服务器。无 ALPN 覆盖、托管证书、额外传输/核心、multiplex 或应用 UDP 扩围
- UUID/私钥/short ID 不回显，空输入保持；独立 UUID/short ID 替换，未知/高级导入字段拒绝，不能静默清除后公开。普通响应保持 PR #23 allowlist
- 参考 TLS 握手与应用目标分别计数；认证失败可产生参考回落，异步伪装 GET 不保证完成。错误 UUID/flow/key/short ID/SNI 要求真实拒绝原因、应用零送达、无 DIRECT；不以超时/EOF 代替
- REALITY 证书绑定在持久化前拒绝；有效 legacy raw 安全切换在保存后清除旧绑定。复现只观察到旧代码残留 binding/error 状态，未观察到 REALITY 被续期覆盖；有效 TLS 的 desired/applied 与停止待应用语义保留

## 当前证据

裸前置准确 `a44b5ce20edcaee1ee3b26c34b7c21badfe5bf7b` 八组/11 jobs/全部步骤 attempt 1 成功。[链路 82 项](https://github.com/ForceMind/V-UI/actions/runs/37471617751)及独立 HY2 8/TUIC 8 通过，Mihomo YAML、sing-box JSON、实际 URI importer 均验证正向和五类负向。首次 `6776950` 的 9 个伪装 HEADERS 断言失败保留，修正没有更换核心或放松认证/应用送达门槛。

最终候选 `70cc2f4` 与正常签名 master `6b049262` tree 相同；候选八组全部 attempt 1，主线八组最终成功。主线链路首次继承 VMess/Mihomo CA 用例缺少必需 x509 原因，unchanged-code attempt 2 取得真实 unknown-authority 原因后通过，其余七组 attempt 1；该重跑不证明被动日志轮询不稳定性已永久修复。真实链路 89 项及独立 HY2 8/TUIC 8，公开订阅、严格编辑、Chrome/秘密回填、取消/刷新/再编辑/导出及损坏后停机恢复均有准确主线证据。原有 ACME/安装/40 项 ToClash/四目标 Linux 与完整八组不减，见[主线收口](REALITY_VISION_CLOSURE_047.md)；完整来源与前置/审查失败历史见[阶段契约](REALITY_VISION_047.md)。

[PR #25 XHTTP 刻画](XHTTP_CHARACTERIZATION_CLOSURE_048.md)最终候选 `f7996c5` 与签名 master `66dbe70c` 同 tree，分别八组/11 jobs/全部步骤 attempt 1 成功。链路各 101 项、独立 HY2 8/TUIC 8；默认 496 项中 375 执行、121 明确 skip，真实核心与继承 REALITY 完整 Chromium 流程分别验收。XHTTP 的 Mihomo YAML/实际 URI 路径正向及六类负向通过；HTTPUpgrade 原生成功、原样 URI 失败、独立协议 recorder、同 bytes 验证 TLS replay 的实际 HTTP400 与普通 TCP 对照分别记录。sing-box 1.14.2 不支持 XHTTP，HTTPUpgrade 仍有固定 Mihomo URI 转换缺口，公开导出保持阻断。首次 `77c264a` CI 缺少晚期日志原因的失败及源码诊断/审查修正保留于[刻画记录](XHTTP_CHARACTERIZATION_048.md)。

历史本地 netlink/socket/Chromium EPERM 按原结果保留，不写成运行通过；当前 XHTTP 本地运行与 HTTPUpgrade netlink 阻断另见刻画记录，不泛化为所有本地测试不可运行。源码、配置检查、真实链路、浏览器、包验证、发布与部署分别记录。本轮未新建 tag/Draft Release/公开 Release、晋升附件、操作真实 CA/账户或修改实际防火墙；已有历史 `v1.0.0` tag 不变。先完成本版发布准备与交付决定，再推进独立 UDP/DNS 实现；[人工发布边界](RELEASING.md)不变。

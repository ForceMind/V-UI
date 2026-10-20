# V-UI：可选 Tailscale 私有管理网接入方案

> 状态：**设计研究 / 待分阶段评审**，没有实现、安装、测试或发布。  
> 日期：2026-10-20  
> 仓库：ForceMind/V-UI；目标基线为当前 `master`，不替代现有 [开发交接](DEVELOPMENT_HANDOFF.md)。  
> 范围：将 Tailscale 当作**可选的管理平面访问通道**，而不是替代 VLESS/sing-box/Xray 的转发内核或手机节点。任何实际外网/真实设备验证均另行取证。

## 1. 产品定位：互补，不融合成另一套 VPN

V-UI 的既有主线包含代理与中转双开关、每入口三态 `inherit / direct / relay` 和单一选定海外上游。现成手机标准客户端只导入自己选择的节点；国内入口到海外上游的第二跳由服务器控制。该机制已按 [每入口路由契约](PER_INBOUND_ROUTING_CONTRACT.md)完成 TCP 收口，但相关新功能并不因此属于已发布的 v0.4.8，也未获得泛化的 UDP/手机/真实外部线路资格。

Tailscale 是 WireGuard 型私有设备组网工具，提供设备身份、尾网（tailnet）IP、NAT 穿透，必要时使用 DERP 或 peer relay 传输加密数据。它与 V-UI 对应的是两个不同维度：

| 用户需求 | 由谁解决 | 本方案是否改变 |
| --- | --- | --- |
| 手机使用普通 VLESS 节点访问网络 | V-UI 当前核心/导出 | **不改** |
| 国内服务器按入口直连或中转海外 | V-UI 现有路由和上游 | **不改** |
| 管理员跨网络访问 V-UI 面板、SSH | 可选 Tailscale tailnet | 研究兼容接入 |
| 服务器作为他人出口（Exit Node） | Tailscale 的独立功能 | 默认不启用、不自动配置 |
| 多设备网段路由（Subnet Router） | Tailscale 的独立功能 | 默认不启用、不自动发布 |
| Tailscale 作为 VLESS 第二跳 | 不在本方案范围 | **禁止隐式改变已应用出口** |

**一期推荐：操作员可自行安装和连接 Tailscale，通过私有网络管理既有 V-UI 服务器；V-UI 默认不依赖 Tailscale，原有用户与代理业务完全不受影响。**

## 2. 核心架构与边界

示意（仅管理访问）：

```text
管理员电脑或手机 -- tailnet / WireGuard / 可能的 DERP --> 服务器 tailscaled
                                                      |
                                                   私有管理入口
                                                      |
                                                  V-UI 面板/API
                                                      |
                                                  保留管理员鉴权

标准 VLESS 客户端 -- V-UI 入站 -- 既有 direct/relay 策略 -- 既有目的地/选定上游
                                    （独立，不经过 tailnet）
```

- 默认模式 `disabled`：不安装 `tailscaled`，不改变监听地址、systemd、路由表、防火墙、DNS、SSH、出口、证书或核心依赖。
- 可选 `external-tailnet`：由管理员自行在主机及管理终端安装/认证 Tailscale；V-UI 只提供配置指导与可选的只读检测，绝不接管 Tailscale 账号。
- 即使 tailnet 提供私网接入，**V-UI 登录/权限/CSRF/会话保护仍然生效**；Tailnet ACL/设备批准不是面板应用层鉴权的替代。
- 不假设 `100.64.0.0/10` 中任意地址皆可信，不把来源 IP 当成唯一身份；检测地址冲突和已有 CGNAT/代理路由冲突。
- 面板的 HTTPS/WSS 及 Web 登录继续保持；不得为了省证书将管理 API 暴露成公网明文 HTTP。
- 现有节点/导出/中转/本机代理开关的持久态和已应用态必须保持不变，不能因 tailnet 上线而刷新或重编译业务路由。
- 低资源目标（1 vCPU/512 MiB）仍是独立资格；未测量 `tailscaled` 常驻占用前，不将同机共存宣称为达标。

## 3. 可分阶段交付范围

### V-TS-01｜文档与人工连通操作（P0，首期）

先提供明确的“仅远程管理”说明，不执行自动化安装：

1. 管理员在服务器和管理设备上自行安装并登录 Tailscale；可按自身策略设置 tailnet ACL/device approval，**不用向 V-UI 填写 Tailscale auth key**。
2. 检查服务器 Tailnet 在线、路由未冲突和管理终端已被允许访问目标。可使用 Tailscale 自带状态/连通性诊断，区分 direct、peer relay、DERP；这只是管理链路，不是 V-UI 用户节点的路由状态。
3. 优先使用既有受保护的管理 HTTPS 入口或受限的内部反向代理；如要绑定 tailnet IP/接口须经过 V-TS-03 独立验证，不要简单地监听所有接口。
4. 故障时走已明确保留的运维入口（如本地控制台/经过授权的 SSH）；Tailscale 不可用不能阻断服务端入站或既有回滚流程。

**验收**：独立终端经 tailnet 访问管理面板并完成标准应用鉴权；停掉 `tailscaled` 后既有已应用 VLESS 和端口/出站策略不变；仅手动操作、有明确回滚指引。

### V-TS-02｜可选只读状态卡片（P1）

需要 UI 才增加，默认隐藏/关闭，能力由管理员显式选择启用：

- 只读字段：是否检测到 `tailscaled` 可执行文件/进程、Tailnet 在线或离线、设备可用的管理地址、管理链路是 direct/relayed/unknown（必须有明确可靠数据来源，否则显示 unknown）。
- 管理地址优先展示在已授权的管理页面；普通节点/订阅/API 只读响应不得包含 Tailnet IP、机器标识、账户名或会话凭据。
- 只读检测使用严格超时、固定参数和最小权限；不运行外部脚本、不执行任意 shell 命令、不将带凭据的完整 JSON/诊断原始输出写日志；不可用时“不支持/未检测”而不是触发安装。
- 检测无关 V-UI 核心 `check/apply/reconcile`：尾网状态变化不得触发代理重启或自动切换出口。

**验收**：未安装、未登录、离线、DERP/peer relay、权限不足、状态输出异常、超时等全部可回退到“不可用/未知”，不会暴露秘密或改变业务。

### V-TS-03｜私有接口面板绑定（P2，需单独设计审查）

只有确实需要“面板只在 tailnet 中可访问”时才纳入：

- 管理员明确选择 `disabled / existing-https / tailnet-only` 等有界入口策略；默认继承现有监听/反向代理行为，不做自动收口。
- 不信任端口位于私网、请求携带特殊 Header 或源自 `100.64/10` 即可绕过身份验证；应用级身份与授权仍强制。
- 必须考虑 `tailscale0` 地址变化、DNS/MagicDNS、IPv4/IPv6、系统重启先后顺序、接口丢失、Nginx bind 行为与已有监听端口冲突。
- 若新候选绑定验证失败：保持旧的已生效配置及服务，不执行“退回公网 0.0.0.0”或跳过 TLS 的隐式回退；管理入口可能中断时提前说明带外恢复路径。
- 配置检查→候选准备→原子应用/恢复仍遵循 V-UI 已有事务纪律；不能只改面板前端开关就宣称系统监听完成隔离。
- 生产环境修改管理端口、反向代理、防火墙或系统路由属于新的敏感操作，不能由本研究文档授权。

**验收**：被允许设备有 TLS+登录的正确访问；未授权 tailnet 设备和公网入口在 `tailnet-only` 下均不能访问；停止 Tailscale/启动竞态有明确恢复方案且不暴露公网；所有旧监听与业务入站有准确回归。

### V-TS-04｜代理/VPN 共存冲突矩阵（P1，与 V-TS-03 可并行）

专门验证“主机本身代理”和已有中转开关不会被网络 overlay 改写：

- 在同一测试主机比较 Tailscale 默认尾网、启用它的 Exit Node（仅实验环境）、主机 proxy on/off、全局 relay on/off、每入口 direct/inherit/relay。
- 观测 DNS、默认路由、策略路由、fwmark、反向代理、TLS 和外部访问路径。实验结果必须区分“V-UI 业务出口”和“管理员 Tailnet 链路”；不能把后者 DERP 误报为 VLESS 中转路径。
- 固定单 selected_upstream；任何有效 relay 入口按旧要求 fail closed，不允许在上游失败时悄悄改经 Tailscale 或国内直出。
- 禁止为排查冲突而关闭主机防火墙、SSH、证书校验、核心路由保护，或替用户自动开启 Exit Node、Subnet Router、TUN、UDP 双跳。

**验收**：包含已允许直连与有效中转的真实 TCP 样本、正确来源归因、上游失败零目标送达、不变的设备导出与路由身份；遇冲突能说明互斥限制而不暗中改变配置。

## 4. 安全与资源门槛

| 项目 | 必须满足 |
| --- | --- |
| 身份与 ACL | 管理端仍经 V-UI 鉴权；可选外部 tailnet ACL 只缩小入口，不提权 |
| 最小权限 | 不读取/持久化 Tailscale auth key、设备私钥、客户端凭据；只读模块无安装能力 |
| 公网暴露 | 不自动开放新端口、不更改 SSH/防火墙；tailnet-only 错误不得回退公网监听 |
| API/日志 | 一般状态 API 不泄露地址、设备身份、原始 tailnet 状态/凭据；测试用假身份 |
| 路由隔离 | 不改变每入口三态、上游选择、DNS/UDP 资格、已有 active snapshot |
| 系统约束 | 单独测量 1 vCPU/512 MiB 下后台常驻、峰值、OOM、启动和失联恢复 |
| 回滚 | 有外部/本地控制台恢复路径，旧有效代理继续服务，服务不可达不等于业务停机 |
| 发布 | 文档、Mock、实验机、真机、准确 CI 和产品发布严格分开，不改 VERSION/Tag/Release |

## 5. 与既有工程的集成节点

- 首期新增本文档，并从 [文档首页](README.md) 引用；**不修改应用代码或依赖**。
- 后续只读候选从管理面板/诊断模块新增明确可关闭的数据适配器；要复用 V-UI 现有普通响应 allowlist，独立评审敏感字段。
- 若做尾网绑定，须审查现有 Web 入口、Nginx 配置、systemd 启动和恢复逻辑，先独立契约与测试，再做实际改造。
- 每阶段从最新 `master` 新建独立 PR，标注 exact-head、独立审查结果、准确 CI（现有八组）、相关浏览器/真实二进制/四目标软件资格，实际用到了哪些；不能继承旧 PR #49/#50 的测试绿灯。
- 严格遵守 [AGENTS](../AGENTS.md)、[开发交接](DEVELOPMENT_HANDOFF.md)、[每入口路由契约](PER_INBOUND_ROUTING_CONTRACT.md)、[低资源目标](LOW_RESOURCE_TARGET.md) 与现行正式发布门槛。未取得授权时不执行生产部署、Tailscale 认证、真实 VPS 登录或防火墙修改。

## 6. 资料

- [Tailscale Connection Types](https://tailscale.com/docs/reference/connection-types)：直接连接、DERP Relay、Peer Relay 的差异。
- [Tailscale Device Connectivity](https://tailscale.com/docs/reference/device-connectivity)：不同网络下的连通性限制。
- [Tailscale About WireGuard](https://tailscale.com/docs/concepts/wireguard)：组网与 WireGuard 的关系以及与其他 VPN 共存可能冲突。
- [Tailscale ACLs / grants](https://tailscale.com/docs/features/access-control)：尾网管理策略，不能替代 V-UI 应用鉴权。

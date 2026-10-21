# V-UI：可选 Tailscale 私有管理通道方案

> 状态：**管理网络仍仅设计与待执行验收计划；未安装、未做网络实测。**
> 2026-10-21：V-TS-02 另开[默认关闭只读状态候选](TAILSCALE_STATUS_CONTRACT.md)，不改变此文 2026-10-20 的安装/共存/恢复边界；下文“本轮”保留原文档阶段范围。
> 更新：2026-10-20。核对主线 `c66af7c55e6e9e334262f7e1f098778cb129e795`、[PR52](https://github.com/ForceMind/V-UI/pull/52) 原 head `32ec01341ca9cde8bc6b22c0f8882845eabf88af` 和 [Issue53](https://github.com/ForceMind/V-UI/issues/53)。
> 最低共存、URL 与回滚门槛见[验收计划](TAILSCALE_MANAGEMENT_ACCEPTANCE.md)。**这些门槛尚未通过，因此目前不建议在现有业务主机上安装。**

## 1. 定位与本轮边界

Tailscale 只研究为可选的私有管理通道，保留 V-UI 登录、会话、CSRF 和管理员权限检查。它不替代 VLESS 入站、现有 direct/relay 中转或手机标准节点，不解决 UDP，也不扩大现有协议资格。每入口 TCP 的已合并状态见[开发交接](DEVELOPMENT_HANDOFF.md)；正式 v0.4.8 仍是安装器修复基线，后续主线功能尚未正式发布。

| 使用目的 | 方案边界 |
| --- | --- |
| 管理员访问既有 HTTPS 面板 | 研究保持原 URL 的 tailnet 路径；首轮使用独立电脑 |
| 标准 VLESS 客户端 → 入口 → 既有直连/选定上游 | 产品路径保持；必须另行验证与 overlay 共存 |
| Tailscale Exit Node / Subnet Router / Tailscale SSH / Funnel | 本计划不启用、不发布路由、不增加此类资格 |
| tailnet-only 面板、公网收口 | 后续独立设计；仅增加管理通道不会关闭公网面板 |
| 状态展示 | 后续默认关闭的只读候选，见第 5 节；本轮不实现 |

本轮不安装/启动 Tailscale、不认证绑定账号、不读取或生成 auth key/设备密钥、不修改权限、SSH、证书、DNS、防火墙、路由、监听或 systemd，不应用/重新应用代理策略，不改代码、依赖、固定核心、VERSION、工作流，不发布或部署。`disabled`、`external-tailnet` 等名称仅是设计概念，当前没有对应产品设置。

此前 `implement-vui-restricted-udp-d2` 已被平台中止。此文档工作独立进行；不恢复、复制、重新审查或执行该工作，也不以共存验收或 CI 为由绕过暂停范围。

## 2. 安装建议之前的共存与回滚门槛

Tailscale 安装不是“系统网络完全不受影响”的操作。Linux 默认 netfilter 模式会维护并激活自己的防火墙规则；此外须核对它维护的策略路由、DNS、接口、地址及与其他 VPN 的冲突。详见官方 [netfilter 模式](https://tailscale.com/docs/reference/netfilter-modes)和 [VPN 共存限制](https://tailscale.com/docs/reference/faq/other-vpns)。本方案不建议通过关闭 netfilter 或安全校验来避免验证。

必须先在另行授权的独立非生产环境完成[最低矩阵 C0–C8 与回滚 R0–R4](TAILSCALE_MANAGEMENT_ACCEPTANCE.md)，记录准确产品提交、Tailscale 版本、系统/防火墙后端、前后差异和失败，再决定是否能给出安装建议。顺序是：

1. 在安装前验证不依赖 tailnet、待覆盖域名或同一个 VPN 的带外控制台/既有授权 SSH；准备可验证的旧配置、备份和恢复责任人。
2. 保存防火墙、IPv4/IPv6 路由与策略规则、DNS、监听、业务入口、desired/applied/active 策略及自身地址快照的私有基线。
3. 在隔离环境观察安装/连接后的差异，验证 HTTPS 管理与原 VLESS TCP 流量、受保护中转和失效拒绝行为。
4. 覆盖接口 up/down、断网重连、面板/核心/整机重启、启动次序和策略恢复，并演练回到安装前及安装后两种已知基线。
5. 任一安全不变量不成立、恢复依赖自动重新应用、业务来源不明或带外路径不可用即停止推广；保留失败。安全拒绝可以证明保护生效，不能算安装共存通过。

### 自身地址快照：源码推导，尚未实测

以下调用点在上述主线和 PR52 原 head 的产品源码相同。本次仅静态阅读，没有调用这些函数或执行网络测试：

| 源码入口 | 已读到的行为 | 对本方案的推导 |
| --- | --- | --- |
| [proxy_resolution.py](../app/services/proxy_resolution.py) `self_address_snapshot()` | 枚举所有本机接口 IPv4/IPv6，加上配置的面板域名解析结果；失败不返回部分快照 | `tailscale0` 地址新增/消失，以及服务端面板域名答案变化，可能改变自身地址集合 |
| [proxy_policy.py](../app/services/proxy_policy.py) `AddressPolicy` / `compile_proxy_policy()` | 自身地址进入拒绝集合及策略身份摘要 | 接口变化关系到自连保护与已应用策略身份，不能只验证“面板可打开” |
| [proxy_apply.py](../app/services/proxy_apply.py) `_rollback_guard()` / `_recovery_guard()` / `recover()` | 需要上游的活动策略在回滚时核对自身地址集合；恢复重新编译并核对核心修订与策略身份；失败停止受影响运行时并保留证据 | 在已有 host proxy 或有效 relay 策略时，地址变化可能使重启、恢复或失败回滚进入 `RECOVERY_REQUIRED`；停止 Tailscale 不保证恢复业务 |

不是每次 up/down 都必然报错：必须分别记录接口是否仍存在、地址是否实际变化、活动策略是否需要上游和操作触发点。不能将静态保护机制推导描述为已经复现的故障或“运行中必然立即停机”。**不得排除 `tailscale0`、减少自身地址拒绝项、削弱自连保护、改写 active 元数据或自动 apply/reconcile 来掩盖风险。** 若保持现有保护无法完成恢复，安装建议继续阻塞，转为独立设计问题。

## 3. 保留原 HTTPS URL 的首选研究路径

### 标准安装的实际约束

- [scripts/install_system.py](../scripts/install_system.py) `install_verified()` 将面板 origin 设为安装域名与端口；证书仍须覆盖该域名，见[证书说明](CERTIFICATES.md)。
- [app/serve.py](../app/serve.py) 是标准受管部署入口：Uvicorn 直接终止 HTTPS，要求 origin 的有效端口等于监听端口，且 `proxy_headers=False`。不能把 Nginx/外部 TLS 终止视为标准已有路径。
- [app/middleware/auth.py](../app/middleware/auth.py) 将 Host 与规范化 origin 的 authority 比较；登录及写请求还要求精确 Origin 和 `X-VUI-Request: 1`。新增网络路径不能绕过这些检查或应用登录。

因此直接打开 Tailnet IP 或 MagicDNS 名称，可能遇到证书名称不匹配或 Host 拒绝；仅打开原域名而仍连接公网 IP，也没有证明流量经过 tailnet。

### 候选 A：只在独立管理端定向解析，URL/端口/证书均保留

假设既有地址为 `https://panel.example.com:8443`，候选仍使用此完整地址，SNI 为 `panel.example.com`、Host 为 `panel.example.com:8443`、Origin 为 `https://panel.example.com:8443`；仅在专用管理端的受控解析上下文中，让该域名连接服务器 Tailnet 地址。服务器 origin、证书、监听端口与业务配置均不变。此路径**待验收，尚不是已支持的部署步骤**。

```text
独立管理电脑：原 HTTPS 域名:原端口 → 经核对的 Tailnet 地址
             → tailnet（direct / DERP / Peer Relay）→ 既有 Uvicorn HTTPS
业务客户端：原节点域名/地址 → 原入口 → 原 direct/relay 策略
```

进入实验前及验收时必须同时满足：

1. 既有监听确实能接收目标 Tailnet 地址及相同端口，现有主机规则与已审阅的最小 tailnet 授权允许该电脑访问。若只绑定 loopback/公网单地址而不可达，候选 A 不适用；不能为方便而扩大到全接口监听。
2. 定向解析只影响管理端；不改公共 DNS、不在服务器改 hosts/面板解析，不将 split DNS 推送至整个 tailnet，不改变 VLESS 客户端解析。先列出原域名是否同时用于 VLESS、订阅或其他业务；同机 hosts 是按名称影响所有应用，并不按端口隔离。若共用名称，使用独立管理电脑/已验证隔离解析上下文；不能假定“只给面板端口改解析”，无法隔离则阻塞此候选。
3. 分别核对 A 和 AAAA、浏览器 DNS/连接缓存、DoH、安全 DNS、系统解析和代理远程 DNS。未覆盖的 AAAA、浏览器代理或 DoH 可能仍连接公网；不得仅凭 hosts 文件或一次命令行请求成功宣称浏览器路径成立。
4. TLS 校验须通过原受信证书及 SNI；浏览器实际连接地址与操作系统路由/服务端到达证据共同证明 tailnet 路径，应用 Host/Origin 不变。Tailscale ping/status 成功只证明 overlay，不能替代浏览器登录、API 与路径证据。
5. 退出时撤销且仅撤销本次管理端解析覆盖，处理相应缓存，复核恢复后的原解析和业务路径。已失联的管理端可用带外路径处理服务端故障，不能依赖原域名自动回公网来充当恢复证明。

### 其他入口的边界

| 入口 | 当前判定与后续工作 |
| --- | --- |
| Tailnet IP / MagicDNS / 新 tailnet HTTPS 域名 | 不是即插即用；须单独设计证书、origin、端口、Cookie/登录与客户端兼容迁移 |
| Tailscale Serve / HTTPS 反向代理 | Serve 默认可终止 TLS，但不会自动满足现有直出 HTTPS 端口和 Host/Origin 约束；须明确前后端 TLS、SNI、Host/Origin、鉴权与回滚。不能用 `https+insecure`、跳过证书验证或伪造转发头作为方案 |
| 原始 TCP 转发/TLS 保留代理 | 独立研究候选；须验证原域名/证书/端口、实际路径、监听与冲突，不是本轮已支持路径 |
| tailnet-only | V-TS-03 独立阶段；现有公网监听不会因接入 tailnet 自动消失，不可称公网已隔离 |

最小授权只是一份待评审的计划：明确哪些管理设备/身份能访问哪台服务器的哪一个面板 TCP 端口；SSH 若需要须另列授权。其他 tailnet 成员、地址段或特殊 Header 不自动受信任。本轮不改 grants/ACL、设备批准或系统权限。

## 4. 分阶段交付与剩余资格

| 阶段 | 交付和退出门槛 | 当前状态 |
| --- | --- | --- |
| V-TS-01 | 本方案、最低共存及恢复矩阵、候选 A 的 TLS/登录/路径验证；通过隔离环境验收后才考虑人工安装建议 | 仅文档修订，矩阵未执行 |
| V-TS-02 | 默认关闭的只读状态适配器；独立设计/实现/审查与异常输出测试 | 未实现 |
| V-TS-03 | 明确 TLS、监听、fail closed、公网不可达和带外恢复的 tailnet-only 方案 | 未设计完成，不随一期安装收口 |
| V-TS-04 | 更完整系统/VPN 共存和准确目标资格；最低共存部分已前移为 V-TS-01 前置门槛 | 未执行，不启 Exit Node/Subnet Router，不恢复 UDP 专项 |

首轮建议独立电脑管理。手机 iOS/Android 通常仅允许同时启用一个 VPN，不能保证与 VLESS/TUN 客户端并开；需要独立真机资格，见官方 [VPN 限制](https://tailscale.com/docs/reference/faq/other-vpns)。管理链路可能从 P2P 退回 DERP/Peer Relay，不能保证直连、时延或吞吐；这些连接类型不代表 VLESS 的出口选择，见[连接类型](https://tailscale.com/docs/reference/connection-types)。

1 vCPU/512 MiB 整机与 24h 稳定性仍未完成，增加 `tailscaled` 后没有内存/CPU 预算承诺。后续须单独测整机后台、峰值、双核心、失联重连、OOM、磁盘/网络及长期运行；已有[低资源软件证据](LOW_RESOURCE_TARGET.md)不能转授同机共存资格。

## 5. 后续只读状态卡片契约

仅在独立阶段明确需要时实现，默认关闭/隐藏。探测使用固定可执行文件与固定参数列表，不经 shell、不允许请求方指定命令/目标；有明确的执行超时、输出 bytes/记录数上限、并发上限、缓存 TTL 和刷新限频，具体数值须在该阶段评审确定后才能实现。不因失败安装、登录、重启核心或修改代理/网络。

字段采用白名单：可用性、在线/离线/未知、数据时间与过期标记，以及有可靠数据源的目标 peer 链路类型。设备/进程可见性不证明已登录；未安装、权限不足、超时、截断、异常 JSON、未知版本或缓存过期均显示未知/不可用。未经特权读取就无法判断时，不能申请 sudo、增加 socket/组权限或授予修改能力。

管理地址仅在已授权管理页面、且通过第 3 节 URL 契约后展示；普通节点、订阅、普通状态 API 和日志不得泄露 Tailnet IP、机器/账户/peer 标识、原始状态 JSON、认证 URL 或密钥。读状态不是 V-UI 核心 `check/apply/reconcile`；任何 tailnet 状态变化都不能触发重新应用或切换出口。

## 6. 接手与证据

本轮覆盖 [P2：安装前共存门槛](https://github.com/ForceMind/V-UI/pull/52#discussion_r4321970259)与 [P2：URL/origin 兼容路径](https://github.com/ForceMind/V-UI/pull/52#discussion_r4321970261)的文档缺口；不将文档修订写成实测通过，也不代替评审者关闭线程。

接手先重读 [AGENTS](../AGENTS.md)、最新主线、PR52 和 Issue53，再依[验收计划及证据模板](TAILSCALE_MANAGEMENT_ACCEPTANCE.md)推进。每阶段正常提交、独立审查，记录准确 head/tree、实际命令、CI run/attempt、失败和未运行项；不继承旧 PR 的绿灯。此次仅允许正常文档提交触发现有流程，不手动启动/修改暂停的 UDP 专项，不扩大为 CI 改造、合并、发布或部署。

旧 [Portable run 39378368967](https://github.com/ForceMind/V-UI/actions/runs/39378368967) 的 `distro-probe` **failure 保留**：openSUSE 软件源元数据超时后 `python3` 缺失，最终 exit 127；该发行版实际 probe 未跑完。这不是已确认的产品逻辑失败，也不是全绿；本轮不修工作流、不手动重跑，后续通过也不能抹去此次失败。详细归因见[证据账本](TAILSCALE_MANAGEMENT_ACCEPTANCE.md#5-证据账本与交接模板)。

官方资料（2026-10-20 只读核对，不能代替实测）：[netfilter](https://tailscale.com/docs/reference/netfilter-modes)、[VPN 共存](https://tailscale.com/docs/reference/faq/other-vpns)、[连接类型](https://tailscale.com/docs/reference/connection-types)、[设备连通性](https://tailscale.com/docs/reference/device-connectivity)、[Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)。

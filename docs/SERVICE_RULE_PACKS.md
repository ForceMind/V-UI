# 服务规则包（Service Rule Pack）与敏感服务分流设计

> 状态：设计基线  
> 适用范围：V-UI v0.3.x 及后续  
> 最后核验：2026-10-03  
> 目的：避免“只维护少量静态域名”导致新增域名在默认直连模式下绕过代理，并为 Claude、ChatGPT、Google 等服务建立可维护、可测试、可回滚的完整分流模型。

## 1. 背景与问题

V-UI 当前 ToClash 工作区有两种主要路由语义：

- **常规模式**：本机/内网优先直连，自定义与服务规则之后，中国大陆直连，其余交给 PROXY。
- **默认直连模式**：只有明确指定的服务/域名交给 FORCE_PROXY，其余最终落入 DIRECT。

默认直连模式最节省代理流量，但也最容易暴露“漏规则”问题。

假设 Claude 预设只维护 anthropic.com、claude.ai、claude.com、claudeusercontent.com，而服务后来使用独立短域名 clau.de。若客户端最终规则类似：

~~~yaml
- DOMAIN-SUFFIX,anthropic.com,FORCE_PROXY
- DOMAIN-SUFFIX,claude.ai,FORCE_PROXY
- DOMAIN-SUFFIX,claude.com,FORCE_PROXY
- MATCH,DIRECT
~~~

访问 https://clau.de/... 时，第一跳可能直接走本地出口。即使随后 30x 跳转到 claude.com 并命中代理，第一跳已经发生。

这类问题不是 Claude 独有。任何服务只要增加新短域名、独立登录域名、新 API 域名、MCP/上传/下载域名、CDN 域名或新的 IPv4/IPv6 前缀，都可能绕过只靠手工静态域名维护的旧方案。

因此，V-UI 不应继续把“服务预设”等同于“几条域名字符串”，而应升级为 **Service Rule Pack（服务规则包）**。

## 2. 本文不做的推断

本文只处理网络分流与出口暴露风险，不对服务商动机做无证据推断。

- clau.de 如果未被代理，目标站点可以看到该连接的公网出口地址，这是普通互联网连接的客观结果。
- 目前不把“某个问卷专门用于识别中国用户”作为 V-UI 的技术事实。
- “访问过一次漏规则域名”不等于账号一定被限制、封禁或完成某种归因。

V-UI 的职责是让用户明确控制流量出口，并尽量避免配置层面的意外直连。

## 3. Service Rule Pack 数据模型

每个服务预设应从简单域名数组升级为结构化规则包：

~~~text
ServiceRulePack
├── id
├── display_name
├── risk_class
├── enabled_by_default
├── critical_domains
├── upstream_domain_sources
├── exact_domains
├── domain_suffixes
├── ip_cidrs_v4
├── ip_cidrs_v6
├── ports / protocols
├── resolver_policy
├── update_policy
├── last_known_good
├── source_metadata
└── tests
~~~

规则应拆成三层。

### 3.1 Embedded Critical Baseline

项目仓库内置的关键兜底规则：

- 随 V-UI 版本发布；
- 不依赖运行时访问 GitHub；
- 经过代码审查和测试；
- 上游源暂时不可用时仍能工作；
- 对敏感服务至少覆盖最关键的入口、登录、API、上传下载与短域名。

### 3.2 Upstream Maintained Rules

从可信社区或官方来源同步、审核后进入的扩展规则，用于自动跟进服务新增域名。

远程源不能成为单点依赖。更新失败时必须继续使用 last-known-good（LKG）+ embedded baseline，不能把该服务悄悄退化为 DIRECT。

### 3.3 Network Fallback

可选的 IPv4/IPv6 CIDR 兜底，用来增强服务自有地址空间的覆盖，但不能作为唯一真相。

原因：

- CDN、云厂商、第三方登录和共享基础设施不一定属于服务商自己的 ASN；
- 前缀可能迁移、拆分、借用或由其他 ASN 宣告；
- 大范围 ASN 路由可能误伤其他业务；
- IPv4 与 IPv6 必须同时考虑。

推荐模型是：

~~~text
关键域名基线
    +
审核后的上游域名集
    +
必要的精确 IP/CIDR 兜底
~~~

而不是“把整个 ASN 都代理 = 完整服务规则”。

## 4. Claude / Anthropic 当前基线

截至 2026-10-03，V2Fly domain-list-community 的 data/anthropic 包含：

~~~text
anthropic.com
clau.de
claude.ai
claude.com
claude.dev
claudemcpclient.com
claudemcpcontent.com
claudeusercontent.com
full:servd-anthropic-website.b-cdn.net
~~~

来源：

- https://github.com/v2fly/domain-list-community/blob/master/data/anthropic
- https://github.com/v2fly/domain-list-community

V-UI 的 Claude Rule Pack 至少应把以下内容放进 embedded critical baseline：

~~~text
anthropic.com
clau.de
claude.ai
claude.com
claude.dev
claudemcpclient.com
claudemcpcontent.com
claudeusercontent.com
~~~

上游 exact/full 域名应进入审核后的扩展集。

### 4.1 为什么 clau.de 必须单独覆盖

clau.de 是独立域名，不属于 *.claude.com。

因此：

~~~text
DOMAIN-SUFFIX,claude.com
~~~

不会匹配：

~~~text
clau.de
~~~

在默认直连模式中，如果没有单独规则，clau.de 会继续落到最终 MATCH,DIRECT。这正是 Service Rule Pack 要消除的维护缺口。

## 5. Anthropic IP / ASN 只能作为辅助

截至 2026-10-03 的公开 BGP 观测中：

- 160.79.104.0/23 由 Anthropic, PBC 的 AS399358 宣告；
- 该 IPv4 前缀包含 512 个地址；
- Anthropic 同时存在 IPv6 前缀；
- 更大的地址分配块与全球路由表中实际宣告的具体前缀不是同一个概念。

参考：

- https://bgp.he.net/net/160.79.104.0/23
- https://bgp.he.net/AS399358

可以把经过审核的精确前缀作为 Claude Rule Pack 的辅助规则，但不要把“属于某 ASN”当成唯一判断条件。

## 6. 规则优先级

Service Rule Pack 必须在最终 catch-all 规则之前执行。

推荐顺序：

~~~text
1. 本机 / loopback
2. 明确的私网与管理面
3. 用户自定义 REJECT
4. 用户自定义 DIRECT
5. 用户自定义 FORCE_PROXY
6. 启用的 Service Rule Packs
   6.1 critical exact domain
   6.2 critical domain suffix
   6.3 reviewed upstream domains
   6.4 reviewed IP/CIDR fallback
7. 中国大陆直连（常规模式）
8. PROXY（常规模式）
   或 MATCH,DIRECT（默认直连模式）
~~~

对默认直连模式，第 6 步就是关键安全边界。

自动化测试必须证明所有启用服务的规则都位于最终 MATCH,DIRECT 之前，不能因序列化、合并、自定义规则插入或 UI 排序而改变。

## 7. Mihomo 导出语义

Mihomo 完整 YAML 应由服务端直接生成最终规则，不要求用户额外手工修改客户端。

Claude 最低兜底示例：

~~~yaml
rules:
  - DOMAIN-SUFFIX,anthropic.com,FORCE_PROXY
  - DOMAIN,clau.de,FORCE_PROXY
  - DOMAIN-SUFFIX,claude.ai,FORCE_PROXY
  - DOMAIN-SUFFIX,claude.com,FORCE_PROXY
  - DOMAIN-SUFFIX,claude.dev,FORCE_PROXY
  - DOMAIN-SUFFIX,claudemcpclient.com,FORCE_PROXY
  - DOMAIN-SUFFIX,claudemcpcontent.com,FORCE_PROXY
  - DOMAIN-SUFFIX,claudeusercontent.com,FORCE_PROXY

  # 可选、审核过的网络兜底
  - IP-CIDR,160.79.104.0/23,FORCE_PROXY,no-resolve

  # 默认直连模式最后才允许出现
  - MATCH,DIRECT
~~~

注意：

- 160.79.104.0/23 不是 Claude 完整地址空间的声明，只是当前可核验的网络兜底候选之一。
- 加入网络兜底时 IPv6 必须单独评估并维护。
- 如果客户端不支持某条规则类型，导出器必须明确报错或降级到已证明等价的表达，不能静默删除关键规则。

## 8. sing-box 规则集

sing-box 自 1.8.0 支持 rule-set。远程规则集可以采用：

~~~json
{
  "type": "remote",
  "tag": "geosite-anthropic",
  "format": "binary",
  "url": "https://example.invalid/geosite-anthropic.srs",
  "update_interval": "1d"
}
~~~

但 V-UI 不应长期复制旧式 download_detour 配置。

sing-box 官方文档当前说明：

- download_detour 在 1.14.0 已弃用；
- 计划在 1.16.0 移除；
- 新配置应使用 http_client；
- 远程 rule-set 若需持久化缓存，需要相应缓存配置。

官方文档：

- https://sing-box.sagernet.org/configuration/rule-set/

因此 V-UI 生成器必须按项目打包的 sing-box 固定版本生成匹配语法，不能把网上旧配置当作永久模板。

### 8.1 远程规则故障策略

禁止：

~~~text
远程规则集下载失败
        ↓
该服务没有规则
        ↓
MATCH,DIRECT
~~~

推荐：

~~~text
远程更新成功
  -> 验证
  -> 写入新快照
  -> 标记 LKG

远程更新失败
  -> 保留 LKG
  -> 合并 embedded critical baseline
  -> 面板显示 degraded / stale
  -> 不把失败解释为“规则为空”
~~~

首次安装没有 LKG 时，embedded baseline + 随发布包附带的审核快照必须足以生成基础规则。

## 9. 上游同步与异常保护

每次同步至少记录：

~~~text
source
source revision / commit
retrieved_at
content hash
parsed rule count
validation result
previous LKG hash
effective merged hash
~~~

推荐流程：

~~~text
fetch
  ↓
size / format validation
  ↓
parse
  ↓
normalize
  ↓
deduplicate
  ↓
safety validation
  ↓
diff against LKG
  ↓
accept / quarantine
  ↓
publish new LKG
~~~

以下异常不能自动覆盖当前有效规则：

- 规则数量突然变为 0；
- 上游解析失败；
- 普通服务突然出现极大的 IP 范围；
- 大量规则被删除；
- 域名格式非法；
- 出现私网、loopback、默认路由等危险 CIDR；
- 上游内容变成 HTML 或错误页；
- hash 改变但解析结果明显异常。

异常更新进入 quarantine，面板显示“上游更新异常，继续使用上一个有效版本”。

## 10. DNS 与域名匹配

“rules 中存在域名”不等于所有应用流量都会被正确捕获。

兼容测试应覆盖：

- 普通系统代理；
- TUN；
- fake-ip / redir-host；
- IPv4 only；
- dual-stack；
- QUIC / HTTP3；
- 客户端自身 DNS；
- 浏览器 DoH；
- 应用自带解析器。

V-UI 的目标是：

1. 对明确支持的客户端/模式写清兼容边界；
2. 不把“规则存在”误写成“所有程序一定经过代理”；
3. 对完整 Mihomo 配置同时保证 DNS 与 rules 语义一致；
4. 对未经过 Mihomo/sing-box 的流量不做虚假保证。

## 11. 不允许静默直连

对启用了敏感服务强制代理的 Rule Pack，应遵守：

> **已知服务规则失效时，宁可保持旧版本并告警，也不能静默把已知目标改成 DIRECT。**

具体包括：

- 上游下载失败：继续使用 LKG；
- 新规则解析失败：继续使用 LKG；
- 生成器不支持某规则类型：拒绝发布该新配置；
- FORCE_PROXY 没有可用代理节点：不能自动替换成 DIRECT；
- 订阅生成失败：保留上一个已保存版本；
- 客户端格式能力不足：显式提示，不静默删除关键规则。

这与 V-UI 现有“失败不能伪装成功”“不在失败/无节点时悄悄改成 DIRECT”的约束一致。

## 12. UI 建议

普通用户不需要理解 Geosite、ASN、SRS。

建议服务卡片显示：

~~~text
Claude / Anthropic          [启用]

出口：
● FORCE_PROXY
○ 自定义策略组

覆盖：
✓ 关键域名
✓ 社区规则
✓ IP/CIDR 兜底

规则状态：
Up to date
Last checked: 2026-10-03
Source revision: abcdef0

[查看规则详情]
~~~

详情页再展示 embedded baseline、upstream source、规则数量、最近成功更新时间、LKG、diff、CIDR 与 stale/error 状态。

用户本地 override 可以支持追加域名、排除域名、追加 CIDR，但不能无提示删除项目内置 critical baseline。高级覆盖必须明确展示风险并进入预览/导出测试。

## 13. Claude Rule Pack 验收用例

### 13.1 默认直连

输入：

~~~text
mode = default-direct
claude.enabled = true
~~~

必须满足：

| 目标 | 预期 |
| --- | --- |
| https://clau.de/ | FORCE_PROXY |
| https://claude.ai/ | FORCE_PROXY |
| https://claude.com/ | FORCE_PROXY |
| https://api.anthropic.com/ | FORCE_PROXY |
| https://foo.claudeusercontent.com/ | FORCE_PROXY |
| 非服务普通域名 | DIRECT |

核心断言：

~~~text
clau.de MUST NOT fall through to MATCH,DIRECT
~~~

### 13.2 上游不可用

模拟 upstream fetch timeout：

- clau.de 仍命中 embedded baseline；
- LKG 仍保留；
- 订阅不退化；
- UI 标记更新失败；
- 不覆盖旧快照。

### 13.3 上游返回空文件

必须 validation failed；不接受 0-rule snapshot；不覆盖 LKG；不生成“空 Claude 规则包”。

### 13.4 上游新增域名

新增域名应进入 diff，经策略校验后进入 effective set；新订阅包含该规则，embedded baseline 不受影响。

### 13.5 规则顺序

DOMAIN,clau.de,FORCE_PROXY 必须出现在 MATCH,DIRECT 之前。

### 13.6 IPv6

如果配置 IPv6 fallback：

- 导出必须保留；
- dual-stack 不能只有 IPv4 兜底；
- 不支持 IPv6 规则的输出格式必须显式报告能力差异。

## 14. 版本与发布策略

Service Rule Pack 本身应有版本信息：

~~~text
pack_schema_version
pack_revision
upstream_revision
generated_at
~~~

正式 Release 中：

- 固定 embedded baseline；
- 可附带审核后的 upstream snapshot；
- 记录来源与许可证；
- 不依赖安装时在线解析 latest 才能生成基础配置；
- 远程更新是增强，不是启动前提。

## 15. 与现有 40 项服务目录的迁移

不要一次重写整个 ToClash。按小版本逐步迁移。

### Phase A：数据模型

- 引入 ServiceRulePack schema；
- 旧静态域名数组自动转换为基础 pack；
- 输出保持与现有规则一致；
- 不改变 UI 行为。

### Phase B：Claude 试点

- Claude / Anthropic 首先迁移；
- 加入 clau.de critical baseline；
- 增加上游规则快照；
- 增加 LKG；
- 加入完整验收测试。

### Phase C：更新器

- 上游刷新；
- hash/revision；
- validation；
- quarantine；
- rollback；
- UI 状态。

### Phase D：逐项迁移高价值服务

优先迁移 ChatGPT/OpenAI、Google/Gemini、GitHub/Copilot、X、Facebook/Meta、Discord、Telegram 及当前 40 项中的其他高频服务。

每个 pack 都必须有 baseline、upstream 来源、fixture、default-direct 测试、normal-mode 测试和 Mihomo 输出测试。

### Phase E：网络兜底

在有充分证据时再逐项增加 IPv4 CIDR、IPv6 CIDR 与 ASN/BGP observation metadata。不要把“发现一个 ASN”自动转换成生产路由。

## 16. 建议代码边界

~~~text
service catalog
    ↓
RulePackLoader
    ├── EmbeddedSource
    ├── SnapshotSource
    └── RemoteSource
    ↓
RulePackNormalizer
    ↓
RulePackValidator
    ↓
RulePackMerger
    ↓
Routing IR
    ├── MihomoRenderer
    └── SingBoxRenderer
~~~

关键原则：

- 上游格式不直接进入 renderer；
- renderer 不负责联网；
- UI 不直接拼接规则；
- 远程失败不修改 effective LKG；
- 最终输出都从统一 Routing IR 生成。

## 17. 安全边界

Service Rule Pack 解决的是：

- 已知服务域名漏配；
- 上游规则可维护性；
- 新域名进入最终 DIRECT 的风险；
- 远程更新失败后的静默退化；
- 不同客户端格式的规则一致性。

它不保证：

- 服务商无法通过账号、支付、设备、手机号或其他应用层信息判断地区；
- 用户所有程序一定经过代理；
- 浏览器扩展、独立 VPN、系统私有 DNS 不会绕开当前客户端；
- 第三方 CDN 永远稳定；
- 一个服务的所有网络依赖都能通过 ASN 推导。

因此 UI 与文档应使用“分流覆盖/出口控制”，不使用“绝对匿名”“绝不会识别地区”等承诺。

## 18. 当前结论

V-UI 对敏感服务的推荐方案：

~~~text
Embedded Critical Baseline
          +
Reviewed Upstream Rules
          +
Optional Precise Network Fallback
          +
Last-Known-Good / Validation / Rollback
          ↓
Unified Routing IR
          ↓
Mihomo / sing-box
~~~

Claude / Anthropic 必须把 clau.de 纳入关键基线。

对于默认直连模式，任何已启用服务都必须经过自动化测试证明：

> **所有当前已知关键入口先命中 FORCE_PROXY，再允许最终 MATCH,DIRECT。**

本文是后续 Service Rule Pack 实现与验收的设计基线。动态上游更新、LKG、quarantine 与 CIDR 自动维护在代码真正完成前，不能写成“已经上线”。

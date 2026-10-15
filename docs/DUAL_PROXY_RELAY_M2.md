# 双开关 M2：来源路由与提交式运行事务

日期：2026-10-15。承接 [M0 合同](DUAL_PROXY_RELAY_CONTRACT.md)、[M1 草稿实现](DUAL_PROXY_RELAY_M1.md)。本文件描述 M2 候选实现及必须执行的验收，不提前授予真实双跳、浏览器或用户海外线路资格。

## 实现入口

- `proxy_policy.py`：纯配置编译；四种独立开关组合、统一上游、按入口 tag 的 TCP/DNS/地址检查、UDP 拒绝和末尾 reject。普通双关配置保持原样；仅本机模式保留普通节点的直出行为。接入中转开启前检查全部核心的启用入口，不合格入口阻断整个开启操作。
- `proxy_resolution.py`：系统 bootstrap 独立子进程，3 秒启动在内的期限、128 MiB 地址空间、最多两个 worker、32 地址/4 KiB 输出上限。所有结果检查后固定一个 IP，保留原 SNI；仅显式检查/应用重新 bootstrap。面板及真实网卡自身地址完整收集，失败不省略防护。
- `proxy_apply.py`：共享管理锁、固定核心语法检查、实际端口所有者检查、持久 journal、启动验证、最后提交活动状态。应用重复请求使用相同幂等键；普通节点或证书应用只读取活动代理快照，不发布尚未应用的新草稿。
- `proxy_store.py`：向后兼容 M1 状态，增加私有运行上下文和精确活动事务身份；公开 pending 只回传固定五字段，公开活动信息额外仅包含固定 IP/解析时间。秘密、原始配置、自身地址目录和运行上下文不出现在普通响应。
- `core_manager.py` / `inbound_service.py`：全核心入口快照、修改前资格校验、明确 DB 已保存但运行未应用的区别、自动恢复顺序。旧 Xray 运行状态即使已不在当前 DB，也会阻止中转开启；活动中转启动时不能复活旧的直接出口 Xray。
- `core_runtime.py`：实际监听地址、协议和所有者就绪检查；精确旧配置恢复原语。恢复失败停止受影响进程并保留证据，不能启动未提交候选作为回退。
- `api/proxy.py`：`/check` 与 `/apply` 分开；检查不代表 TLS、REALITY、代理认证、HTTPS 或 DNS 诊断成功。应用只有活动提交成功才返回生效。鉴权、同源、CSRF、实际请求体限制及无秘密错误沿用 M1。

## 状态和失败语义

两个开关默认关闭，四组合独立成立。本机只创建 `127.0.0.1`、独立鉴权的显式 HTTP CONNECT / SOCKS 代理；无 wildcard、系统代理设置、TUN、路由表或系统 DNS 修改。接入节点凭据和端口不因选海外出口而轮换。

本机端口在切换前按 TCP/UDP × IPv4/IPv6 检查实际占用和所有者；准确旧核心拥有的 IPv4 loopback TCP 监听可以原位更换，其他占用拒绝。预检与启动之间仍有不可完全消除的竞态，所以启动后再次校验核心实际监听，不把预检成功当最终生效。

节点/证书更新后的运行失败可恢复原已提交安全配置；不把新 DB 记录描述为已应用。若自身地址集合变化，旧策略可能遗漏新的自身目标，此时不能恢复运行。启动时当前完整入口/自身快照与保存的活动策略不一致，保留停止及可恢复故障，不从新草稿猜测运行；管理员需检查差异再明确应用。未知、损坏或未确认写入状态同样不能默认双关。

正常恢复仍拒绝活动 ON→旧 OFF/legacy 的隐式降级。M1 文档中专用离线恢复的缺口仍须在完整交付前处理，不能把正常 journal 恢复等同于任意损坏状态的修复。

## 存储、容量与严格请求边界

运行目录必须为当前服务所有的 0700 目录；配置和状态为同所有者、单链接的 0600 普通文件。逐层禁止跟随 symlink，校验并固定目录/文件描述符，核心从受持有的描述符读取准确配置。Xray 对无扩展名 `/proc/<pid>/fd/<fd>` 显式指定 JSON；两种固定核心都需正/负 parser 检查。已有空 `owner.lock` 可保留旧版 0644/0640 的只读位，但必须仍由服务所有、单链接、空内容且无组/其他写权限，所在目录仍是 0700；新锁为 0600，不在线修权限。

代理状态/单个写入意图的有限上限为 2,000,000 bytes。历史保留且不自动删除，达到容量后后续修改可能被拒绝；这不是存储损坏，不会停止已生效的安全运行。准备应用前同时为 PREPARED→COMMITTED 和 PREPARED→ABORTED 的准确序列化体积保留空间，草稿/秘密写入前同样预检；超限返回固定 HTTP 409 `PROXY_CAPACITY_EXHAUSTED`，保持状态/历史/运行不变。历史归档/维护工具尚未在此阶段提供，不能声称可无限次变更。

`If-Match` 与 `Idempotency-Key` 各只允许一个物理请求头，即使重复值相同也拒绝；逗号合并、弱版本、通配符和非法格式均不能绕过版本/幂等控制。普通错误只返回固定代码，不回显路径或秘密。

真实核心候选启动失败时关闭底层自动旧配置启动，先由事务协调器校验旧配置和当前自身地址，再决定恢复；不得先恢复然后才发现旧策略不安全。负向网络证据使用不可重置的阶段基线，失败客户端/入口停止后仍检查延迟送达与最后日志，不能被下一次请求的新基线吸收。

## 固定内核边界更正

[合同 §5.4](DUAL_PROXY_RELAY_CONTRACT.md#54-m2-固定核心行为修订2026-10-15)显式记录两项源码确认边界：运行层 IPv4-mapped 可能在进入规则前已变为真实 IPv4；双族 DNS 一族成功、另一族失败时可以继续使用成功族。

这些修正不改变禁止集合、私网/自身/metadata 防护、检查同一结果后拨号、无普通 DNS / DIRECT fallback 或核心 pin。必须以实际 wire、多答复、重绑定和 DNS 部分/整体失败测试补充源码证据；不靠删除安全负例改变结果。

## 验收入口

```sh
PYTHONPATH=.:tests python -m unittest \
  test_proxy_policy test_proxy_resolution test_proxy_store test_proxy_apply \
  test_proxy_integration test_proxy_api test_proxy_runtime_api \
  test_proxy_backup test_proxy_policy_installed_smoke -v

VUI_TEST_CORES=/path/to/pinned/cores \
  python -m unittest discover -s tests -p test_proxy_policy_real.py -v

VUI_PROXY_LOOPBACK=1 VUI_TEST_CORES=/path/to/pinned/cores \
VUI_TEST_MIHOMO=/path/to/pinned/mihomo \
  python -m unittest discover -s tests -p test_proxy_double_hop.py -v

VUI_PROXY_LOOPBACK=1 VUI_TEST_CORES=/path/to/pinned/cores \
VUI_TEST_MIHOMO=/path/to/pinned/mihomo \
  python -m unittest discover -s tests -p test_proxy_apply_real.py -v
```

真实测试覆盖独立 TLS/REALITY 上游、国内 TLS 入口、两种客户端、显式本机 CONNECT/SOCKS5h、四状态/12 有向切换、实际事务和故障回滚、固定 DNS 路径及地址/UDP拒绝。HTTP 应用目标与 REALITY 参考 TLS 握手目标分别计数；真实错误原因、业务目标零送达和无 direct 不以超时替代。测试的 loopback policy 仅内部构造，没有 API/配置/环境变量放开生产安全规则。

`scripts/proxy_policy_smoke.py` 接入四目标实际包消费门槛：包内解释器运行有界、仅数字地址的 resolver worker（不发 DNS），以及 TLS/REALITY × 四组合的固定 sing-box 配置检查。它不启动核心、不接触安装实例数据、不代表实际双跳或四目标全协议运行验收。

## 已保留的开发期证据与未完成项

- 独立策略/解析审查：53 项专项通过；两个内核行为边界已显式登记。新事务/存储、生命周期和 API 的专项测试分别记录，不叠加重叠命令冒充独立总数。
- 本地固定 sing-box 1.14.2 四组合 × 两类上游语法检查通过；三项真实策略运行测试因内核 netlink `EPERM` 在监听前失败。执行器重试未到达测试，受其只读文件系统阻断；没有改变宿主设置、核心 pin、TLS 验证或转成 skip。
- 完整真实链路必须在已批准 CI 执行；本地不具备该权限不能泛化为所有平台不可用，也不能提前写成功。
- M3 界面/诊断/严格导出、完整 M4、实际用户线路、独立外部客户端、真实小型 VPS / 24 小时均另记资格。没有 tag、Release、旧资产覆盖或生产部署。

准确候选、正常合并与准确 master 的 workflow/job/step/attempt 以独立 PR 和[进度账本](DUAL_PROXY_RELAY_PROGRESS.md)登记；任何改动均使先前候选证据失效，必须重新检查。

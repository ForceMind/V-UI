# 每入口 TCP 三态主线收口（2026-10-18）

PR49 已正常合并。签名 master `0962f0c4995b3b7606652027f2330285e4dea1f7`，tree `b12d1e40d16060d3adfa1c29fb380ae5949896f5`；parents 为 a98 + 已验候选 b5，源树与候选相同。独立源码、运行证据与原包复核完成。

### 本次主线，而非继承候选

八组、11 个最新 jobs、全部步骤成功。七组 attempt 1，deployment attempt 2：
- [Portable Linux runtime matrix](https://github.com/ForceMind/V-UI/actions/runs/39158041506)：attempt 1
- [Real loopback proxy and DNS chain](https://github.com/ForceMind/V-UI/actions/runs/39158041508)：attempt 1
- [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/39158041565)：attempt 1
- [ACME certificate acceptance](https://github.com/ForceMind/V-UI/actions/runs/39158041480)：attempt 1
- [Documents and release contracts](https://github.com/ForceMind/V-UI/actions/runs/39158041631)：attempt 1
- [ToClash reference and export verification](https://github.com/ForceMind/V-UI/actions/runs/39158041515)：attempt 1
- [One-command installation acceptance](https://github.com/ForceMind/V-UI/actions/runs/39158041534)：attempt 1
- [Selected release deployment gates](https://github.com/ForceMind/V-UI/actions/runs/39158041466)：attempt 2

- 基础套件 1970 方法：1765 通过、205 跳过（跳过不授真实资格）。后续显式 proxy browser 20/20（13 浏览器 + 7 静态/路由）、实际 editor/API/SQLite/core 4/4（3 控制 + 1 真实）、D1 9/9（4 静态 + 5 浏览器）、managed editor 2/2 均完成。
- loopback 十组共 220 方法全部通过。新增每入口组为 7 控制 + 3 真实方法，52.517s；普通 YAML 的导出节点字段保持不变，普通 URI 字节原样交固定官方 Mihomo；测试仅加本地选择器外壳，direct/relay/inherit 同实例、host/global 四状态及全部 12 个有向转换。三场景分别完成 2/14/45 次业务 HTTPS，另各 1 次可达性控制；各场景保持单一客户端 PID。
- 普通 DNS 为 2/18/40 次，protected DNS 为 0/10/50 次；UUID/SNI/CA/停止上游/中转 UDP 均有真实拒绝原因及零意外业务送达检查，最终排空/回收完成。普通直连既有小 UDP 路径保留，中转 UDP 仍在服务端明确拒绝。
- 四目标原包各 228 项 manifest 完整集合/唯一性/大小/SHA256/CRC 通过。169 个仓库 payload 与准确主线匹配（两份前端文件仅执行既有确定性 release 变换）；两份 source archive 的 432 个文件逐字等 Git 源树。portable 的 installed mixed-config 8、distinct-identity 4、mixed-restore 4、old-reader refusal 4 均通过，未冒称其启动了代理或验证转发。
- 实际 x86_64 部署 4 方法（3 夹具 + 1 真实）494.134s 通过。512/384/320 MiB 三个 cgroup 各 29 阶段、1 CPU/无 swap，峰值分别 292331520 / 287379456 / 287408128 bytes，OOM/kill 均为 0。此为有限 cgroup 实测，不是低配整机 24 小时稳定性证明。

### 四目标实际包 SHA256

- x86_64-gnu kit：`cd096dec674a38205adb915de0e5f652108de4d40949405ba7e90fba721f83fb`
- aarch64-gnu：`f95c646141c85b2c7276004c8cee4a393b0e109522b91a20a1b22138555d0292`
- x86_64-musl：`285542335c48022236ae0befdfd826c1258d1f906738bf417a35c3c7c8ec7b79`
- aarch64-musl：`3246c07111b8619cacb5e5017d4e5f9ceb23cb18e184364546d0e2703d5f5704`
- deployment 实际消耗包：`bf4562614bb4c7bccbd6dcff8733905c5a731e39e845d78b2cf9841c7f6759a1`，100230321 bytes；228 个 payload 逐字同上述 x86 kit。

### 失败账本继续保留

- 8b 候选：两项新浏览器夹具导航失败，另 ARM glibc 构建遇 GitHub API 403。
- f2 候选：原生页面有多个监听器，而旧 pinned-Vue 测试夹具只保留一个，D1 静态失败；后续 editor 未运行。修复仅校正测试事件模型，不删秘密清理断言。
- 本次 master deployment attempt 1/job117765250940 在固定上游 source commit API 返回 HTTP403 rate limit exceeded，尚未执行部署门槛。原日志 SHA256 `a817d404edc206c7be7a69daf4224748855a561e72351ed7de35c0fd225ee83b`。仅对该失败 job 同 head 重跑一次；源码、pin、凭据、权限及门槛不变，attempt 2/job117765545990 完整通过。首轮失败没有改判。

### 用户路径与剩余范围

现成客户端导入不同普通 VLESS 节点即可分别使用已应用的直连/中转入口；第二跳由服务端配置。没有要求用户换自研手机 App。使用方法见[主线用户指南](PER_INBOUND_ROUTING.md)，计划与后续任务见[开发交接](DEVELOPMENT_HANDOFF.md)。

本次主线新增资格仅 TCP。stock Mihomo UDP 大包/同关联恢复、手机 VPN/TUN、公网真实线路、整机长期低资源测试仍独立待做。D2 实验及其 RED 没有迁入产品或改判。代码已合并；尚未新建 Release、发布或生产部署，VERSION 与官方核心 pin 保持。

## 候选与接手边界

最终候选 `b5a8e1ae000f2fe88b8e577378e76c073ebcb193` 与上述 master 同 tree；八组/11 jobs/所有步骤 attempt 1 通过，随后正常 merge 并独立核主线。候选八组：
- [Documents and release contracts](https://github.com/ForceMind/V-UI/actions/runs/39156458669)
- [ToClash reference and export verification](https://github.com/ForceMind/V-UI/actions/runs/39156458653)
- [ACME certificate acceptance](https://github.com/ForceMind/V-UI/actions/runs/39156458643)
- [One-command installation acceptance](https://github.com/ForceMind/V-UI/actions/runs/39156458625)
- [Real loopback proxy and DNS chain](https://github.com/ForceMind/V-UI/actions/runs/39156458627)
- [Portable Linux runtime matrix](https://github.com/ForceMind/V-UI/actions/runs/39156458778)
- [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/39156458659)
- [Selected release deployment gates](https://github.com/ForceMind/V-UI/actions/runs/39156458639)

[PR49 最终独立验收记录](https://github.com/ForceMind/V-UI/pull/49#issuecomment-6254315110)保留准确归档摘要、首败与重跑说明。本文记录的是以上已验软件提交；后续包括纯文档的新提交仍须独立审查、准确 CI 与正常合并，不继承旧提交资格。

下一步先按[标准客户端兼容计划](STANDARD_CLIENT_COMPATIBILITY.md)在独立实验中核 stock Mihomo UDP 容量与同应用关联恢复；不能改写原 sing 设备 RED 或开启产品中转 UDP。手机/公网、整机 24 小时、正式发布与部署分别按[交接任务表](DEVELOPMENT_HANDOFF.md)保留环境和权限门槛。

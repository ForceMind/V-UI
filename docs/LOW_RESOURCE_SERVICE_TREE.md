# 完整服务角色的资源夹具

本轮修正资源夹具的覆盖缺口，产品 allocator 与密码参数不变。新的准确候选尚待 CI；以下设计与本地测试不能替代受限资源验收。

## 为什么重新测量

`adacb467dba1b24fa7bde3aa3a1cd24cdb6059d5` 的资源服务 core 由 worker 直接启动，缺少产品 watchdog，也未继承 panel 的 GNU allocator 默认。旧证书夹具把 manager 与 HTTP-01 responder 放在同一进程，两个 job 后就结束。原始成功结果保留，但不能证明完整服务角色或整个十分钟的后台驻留。

[旧拓扑长测 37879396355](https://github.com/ForceMind/V-UI/actions/runs/37879396355) 于 2026-10-09 04:59:29 UTC 首轮成功；[原始附件 11596795715](https://github.com/ForceMind/V-UI/actions/runs/37879396355/artifacts/11596795715) ZIP SHA256 为 `9f401c8bc2ccc1710decc46573fb0c24bf0178d48e4f3dcb629e7811e4eb02b4`。独立按原 adac validator 复核，39 阶段全部通过：

- panel / 单 core 空载 1800.0014 / 1800.0155 秒，各 60 个 memcg 样本；末端总 cgroup 174985216 / 253923328 字节，单核平均 CPU 0.14605% / 0.16757%。未记录 PSS、PID starttime 或第 61 个 1800 秒边界样本。
- 1 / 10 / 50 持久连接分别持续 600.0008 / 600.0028 / 600.0120 秒，准确完成 600 / 6000 / 30000 个 64 KiB 响应；目标连接数准确、零错、无重连。每档另有一个新连接请求和面板恢复。
- 生命周期 peak 479956992 字节，在离线安装阶段达到；各阶段与采样的 max / OOM 均为零。UUID / CA 真实拒绝、目标零送达、登出重放、备份恢复、旧会话吊销和最终清理通过。

上述 242.160 MiB 包含 worker、文件页和内核，并非产品净占用。它不证明 160 MiB 空载预算通过，也不证明 CPU 或功耗改善。外部发生器的 rusage RSS 是高水位，不能相加当作同一时刻的 cgroup 峰值。

## 新的计账与继承合同

- 服务端使用实际 installed Python → installed `core_child.py` → bundled sing-box；逐次健康检查实际 PID、starttime、PPID、PGID、cgroup 与有限 allocator 字段，结束核整个进程组消失。父进程仍为资源 worker，明确不冒充 panel API apply/check；真实 API 节点流程由普通 deployment 门槛另验。
- watchdog / core 与 panel 式 manager / Certbot 使用同一个已验证目标的 panel 环境策略。独立 HTTP-01 responder 使用独立受限环境，明确无 panel mmap 默认和 malloc tunable。只记录这两个 allocator 字段，不输出完整环境或凭据。
- 两段 30 分钟 idle 各采 61 个固定槽，从真实 0 到 1800 秒、每 30 秒一次，保留全部成员、RSS/PSS 分项和前后 memcg 总账。要求采样位于本阶段真实时间窗，两阶段有序、不重叠，worker / panel 跨阶段身份不变；core / watchdog 绑定实际服务树。观察器、其他成员、页缓存及内核始终计入原总账，不清缓存、不减去分项改判预算。
- 长测先在另一个临时 unit 对同一个准确包执行完整 accounting 预检，再创建新的长测 unit。前置行为可能预热宿主页缓存，因此不称冷启动。workflow 的 150 分钟超时覆盖预检与原完整 90 分钟上限，不缩短任何测量阶段。

## 证书叠加合同

仅使用显式 loopback Pebble、私有测试 CA、假 `.test` 域名、隔离 DB 和真实 Certbot HTTP-01。首次假账户保留后创建新假账户，强制 due renewal 再验证；不泛称普通同账户续期或在线证书切换。

manager 必须运行产品真实后台线程，记录 TID / starttime 并持续检查。发布 phase 与旧 job 集合后才排队签发或提交 due 状态；用产品 process lock 等待前次应用结束，避免后台任务抢在 phase 之前运行。主线程只轮询 DB，不调用 `process_once()` 假冒后台任务。

独立 responder 导入实际 installed handler，安装清理与 signal handler 后启动 server，以真实非 challenge 404 探针确认就绪。GET 开始时只读取一次 phase；观察/写证据失败只使验收失败，不改变原 HTTP 响应。停止时等待真实请求线程退出并保留完整最终事件序列。

先确认后台线程和 responder 就绪，再完成 worker 的角色采样绑定 ACK，之后才开始 10 连接 / 600 秒 / 6000 响应。两个 job 完成后两种服务角色继续驻留，流量及新单连接恢复全部完成后才允许停止。后台 thread、Certbot 独立进程组、responder 进程组均需结束，最终 job 集合仍恰为原两条。所有角色留在服务 cgroup，Pebble / DNS / 流量发生器另组。

这里的 certificate manager 是额外独立 Python 进程，开销也计入总账；正式安装的独立 HTTP01 unit 在夹具中合并到同一个资源账本。因此这是受控服务角色覆盖，不是正式 systemd/OpenRC 单元拓扑、root 自动签发引导或整机验收。

## 验证与仍待完成项

新的准确候选 CI 尚未登记。单元与本地真实 Pebble 测试仅验证夹具行为；完整 600 秒、90 分钟、固定短门槛三档和 UI / 大数据仍需在新准确提交重验。既有 256 MiB 非稀疏日志、1000 节点、大规则、备份恢复语义、固定一页容差、OOM 和拒绝/清理要求均不放宽。

160 MiB 空载预算、真实整机 OS 余量、24 小时、双核心、真正过载、受限 root 全链路和四平台资源矩阵分别保留为未完成项。现有 CI 的 native ARM64 与 musl 环境可继续适配资源测试，不能用兼容性通过代替资源通过。

一次助手环境回退使未提交的早期夹具草稿和未公开大导出 allocator A/B 原始文件丢失。本次从已保存 adac 源码重新构建，不能声称字节恢复；旧本地摘要不充当可独立复核的原始结果。重建 WIP 独立保存，完成审查与重新验证后才更新性能候选。

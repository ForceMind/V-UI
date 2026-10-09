# Native ARM64 受限资源回归

状态：准确 `88d0c41dcfa26d7014130b04b5b94d4f04b7c611` 原生 GNU ARM64 短门槛通过。这里扩展既有四目标兼容矩阵的资源覆盖，不新增产品平台或正式支持承诺。

`run-low-resource-arm64` PR 标签显式启动 `Opt-in native ARM64 low-resource acceptance`，仅使用已有 `ubuntu-24.04-arm` 原生 runner，不使用 QEMU。开始核对实际 Linux / aarch64 / `aarch64-gnu`，记录内核、libc、基础页大小与准确源码；在限额外构建该准确提交的固定官方核心和离线 wheels 包。随后分别创建 512 / 384 / 320 MiB、零 swap、单核配额的非 root transient unit，复用既有 `low_resource_acceptance.py` 完整 smoke 合同。

每档都包含实际离线安装、HTTPS 验证、installed watchdog 与 bundled sing-box、真实 UUID / CA 拒绝及零送达、备份恢复、撤权与清理。实际 target、进程身份、allocator 策略和完整 cgroup 限额由现有校验器核验；原固定一个基础页容差按 runner 实际页大小记录，不增加容差、不放宽 OOM / 不完整报告拒绝。前一档失败时保留原始附件，后续档按工作流未执行处理，不把未运行记为成功。

证据附件包括 host 元数据、构建日志、每档 worker / coordinator JSON、服务及负向日志，保留 7 天。普通八组及 x86_64 原门槛保持。不同架构的独立构建包分别按源码与包摘要记账，不用 x86_64 结果替代 ARM64。

这是短时 cgroup 回归，包含 worker、客户端、所有服务子进程、文件页和内核计费；宿主 OS、构建和预先由其他组持有的缓存仍在范围外。它不替代 ARM64 的 30 分钟空载 / 10 分钟阶梯、UI / 证书 / 大数据、24 小时、真实整机或 root 全链路。musl 还须在实际受限容器中核验，不能把 systemd 对 docker CLI 的限制称为容器资源限制。

上一完整 x86_64 GNU 提交的证据及预算边界见[服务角色验收](LOW_RESOURCE_SERVICE_TREE.md)。Draft PR #28、master 与 VERSION 0.4.7 保持，无合并、正式发布或部署。

## 88d0c41 原始结果

[运行 37898943559](https://github.com/ForceMind/V-UI/actions/runs/37898943559) 的 [附件 11601414860](https://github.com/ForceMind/V-UI/actions/runs/37898943559/artifacts/11601414860) ZIP SHA256 为 `0817c8dc051fe1e4b0a7c5ba0525c529692ec26ca15af738fb0b79c30af81f24`，已下载、严格校验并独立审查原始日志。该准确提交普通八组与新增 ARM64 共 9 runs / 12 jobs / 131 步骤全部 attempt 1 success；条件 skip 不计通过。

实际 aarch64 / glibc 2.39 / kernel 6.17.0-1022-azure / 基础页 4096。三档各 29 阶段，一核、零 swap，512 / 384 / 320 MiB 的生命周期 peak 为 447537152 / 402653184 / 335544320 字节，max 为 0 / 38 / 102，三类 OOM 全零。384 和 320 从离线 wheels 安装阶段即触顶，不称有余量。

三档 build / worker 记录同一内包摘要 `aba9f5593a6eca98b78c658346aa38eb08a8c88c291c9082edfce13208a93b1e`；证据 ZIP 不含包字节，未独立重新计算内包摘要。原始 unknown UUID / x509 unknown authority、零送达、无 DIRECT、10 / 100 正向请求、实际 watchdog / core 阈值 131072、停机备份与恢复后会话吊销、旧 Cookie 401、全部服务正常退出和 unit 清理通过。systemd 退役后的约 3 MiB 摘要不替代 worker 的生命周期峰值。

该结果只属于上述准确提交与 GNU ARM64 短 profile；新的 [musl 容器资源合同](LOW_RESOURCE_MUSL.md) 仍须自己的实际结果。

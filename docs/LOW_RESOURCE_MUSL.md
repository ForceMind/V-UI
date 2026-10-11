# Native musl 容器资源合同

状态：`6216512` 双架构首次资源回归通过，见末节原始结果；后续 watchdog 就绪夹具修复仍须自己的准确提交结果。本地模拟通过不表示容器资源验收通过。产品平台仍为既有四目标，不新增容器部署产品承诺。

`run-low-resource-musl` 显式 PR 标签在原生 x86_64 / ARM64 runner 上分别构建 `${arch}-musl` 精确离线包。官方 Alpine 3.22、编译依赖与独立 harness 的固定 runtime 依赖在限额外准备；纯 smoke 不安装 Playwright。受测产品仍在新私有目录由包内 Python 完成离线安装。

## 实际限额与身份

每次新建非 root、禁自动重启的容器，从创建起设置 memory=512MiB、memory-swap=512MiB（不准额外 swap）、单 CPU 配额、private cgroup namespace 和 init。不添加 privileged、能力或宿主安全配置；容器 init、worker、安装子进程、面板、watchdog / core、客户端和目标全部保留在同一资源账本。独立 Docker CLI 所在 host 不是受测资源组。

容器入口只开放 smoke，必须读到 init 与 worker 的 `0::/`、只读 cgroup2 挂载及完整实际内核限额。原 systemd 后端仍严格要求随机 unit 的完整路径，不接受 cgroup 根。host 不信 Docker 参数自报：以精确 64 位 container ID 核镜像 ID / 原生架构 / UID、实际 init PID 的 host cgroup、NSpid 和 PID / cgroup namespace，再唯一映射容器 worker 的 PID 与 starttime。musl 目标来自已核容器与 installed runtime，不使用 Ubuntu host 的 GNU target 替代。

## 双握手与失败清理

1. 容器 READY 后等待 host 读回真实 host cgroup 的 memory.max、memory.swap.max、cpu.max、全部计数和身份；核验通过才 GO。
2. worker 使用原完整 smoke：两段 60 秒空载、110 次真实 VLESS/TCP/TLS 请求、实际 UUID / CA 拒绝、零送达、无 DIRECT、HTTPS 鉴权并发、停机备份恢复、旧会话拒绝和服务整组清理。固定一个实际基础页容差和 OOM 门槛不变。
3. 清理产品子进程后保存 terminal 并保活。host 再次绑定同一 init / worker，验证真实测量时间窗及 READY→worker→terminal→host 累计计数，保存终态内核账本后 ACK。ACK 前读数与其后的退出清理分别记录；memory.current / stat 是动态快照，不要求等于较早读点。
4. 缺证据、身份变化、提前退出、OOM、超时或错误目标失败。容器工作有独立 600 秒截止；GO / ACK 各最多等待 120 秒。host TERM / INT 锁存失败并清理。出错时尽力在 stop 前保存实际 host peak / events，无法读取明确标记。
5. 清理按本次已验证完整 ID 逐步 stop、kill、wait、取退出状态和日志、移除；前一步异常不能阻断后续清理。create 响应不确定时，只能用本次唯一名称、unit 标签和镜像 ID 解析回精确 ID。日志故障不阻断移除；Docker daemon 不可达不能假装容器已消失。清理或留证不完整不判通过。

外部协调器、镜像/包构建、宿主 OS 及预先由其他组持有的缓存不计入容器限额，不称整台 512 MiB VPS。保留容器自身完整 memory.current / peak / stat，不能采用扣缓存的 docker stats 数值替代。两个架构和各包摘要分别记账。

当前只规划两架构各 512 MiB 短门槛，不借此声明 musl 384 / 320、30 分钟、10 分钟阶梯、UI / 证书 / 大数据、24 小时、整机或 root 全链路通过。准确 CI 的首次失败亦须保留和定位，不用换阈值或重复碰运气取得绿色。

## 6216512 双架构首次结果

准确提交 `6216512fef009181531b759cb561168e65d9e163` 的 [双架构运行 37901949292](https://github.com/ForceMind/V-UI/actions/runs/37901949292) 两个 job / 每一步均为 attempt 1 success，已下载原始 ZIP、严格校验并独立复核。该提交另有下述 GNU x86_64 夹具首败，不能将本段外推为该提交整体验收通过。

- x86_64 [artifact 11603290122](https://github.com/ForceMind/V-UI/actions/runs/37901949292/artifacts/11603290122)：ZIP SHA256 `90f4baa07ed44e1996d1285851c5cf47582005bbb6fd9e66e76fb3cd614c4fb6`；包摘要 `da709b413e85b79f35d10794be8ee060dd869d69b61e301bf75befb1f21b8757`。实际工作 184.029 秒，ACK 前 host / worker peak 都为 459563008 字节（438.2734 MiB）。
- ARM64 [artifact 11603007438](https://github.com/ForceMind/V-UI/actions/runs/37901949292/artifacts/11603007438)：ZIP SHA256 `75062730c09517fc7ae5acf1c3d23cccfee0fb4af48717683c018d75ae5ea57a`；包摘要 `dba05110236818474781e667a9100f658ebe437b9719db3081b3109fa8ebf540`。实际工作 170.417 秒，ACK 前 host / worker peak 都为 452366336 字节（431.4102 MiB）。

各自 29 阶段、两段 60 秒空载、110 个代理与 100 个鉴权请求、真实 UUID / CA 原因、零送达、恢复撤销与旧 Cookie 401 通过。内外 memory.max=536870912、swap=0、CPU=100000/100000；init / worker UID1001、PID / NSpid / starttime / namespace / cgroup 映射一致，musl watchdog / core 没有 GNU allocator 默认。max / 三种 OOM 均零，峰值已在离线 wheels 阶段达到。READY / GO / terminal / ACK 来源一致，退出0、restart0、OOMKilled=false、精确容器确认移除；已退出后的 kill 返回1是清理诊断，不表示漏清理。

两包各自37个安装 wheel 与构建集合匹配，其中9个对应原生 musllinux wheel；host/build/worker 摘要一致，证据 ZIP 不含内包字节，未独立重算内包。上述 host 终态位于 ACK 前，不冒充 ACK 后退出期的连续采样或整机峰值。

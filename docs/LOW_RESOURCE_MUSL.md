# Native musl 容器资源合同

状态：后端和独立双架构工作流已实现，真实准确提交结果待验；本地模拟通过不表示容器资源验收通过。产品平台仍为既有四目标，不新增容器部署产品承诺。

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

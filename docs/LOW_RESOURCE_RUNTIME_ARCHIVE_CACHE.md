# 新 runtime 压缩归档的有界冷读

## 依据与限制

准确 `4c1d97ba86eaacaa04b1e26a78f4abaeefa94231` 的
[root 运行 38101639709](https://github.com/ForceMind/V-UI/actions/runs/38101639709)
首次通过完整安装、新目录升级及恢复。512 MiB / 1 CPU / 零 swap 父组峰值
516,374,528 bytes（492.453125 MiB），名义余量 19.546875 MiB，max/OOM 全零。
48 份采样将最后增峰保守包围在升级命令开始后
10.860733357–11.544821659 秒；相邻观察中旧 A panel、新 B stage 和 offline pip
以相同 PID/start 身份并存。它不是 pip 独占归因，异步 `memory.stat` 也不是峰值分解。

源码中，stage 对新 B 的压缩 runtime 完成冷读 pin SHA 后，`extract_runtime`
又通过普通 `tarfile.open(path)` 读取它。现有展开树写回仅处理解出的文件，
没有处理被重新读热的压缩归档。这是本候选唯一要验证的缓存生命周期缺口。
不改变 pip 参数、不清全局缓存、不暂停旧 A，不扩展安装器交接包的 fd 接口。

## 产品范围

- 只由 stage 为本次刚解包的 B payload 创建内部读取上下文。payload 的 dev/inode
  在 rename 前捕获；实际读取从根目录起逐级持有 no-follow 目录 fd，并绑定这个身份。
- payload 必须是当前用户拥有的 0700 真目录。归档以 fd-relative、
  O_NOFOLLOW/O_NONBLOCK 打开，必须是当前用户拥有的 0600、单链接、非空且有界的普通文件。
- 完整 pin SHA 和后续 tar 读取使用同一个 fd；沿用现有 `_PrivateArchiveFile`
  冷读提示，没有省略任何字节、成员、路径、链接或展开大小校验。
- 成功退出前复核目录链身份与归档 fd/路径的 dev/inode、大小、owner、mode、
  link count、mtime/ctime；目录时间戳不作为身份，以免把正常父目录活动当成损坏。
  最后提示仍作用于已持有的原 fd，不重新打开或冷却替换后的路径。
- 读取、pin、tar、身份或真实写回错误均进入原提取失败清理，再由 stage 删除本次 B；
  不执行 ensurepip，不切换 CURRENT，不处理旧 A 或用户数据。所有 fd 都收束，
  关闭错误不遮蔽已有主错误。
- 缺少安全遍历接口时，退回同一普通 fd 上的完整 pin/tar 读取，不对源文件发缓存提示。
  advice 缺失或拒绝只失去优化。通用 `extract_runtime` 调用仍默认使用原普通输入路径；
  用户下载包、手工 stage/restore 的源包没有因此获得新的冷却权限。
- 原 ensurepip、offline pip、pip check、完整 runtime 摘要、health、激活、备份恢复和回滚保持。
  没有新增 CLI、依赖、root 权限、服务或常驻进程。

pin SHA 原来位于 unpack 与 extract 的诊断边界之间；现在相同的完整校验位于
`extract_portable_runtime` 边界内部。未来比较必须披露这项计时边界变化，不能把
单个阶段耗时直接当同口径优化。全部工作仍留在原资源组。

## 可复核的本地机制实验

`tests/benchmark_runtime_archive_cache.py` 只使用自建私有合成文件，并要求非 root Linux
账号。先用 `df -T` 确认工作目录为磁盘文件系统，不使用 tmpfs。示例：

```sh
python tests/benchmark_runtime_archive_cache.py \
  --source-root . --work-dir /existing/disk-backed/work --member-mib 32
python tests/benchmark_runtime_archive_cache.py \
  --source-root . --work-dir /existing/disk-backed/work --member-mib 64
```

脚本按 plain/private/private/plain 顺序四次排他新建文件，每次结束后删除，inode 编号可复用；保留完整 pin 检查与提取，
记录源码文件 SHA、归档 SHA、每次 inode/大小/初始和最终驻留页以及耗时；
逐文件流式 SHA、目录/链接目标和权限必须一致。PROT_NONE/mincore 不主动读取映射页面。
起点驻留量仍可能不同；记录耗时不作为公平速度、CPU 或功耗比较。

在本地 UID 1000、overlay、4096-byte 页的机制实验中，32/64 MiB 合成内容对应的
压缩归档分别占 8,195 / 16,390 页；两次普通读取结束均全驻留，带身份保护的候选
两次结束均为 0 页，原归档字节及展开文件/链接/权限一致。这只是两个指定合成输入的
文件驻留观察，不是连续驻留峰值、memcg 收费归属、总内存或真实包的预计节省量。
两份报告的产品文件 SHA256 均为
`9c737e840c9b3b15dabaf16bc3041e78c8923214f2e466bf89d58d380db21c04`；
脚本在 import 前和完成后比对该源码摘要，源文件变化则拒绝输出结果。

## 验收状态与停止条件

当前为本地候选；Python 3.12.14 全量 788 项中 663 通过、125 环境 skip（56.239 秒），
独立审查的 71 项聚焦测试及额外六个替换/错误注入反例通过，未见 fd 泄漏；
文档、compile、JavaScript、shell 和 diff 检查通过。环境跳过不当作真实安装/核心通过。
此前 4c1d 的成功不属于此产品改动。尚未在本候选运行真实 root 安装/升级，
不宣称 492.453125 MiB 峰值已经降低，也不自动重复触发 opt-in root 测试。

如果发现源提示可作用于旧 A/调用者文件、校验语义改变、fd 泄漏、清理不完整或
不支持接口导致功能退化，停止推进并修复。实际包资源验证须使用准确候选、
保留原功能/安全/回滚控制以及完整测量成本，不为不同数字机械重跑。
真实 512 MiB 整机余量、完整大数据余量、160 MiB 工程预算及 24 小时仍未证明。

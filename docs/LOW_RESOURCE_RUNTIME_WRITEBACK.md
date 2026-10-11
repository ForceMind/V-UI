# 新展开runtime的提前写回

## 已测瓶颈

1458诊断使用同一准确离线包，512/320MiB两档各29阶段通过。512累计峰值从展开后318009344，经ensurepip374349824，在离线pip达到477663232字节；320在ensurepip首次触顶(max0→34)，pip继续到139。相邻边界间无peak/max增量。展开后的file约278/277MB，后续现有runtime摘要和health使file明显下降；这些仅为边界末分项，不是峰值同时组成。原件及scope见[安装诊断](LOW_RESOURCE_INSTALL_TRACE.md)。

## 唯一产品变化

在extract_runtime成功完成后、ensurepip启动之前，遍历本次刚创建的runtime目录，对普通文件fsync，再复用既有_PrivateArchiveFile的best-effort DONTNEED提示。目的目录通过exist_ok=False新建，包成员仍经过完整路径/链接限制及tarfile data filter。fd-relative fwalk不跟目录链接，普通文件以O_NOFOLLOW/O_NONBLOCK打开，fstat复核类型及inode身份；内部硬链接可重复同步，但不跟随符号链接。

不读取或冷却旧runtime、用户数据、源包、日志或备份，不清全局缓存。所有进程/文件页仍计入同组。原ensurepip、离线pip及check、runtime完整摘要、重复health验证、激活与恢复均执行；没有把提前写回当成摘要检查。追加工作仍计入extract边界，不更改7边界顺序。

写回错误沿已有提取失败路径清理新目录，再由stage清理新release；旧活动版本不变。advice不支持只失去优化；缺少posix_fadvise/DONTNEED或安全遍历接口的平台跳过此优化，不新增全局兼容依赖。Linux发行目标约束不变。文件页可能被之后的Python/pip重新读入，不能保证硬驻留上限或免费I/O收益。

来源证明继续对比098的72个产品文件，只允许app/release_tools.py这一计划内变化，记录实际差异路径及前后摘要；不会称新产品与098/1458字节相同。新head将使用原512/320短诊断和同输入测试量化峰值、max及耗时。当前安全测试/独立审查正在收敛，实际资源改善尚未验证；1458的320触顶结果保留，160MiB空载和整机资格也不改判。

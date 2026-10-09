# 新私有payload core写回

## 支持本实验的实际证据

9eed准确包的独立诊断37952321075中，两core普通文件共128,534,638字节（122.58MiB）。512MiB组三个离散观察点（解包、ensurepip、pip之后）均31,382页全驻留，整页计128,540,672字节；320组前两点全驻留，pip触限后sing-box从22,451降至9,651页，xray仍8,931页，合计76,111,872字节。两组各29阶段/OOM0，总peak392687616/335544320、max0/52。原件11626721815 ZIP SHA256 `fed69b8a885b2561de78724b550a0d19ef271f14ec253f6c14e1fe6d21011cb2`已独立复核。

观测采用PROT_NONE/mincore，没有读取目标内容或主动触页。它证明页在pip前仍热，却不证明memcg收费归属，不能与current/file相加，也不能把少12,800页解释为本cgroup释放恰50MiB。新实验不预设节省122.586MiB或320一定获得余量。

## 最小产品改动

既有unpack_verified对新release的wheel/runtime归档已经使用8MiB写回和best-effort DONTNEED helper。本次仅将相同路径扩到准确的cores/{x86_64,aarch64}/{sing-box,xray}输出，仍要求manifest.kind=release。backup及用户数据路径、旧core/运行中core、源ZIP均不新增提示；没有改变pip参数或缓存目录。

所有输出仍以xb排他新建，不能跟随已存在的文件或符号链接；源包快照、路径/成员/长度及每字节摘要规则保持。对新增core范围明确核输出fd为当前用户拥有的单链接普通文件，并在写回完成后核路径lstat与fd的dev/inode/owner/长度、链接数一致，再执行原chmod和完整后续验证。身份变化、写回失败、坏摘要均清理此次新payload，不触及旧版本。

额外fsync可能增加I/O等待；advice缺失或不支持时仅失去优化。不是全局清缓存，不提前删除任何验证步骤，也不读取文件来制造驻留观测。原29阶段和既有512/320 trace/mincore保持，产品源码变化仅release_tools一项。

## 验收状态

已增加真实fd/inode范围、跨8MiB批次、源及旧文件不变、缺少/拒绝advice、fsync失败、排他创建拒绝symlink、写中身份替换和坏摘要等本地反例；产品结果仍待准确新head回归。9eed普通deployment首attempt在官方GitHub API查询时403限流，资源未执行；明确记录该外部首败及一次恢复，不能改写成首次全绿。原性能卡160MiB全口径空载、512大数据总余量、24小时/整机等状态不因此改变。

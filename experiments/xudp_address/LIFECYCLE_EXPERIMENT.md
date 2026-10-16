# 独立 UDP 关联取消传播实验

状态：设计经审查，实施中的隔离实验；尚无本阶段实际核心结果，不能用于产品包。

## 已有证据与冻结边界

`45aa1f4af81d191d8211c4ae8b2f2ea6397071de` 的 [run 38947825998](https://github.com/ForceMind/V-UI/actions/runs/38947825998) 保留原门槛 **18/20**。独立完整域名回复证明初始域名/IP、初始端口和 payload 绑定；真实单包 idle 配置 2s，同一海外 UDP inode 到 +6.041s 仍存在，客户端明确关闭后才消失。固定依赖微测表明 timer 取消派生 context 不解除阻塞读；它不是对真实路由 context 的直接插桩证明。

原 9 行地址补丁 SHA-256 `ee08c4049b05d0fd72b1609839af574fe9be59b5e6c1ed4a1da54f0ffcb3a8ae`、原 runner/fixture/helper、原 20 项断言保持冻结。原失败不重写，不把新观察通过替代原失败。

## 最窄假设

只给固定 sing-box `route/conn.go` 的 UDP ConnectionManager 加独立补丁，不修改 sing/canceler、sing-vmess mux 或产品 pin。

仅满足以下三项时挂钩：
1. metadata.UDPConnect=true；
2. 显式 metadata.UDPTimeout>0，而非仅端口推断的默认 timeout；
3. 本次返回的是新建 TimerPacketConn，不能把复用旧 timer 返回的传入 context 当成 timer 子 context。

AfterFunc 只关闭本关联拥有的 outbound。正常 copy 首次结束先 stop hook、关闭 owned outbound，再转原 onClose；只 stop 不够，因为原上传收尾可能先卡在 inbound StatusEnd 写入。原 done.Swap 保持原 onClose 至多一次，nil onClose 同样需要清理包装。stop=false 不表示 callback 已退出；不能当作 join。

不取消父 mux context，不关闭国内共享入站 TLS，不添加无界后台 Close goroutine。原收尾已可重复 Close；测试区分调用次数和实际关闭转换，不宣称新增 exactly-once physical Close。

## 三路源码与二进制身份

- baseline：全部官方固定源码；
- patched：仅冻结地址补丁；
- lifecycle：相同地址补丁，再加独立 route 生命周期补丁。

每路独立版本字符串、路径、二进制摘要、两份补丁摘要及有序补丁清单，记录每个修改文件前后摘要。所有路固定同一个 Go 1.25.14 与规范化模块图。只替换国内测试进程；海外和设备继续核验既有官方 pin，每次启动记录实际 SHA、PID、parser 与 ready 证据。

schema2 kind 为 `vui-xudp-lifecycle-source-experiment`，独立 `lifecycle-provenance.json`。历史 baseline/patched 的 schema1 provenance 与旧 runner 语义保留；第三路不能伪装为 patched。

## 必要验证与准确口径

- 三路均实际运行原17项、TCP2项、同域同端口DNS1项，失败后仍执行其余独立组并审核身份/全部生命周期账本。
- 生命周期路目标是原19/20，保留已定位的原 test13 域名集合失败。另立独立域名等价门槛，严格完整回复、原目标/端口/payload、改投零送达和新关联拒绝，绝不声称原20项全绿。
- 原 test14 必须真正通过，并执行其后的新关联恢复。不能以单纯“socket最终没了”取代。
- 实际 ConnectionManager 单测覆盖 timer、持续上传/下载、正常结束、父取消、并发结束与取消、nil/阻塞 callback、原生deadline/复用timer/非connected/无显式timeout guard。
- Go取证使用真实被测代码和实际活跃 goroutine 栈分类，仅输出计数；测试自建 worker 用独立 done/join。每fixture先核零基线，不能以 onClose 或 read 返回冒充 copy goroutine 已退出。
- 真实运行固定2s/6s。最后一包后被动采样，国内专用 outbound TCP ↔ 官方海外 inbound TCP 配对 inode及海外 UDP inode均须在6秒截止前完整采样中消失，并有对应 session 的真实 End；跨过截止的消失属于不确定/失败。
- 国内父 TLS 可以保留，随后同父新 session 复用成功。另一个兄弟 session 每0.5s活动至8s，不得误断；停止后从最后成功活动另计严格6秒，不发送idle探针。
- 每种官方设备固定三轮重启。核旧PID reap、旧关联资源回归、海外身份保持、新实例ready与真实业务恢复。此项只证明套接字/进程/fixture worker界限，不能据此声称全部Go goroutine或RSS无泄漏。

## 必须保留的背压负例与停止条件

固定 mux Close 会先 localClose，然后同步写 StatusEnd；共享 writer 堵塞时，outbound先释放也不保证负责StatusEnd的copy结束。download 已卡在 inbound.WritePacket 时，关闭 outbound不能解锁共享入站写。

使用真实 `vmess.HandleMuxConnection` 与受控 net.Pipe 分别阻塞实际回复写、StatusEnd写；记录 outbound释放与仍活跃的copy/close边界。恢复读取后有界清理/join，不通过关闭父mux把失败变成成功。若完整回收需要重构mux写队列、取消父连接或新增无界后台关闭任务，停止本route-only实验并单独报告，不扩大补丁。

普通TCP代码不改；满足窄条件的VMess/shared XUDP路径仍可能受影响。VLESS测试和地址单测不授予其他协议产品资格；不发布实验核心、不进入runtime bundle、不向上游自动发帖。

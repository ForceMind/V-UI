# 离线安装短边界归因

完整合格基线 `098e15a70665520d0c0abfc056e97237237037dc` 已完成15 runs / 19 jobs / 205 steps首次成功，9份原件/14份worker报告独立复核通过；[PR28](https://github.com/ForceMind/V-UI/pull/28)保留准确结果。完整性能卡仍未通过：单core30分钟总cgroup采样均257.212MiB、最大260.469MiB；产品panel/core/watchdog映射PSS均146.905MiB与worker81.132MiB是归因口径，不能扣除worker或文件缓存宣称160MiB达标。

GNU短测在首个离线stage结束时，320/384MiB已达各自上限和最终全部max事件，后续短阶段不再增加。512MiB同stage峰值x86为477790208字节、ARM为447164416。阶段末低得多的current/anon/file不能解释峰值组成；先前原件没有内部调用的时间边界。大数据备份本次产品返回前累计peak500355072/max0，后续完整归档散列与恢复验证仍令总peak达到512MiB/max1234；不排除这些读取。

## 唯一新增范围

仅在验收器内包裹既有离线stage的7个同步调用边界：

1. 校验与解包发行包。
2. 展开固定portable Python runtime。
3. ensurepip。
4. 原离线pip安装。
5. pip check。
6. 准备好的runtime完整树摘要。
7. health，包含它原来的再次payload/runtime校验和实际服务启动/鉴权拒绝。

产品 `app/release_tools.py` 不修改。包装转发相同参数、返回值和调用次序，不省略操作、捕获掉产品异常、增加缓存回收或改变分组。health内部重复树摘要仍实际执行并计入health边界，不重复登记为第8个顶层阶段。包装只在此次stage存续，成功或异常均还原全部函数。

每个边界保留before/after单调时窗及同组memory.current/peak/stat/events/cpu.stat，部分失败原件仍保存；产品异常不会被诊断记录失败遮蔽。累积peak/max不重置，checkpoint开销也继续计入。相邻内核文件读取并非原子快照；这是首个涨峰调用的归因依据，不是峰值同步分解或每子阶段独立峰值。协调器核准确unit/source/cgroup、7项顺序、父阶段范围、累计计数单调与完整指标；所有原smoke健康、UUID/CA拒绝、备份恢复、撤权、清理及固定一页容差/OOM门槛保留。

## 来源和运行

显式标签 `run-low-resource-install-trace` 启动独立短workflow。一次构建准确新诊断head发行包，同一ZIP依次用于512/320MiB两档，各自仍1CPU、零swap、600秒worker截止。35分钟job上限覆盖构建和两套原29阶段smoke；普通PR不会自动跑此诊断，也不启动90分钟。

诊断记录`product_provenance`：与098对照app/web/deploy/third_party、main、VERSION、install.sh、runtime requirements、部署/系统入口、build_bundle及固定runtime/core/frontend准备脚本的Git条目和工作树字节；不相同即拒绝。保留路径集合、71个跟踪文件、条目SHA256、baseline和实际source。它证明这组产品实现/入口未改，不证明新发行ZIP与098逐字相同：manifest的source及诊断文档等包装内容可以不同。两档之间则必须使用同一次构建的同一包摘要。

此次只取得离线安装内部证据，未先实施产品优化，不引入新架构或常驻采样器。准确新head运行和数值尚待；098旧CI不能替代新诊断结论。真实整机OS余量、24小时、双core、真正过载和受限root升级仍分别待验。

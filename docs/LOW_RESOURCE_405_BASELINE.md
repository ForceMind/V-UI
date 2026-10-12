# 405b64a 准确提交基线与未达预算

HEAD `405b64a34c0ca691f14afe39eddd60bb1885b300` / tree `f6ccd960e4ab927bb5fee63e3c8212f46d6a908d`。临时复制真实watchdog并让preexec固定延迟0.4秒，旧夹具确定性复现Unexpected watchdog child command；旧621首败未记录argv，具体触发仍未知。仅修资源夹具：先监听后读取argv；未监听且argv为空或精确watchdog命令才允许等待原12秒截止，错误就绪命令、多child与超时仍拒绝。产品watchdog/内存/鉴权/TLS/负向/清理不变。

本地最终634项509通过125环境skip（37.813秒）；6项专项含实际延迟preexec/完整清理、监听探测期间exec读取顺序、错误命令/多child/截止拒绝，独立审查无阻断。文档、编译、diff通过。普通八组、GNU ARM三档、musl双架构512MiB，以及accounting/UI/证书/大数据/完整90分钟共15 runs、19 jobs、205步骤，均为准确本head attempt1成功并逐步骤核验；条件skip不计通过。所有资源原始ZIP已下载严格校验及独立复核。完整长测主段于2026-10-09 08:28:04UTC开始，09:59:06UTC全job终态成功；期间候选冻结。旧621失败与所有通过边界保留如下，不改判首跑成功。

四平台短资源原始证据均下载严格校验及独立复核通过。GNU x86 [37903227125](https://github.com/ForceMind/V-UI/actions/runs/37903227125)三档peak477609984/402653184/335544320、max0/70/148/OOM0；GNU ARM [37903294536](https://github.com/ForceMind/V-UI/actions/runs/37903294536)三档peak446889984/402653184/335544320、max0/40/101/OOM0，各3×29阶段，384/320都触顶。x86后续独立真实Vue/HTTPS/恢复171.287秒通过，不冒充浏览器在320MiB组内。证据artifact11603743256/11603981781 ZIP SHA256分别 `f435bb3822b7e56aef69e10daacca403aeed5b8619a58901d11c045c5ffd922c` / `43ea4427d6fb5952f0f07fdaef3cd9d29f2e24fa380bf99e77888559f5e41994`。

[双架构musl37903294984](https://github.com/ForceMind/V-UI/actions/runs/37903294984)每架构29阶段，实际187.213/169.137秒；ACK前host peak458375168/452247552、max/OOM0、exit0及容器确认移除。x86/ARM artifact11603033727/11603632029 ZIP SHA256分别 `6dc77c708772439ff88f6e7f7123963da3a367fc118cdd1d89b7570469199dc1` / `57cbdbbfb574b3d69312a5aa4b41dad9d744c86de1bd06aee40c35cfe598ab84`。严格validator与独立原始日志复核通过，不称整机或长测。各独立包摘要单独记账，未继承621。

[accounting37904772932](https://github.com/ForceMind/V-UI/actions/runs/37904772932)29阶段/13+13样本，peak476352512/max与OOM0，单core三服务角色PSS均144.282MiB（143.713–144.842），完整组末257064960字节，60秒诊断不判160预算。[大数据37904772880](https://github.com/ForceMind/V-UI/actions/runs/37904772880)45阶段，1000节点/四端点各41请求/256MiB非稀疏日志+64小文件/完整备份恢复与撤权通过；peak536870912、max3530/OOM0，备份阶段触顶。导出50.512170秒/CPU49.460006秒=97.917008%单核，仅该批观测，不因比旧head更快就声称优化。两份ZIP SHA256 `662c827d0f99a917b150a2d8af6a5620e5831c0982f19e9089f58253e6271c02` / `37afc641791a044e188c61763248d60a854dd73247617b0d55cbf446231eb4a4`，已严格验证及独立复核。

[UI37904771544](https://github.com/ForceMind/V-UI/actions/runs/37904771544)35阶段，真实可见19system+5core、隐藏0、双标签仅可见19+5；4端点各600+baseline1成功，原dashboard真实401、跳转及随后11秒无轮询/旧Cookie拒绝通过，peak478302208/max与OOM0。[证书37904771744](https://github.com/ForceMind/V-UI/actions/runs/37904771744)32阶段，10连接600.002476秒/6000响应无错，issue2.727750秒/due2.643625秒、实际后台/独立responder/Certbot/新challenge与材料保留、完整驻留和清理通过，peak479715328/max与OOM0。UI/证书ZIP SHA256 `86a30f2ff561ef62e2b393e50151a3c342d34d0c16ca09e370b62b6460f13851` / `e677a29a5a15b026bca3458a292140b98d8a18744489c2ba93a024d33e506ceb`，已下载严格validator通过及独立原始复核。证书两个精确任务窗口各有27个响应，粗进度counter差各28不冒充精确窗口数；121份服务快照绑定六角色和后台TID，独立responder持续驻留与清理核实。UI与随后proxy短测为分阶段；证书仅新假账户重验证，不泛称普通同账户续期/在线证书切换。


[90分钟37904771475](https://github.com/ForceMind/V-UI/actions/runs/37904771475)39阶段、同包独立accounting预检与实际CONNECT预检全部通过。[artifact11609351070](https://github.com/ForceMind/V-UI/actions/runs/37904771475/artifacts/11609351070) ZIP SHA256 `5b6ab6f4bddc988a5688afa1fedc080c9833b0e6f8721006ab314cebdb715082`，build/worker内包摘要 `803dca62858d36b2c01231f9f99c12725bd5767f611239459df5d5fc20974c76`。双空载1800.032637/1800.093034秒，各61固定槽；CPU0.202822%/0.354575%。面板单独RSS84.449–84.734MiB、PSS81.900–82.186MiB；单core完整产品panel+core+watchdog逐样本合计RSS158.594–159.586MiB（均159.512）、PSS145.649–146.642MiB（均146.567）。这些仅是离散样本，非连续峰值。完整cgroup阶段末177074176/271298560字节（168.871/258.730MiB），worker、页缓存、内核均留在总账；单core末次快照为271110144字节，不与阶段末混用，也不以RSS/PSS代替memcg。

1/10/50条真实持久连接分别600.000515/600.003071/600.014406秒，600/6000/30000个64KiB响应零错，最大响应9.600/9.046/36.072ms，每档另行单连接恢复通过。生命周期peak476844032字节（454.754MiB，已在离线安装阶段达到，非idle/load分项峰值）、max及三种OOM均0；实际角色身份/同组/allocator、UUID/CA真实拒绝零送达无DIRECT、备份恢复撤权与完整清理均验证。固定速率同宿主loopback不是吞吐上限或真正过载。

验收边界：四平台短资源门槛已具备真实证据，GNU x86完整90分钟及UI/证书/大数据也已通过功能和严格限额合同；160MiB完整空载预算仍未证明，320MiB安装仅能触限完成、不具工程余量，512MiB大数据备份触顶、导出接近满核。其他三平台长时/叠加场景、双核心完整资源、真正过载恢复、24小时连续稳定与真实512MiB整机仍未验；这些不能被本批通过替代。当前可复现性能瓶颈是大规则导出的覆盖判断和序列化，后续优化需保持完整规则语义并重测。

仍Draft，master a6aa84dc / VERSION0.4.7保持；无合并、发布、部署、购买机器或安全权限变更。


此页仅记录 405 的已验基线，后续优化必须独立验证；[原卡](LOW_RESOURCE_TARGET.md)和[旧拓扑及失败](LOW_RESOURCE_SERVICE_TREE.md)保留。

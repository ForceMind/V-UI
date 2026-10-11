# 071ee4a 准确提交基线与未达预算

HEAD `071ee4a57d0f81b83d22abee4390036141d8e566` / tree `3bb610e82616b8339f366fbcd2e392655331d885`，parent 为已完整收口的 405。唯一产品变更是每次 build_rule_plan 内的覆盖索引：精确类型集合、域名后缀查询、按需反转域名排序与二分查找；没有跨请求缓存，保持顺序、DNS、警告、归一化、IP 精确语义及失败不直连。固定内存/页容差/OOM/鉴权/TLS/恢复/清理门槛均不变。

原始 405 完整 planner 冻结为差分 oracle；本地641项516通过125环境skip（43.368秒），7新增专项与独立3,027,600个覆盖/重叠组合通过，最大合法输入的完整输出摘要一致，公开YAML字节一致。首份WIP的祖先后缀驻留造成真实内存放大，已在候选前否决：最大合法输入RSS高水位增量约181.9MiB，修正后约12.1MiB（405约10.1MiB），额外索引分配峰值约1.54MiB。过程保留于独立WIP，未验草稿不算正式候选。

同输入假1000节点、相同规则、同进程交替8次的本地直接route诊断中，YAML平均1.036870→0.540598秒，preview0.532873→0.081567秒，四端点正文摘要均等价；不含HTTP/鉴权/并发/cgroup，raw和sing-box波动不声称收益。准确记录、已否决草稿、修正数据和边界在[索引说明](https://github.com/ForceMind/V-UI/blob/071ee4a57d0f81b83d22abee4390036141d8e566/docs/LOW_RESOURCE_RULE_INDEX.md)。代码、测试、文档和benchmark独立复审无阻断；编译/文档/JS/shell/diff通过。

普通八组、四平台短资源、accounting/UI/证书/大数据和完整90分钟均按准确本head首轮成功：15 runs、19 jobs、205步骤，逐步核验，无失败或skip冒充通过。最后[90分钟37916606393](https://github.com/ForceMind/V-UI/actions/runs/37916606393)于2026-10-09 11:51:04UTC成功终态，完整主段10:20:10UTC开始；全部原始资源ZIP已下载、严格validator及独立审计通过。测量期间候选未变，没有继承405绿灯，也没有原样重跑。

[大数据37916543132](https://github.com/ForceMind/V-UI/actions/runs/37916543132)原始[artifact11609679783](https://github.com/ForceMind/V-UI/actions/runs/37916543132/artifacts/11609679783) ZIP SHA256 `aeeb2d486a63bbad50f342fb9e92ec006f8369239287219a70388579586e546e` 已严格校验和独立原始审计。45阶段，1000节点、512+512+64规则、四端点各41次请求、256MiB非稀疏日志和64小文件；四正文SHA/字节数、规则与日志摘要和405相同。导出批次23.875571秒、CPU22.853442秒（95.718936%单核），405为50.512170秒/49.460006秒；不同runner前后差不是严格A/B，也不把工作期间接近满核称低负载。实际停机备份/坏摘要保全/恢复保旧/会话和订阅撤权/负向及完整清理通过。总peak仍恰536870912、max3217、OOM0，在备份阶段触顶；没有解决512MiB余量。

GNU x86 [37916519005](https://github.com/ForceMind/V-UI/actions/runs/37916519005)三档peak479272960/402653184/335544320、max0/69/144/OOM0，各29阶段；随后独立真实Vue/HTTPS恢复146.284秒通过。GNU ARM [37916569658](https://github.com/ForceMind/V-UI/actions/runs/37916569658)三档peak448049152/402653184/335544320、max0/38/102/OOM0。两平台384/320均安装阶段触顶。双musl [37916576050](https://github.com/ForceMind/V-UI/actions/runs/37916576050) ARM/x86各29阶段，169.302937/186.654565秒，ACK前host peak452317184/459698176、max/OOM0，内外限额/身份/ACK后exit0/容器移除核验通过；不扩称整个退出生命周期或整机512MiB。上述短资源原始ZIP均已严格校验和独立审计。

[accounting37916583129](https://github.com/ForceMind/V-UI/actions/runs/37916583129)29阶段13+13采样，peak477429760/max与OOM0；单core三产品PSS均145.443960MiB、RSS样本最大158.414063MiB，总组末259489792字节。仅60秒诊断，不判160预算。[UI37916591404](https://github.com/ForceMind/V-UI/actions/runs/37916591404)35阶段peak479182848/max与OOM0；[证书37916598432](https://github.com/ForceMind/V-UI/actions/runs/37916598432)32阶段peak480149504/max与OOM0，600.002924秒/6000响应无错。三份原件均下载并严格validator及独立原始审计通过。UI两个原dashboard的401均早于登录导航，随后11.000874秒无轮询、旧Cookie拒绝，代理与UI为先后阶段。证书120快照/六角色及后台TID稳定，双任务2.861270/2.852393秒，精确窗口各29响应（粗counter30/29不混用），真实HTTP01/新假账户/旧材料保留及全程驻留清理通过；不泛称真实CA、普通同账户续期或在线证书切换。


完整长测[artifact11613553489](https://github.com/ForceMind/V-UI/actions/runs/37916606393/artifacts/11613553489) ZIP SHA256 `6b2367363c8cff269b11200c79751be5a095d0e3281d7b163283edc6800ec014`，内包 `38357ea92043be184b09492fddff49e7ba96a4eb0ad03fad92087e070a65f7e2`；独立同包预检后39阶段全过。面板/单core空载1800.033085/1800.094344秒，各61固定槽，CPU0.191789%/0.348102%。单core的panel+core+watchdog逐样本RSS157.605–158.871MiB（均158.764）、PSS144.630–145.896MiB（均145.788）；完整cgroup末173047808/262627328字节（165.031/250.461MiB），单core末次采样为261611520字节。离散映射采样不是连续峰值，不扣除worker/cache改判完整160MiB预算。

1/10/50真实持久连接分别600.000423/600.002828/600.013439秒，600/6000/30000个64KiB响应、零错；最大响应6.407/9.626/37.370ms，各档新连接和面板恢复成功。生命周期peak478777344字节（456.598MiB）、max与三类OOM全0；已在离线安装阶段达到，不是每阶段独立峰值。完整服务身份/allocator继承、真实UUID/CA拒绝零送达无DIRECT、备份恢复撤权和整组清理都通过。固定速率同宿主负载不称吞吐上限或真正过载。

本批收口与原卡剩余项分开：已测但尚未达证的是160MiB完整空载、320MiB安装余量、512MiB大数据备份余量；导出CPU总时间缩短，但执行期间仍近满核，不报告瓦数或节电比例。原卡仍可在既有空CI继续推进资源瓶颈、其他三个平台的长时/叠加、真正过载恢复及受限root完整链路；四平台短测已完成，不能称那些平台没有环境。真实512MiB整机与连续24小时尚未验证，尚无已核实的相应长期目标；不以数个重启CI拼接冒充连续24小时。本批不新增产品功能或测量范围，既有双核心未验边界也不改判。

本head全部资源原件索引（均已下载独立复核；这里是证据 ZIP 摘要，不是内层运行包摘要）：

| 范围 | 原始artifact | ZIP SHA256 |
| --- | --- | --- |
| GNU x86 短资源/部署 | [11610731314](https://github.com/ForceMind/V-UI/actions/runs/37916519005/artifacts/11610731314) | `d46e89d8889b6195710482e5c6cb5451f7127c2f881aee54988d6e2f8740e906` |
| GNU ARM 短资源 | [11610631001](https://github.com/ForceMind/V-UI/actions/runs/37916569658/artifacts/11610631001) | `768c594057aba3e0a0977279e69c161dea02ff788f22055a25e23b5951e30fce` |
| musl x86 | [11609829833](https://github.com/ForceMind/V-UI/actions/runs/37916576050/artifacts/11609829833) | `bc79b027e4903d8c5e09ca02a058a853e71e5e485c4701c9ed78a685f28e421e` |
| musl ARM | [11609980373](https://github.com/ForceMind/V-UI/actions/runs/37916576050/artifacts/11609980373) | `0918d6a8e927c9f194be5f57e0cec8b35d2e74c498ab79015df38d3a714826f7` |
| accounting | [11610610333](https://github.com/ForceMind/V-UI/actions/runs/37916583129/artifacts/11610610333) | `98fd32261ced2ea601c64ea30bc6f29ab9c0fca9a06713c8923fc92718992d1f` |
| 大数据 | [11609679783](https://github.com/ForceMind/V-UI/actions/runs/37916543132/artifacts/11609679783) | `aeeb2d486a63bbad50f342fb9e92ec006f8369239287219a70388579586e546e` |
| UI | [11609986598](https://github.com/ForceMind/V-UI/actions/runs/37916591404/artifacts/11609986598) | `95c2d4ec88c116a880a77df60089c78a43f664e9e9f9187ab446fbd2673ed47c` |
| 证书 | [11611211477](https://github.com/ForceMind/V-UI/actions/runs/37916598432/artifacts/11611211477) | `abe327949527d2b99bb7bb2e6e914fdcf9113b839f82c7db32b0f351a3d95c9e` |
| 90分钟 | [11613553489](https://github.com/ForceMind/V-UI/actions/runs/37916606393/artifacts/11613553489) | `6b2367363c8cff269b11200c79751be5a095d0e3281d7b163283edc6800ec014` |

其余普通门槛准确run：[Real loopback proxy and DNS chain](https://github.com/ForceMind/V-UI/actions/runs/37916518722)、[ACME certificate acceptance](https://github.com/ForceMind/V-UI/actions/runs/37916518801)、[One-command installation acceptance](https://github.com/ForceMind/V-UI/actions/runs/37916519171)、[Portable Linux runtime matrix](https://github.com/ForceMind/V-UI/actions/runs/37916518766)、[Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/37916518886)、[Documents and release contracts](https://github.com/ForceMind/V-UI/actions/runs/37916518858)、[ToClash reference and export verification](https://github.com/ForceMind/V-UI/actions/runs/37916518789)。

[405完整基线](https://github.com/ForceMind/V-UI/blob/071ee4a57d0f81b83d22abee4390036141d8e566/docs/LOW_RESOURCE_405_BASELINE.md)完整保存。160MiB完整空载预算、320MiB安装余量和512MiB大数据余量仍未达证；整机512MiB及24小时等原卡待验项保持。仍Draft，master a6aa84dc / VERSION0.4.7保持，无合并、发布、部署或新增机器。


此页是071准确提交的历史验收，后续变更必须独立验证，不继承本页通过。

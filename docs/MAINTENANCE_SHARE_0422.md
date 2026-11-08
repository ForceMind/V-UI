# v0.4.22 维护分享查询修复：计划与证据

## 当前结论（2026-11-08）

本批产品、正常合并、正式发布、短入口、备份后维护升级与读回均已完成。应用发布源固定为`558687a76cd29f4e4735284926eb05bf6664472e`，入口主线为`39181e0cdb9643422a19049df942b208f876faf4`；[PR140最终记录](https://github.com/ForceMind/V-UI/pull/140)补齐原文提交时尚未完成的入口资格。原配置与数据保持，具体运维明细仅私存。浏览器未验证。

入口资格单独核验，不能从应用绿灯继承：

| 阶段 | 准确提交 | 实际短入口安装 | 完整安装/七旧版升级 |
| --- | --- | --- | --- |
| 候选 | `231eae5e6f8020e384eb4a8e399dfda332d1307a` | [42399087684](https://github.com/ForceMind/V-UI/actions/runs/42399087684) | [42399087759](https://github.com/ForceMind/V-UI/actions/runs/42399087759) |
| dev | `330830fef7b0363c90d03b4c0a052157767208ea` | [42402759184](https://github.com/ForceMind/V-UI/actions/runs/42402759184) | [42402759148](https://github.com/ForceMind/V-UI/actions/runs/42402759148) |
| main | `39181e0cdb9643422a19049df942b208f876faf4` | [42405651147](https://github.com/ForceMind/V-UI/actions/runs/42405651147) | [42405651162](https://github.com/ForceMind/V-UI/actions/runs/42405651162) |

三者各自九组/12 jobs及全部必要步骤成功，12个明列浏览器步骤未运行；入口内容树均为`bdf00f17467193f9bde27a576da9510373dcb735`。实际公开main入口字节另行匿名读回，与受验源码及公开22双摘要一致。正式应用和旧21资产保持。

剩余开发计划只在[当前路线图](../ROADMAP.md)跟踪。以下源码准备、早期失败和阶段记录保留当时口径，不能将其中旧的“待完成”句子当作当前待办。


本轮最小修复、独审、Actions非浏览器验收、正常dev→main合并、公开22、独立短入口切换及私有备份后维护升级已获明确授权。配置、凭据、证书、协议、路由、代理开关、核心和旧资产保持。构建与安装仅在GitHub Actions执行，浏览器未验证；不源码热改、不修改SSH或防火墙。运维明细及敏感载荷仅私存，不进入公开记录。

## 根因与修复

v0.4.21的普通分享API已经接受实例和内容两个修订条件，并在同一ORM对象上原子核对；维护UI也已经发送它们。维护Bearer中间件的查询白名单仍仅接受`core`/`host`，请求因此提前返回422。TCP上游用途JSON POST不受此问题影响。

仅对既有精确GET capability `/api/subscription/link/{inbound_id}`追加`expected_instance_revision`和`expected_content_revision`。未知、重复及凭据查询参数仍拒绝；scope、Origin及原API原子校验保持。旧无条件与仅实例条件调用保持兼容，内容条件仍必须伴随实例条件。其他导出路径和方法不开放这两个查询字段。

新增七项真实ASGI/临时SQLite/grant/exchange/Bearer HTTP回归，覆盖成功分享、回收ID、同实例内容变化、非法/重复/未知参数、权限与来源。未mock中间件，使用假凭据，不运行浏览器或真实核心。

## 已完成的应用验收

产品与必要升级检查冻结于`72570cc449c2b4b8ac933588894360a5b284e329`。其后应用发布准备仅允许三项门禁文件与直接文档共13项普通文件变化；22专属flag不能扩大旧21例外，所有旧flag互斥。

| 阶段 | 准确提交 | 结果与安装证据 |
| --- | --- | --- |
| 候选 | `5d562529438bcbeabc562143ed747bfc772dce30` | 九组/12 jobs成功；[真实安装与七旧版升级](https://github.com/ForceMind/V-UI/actions/runs/42388410843) |
| dev（PR137普通合并） | `70bf4a0dc4b894650b2178f67399ab0a142cf822` | 九组/12 jobs成功；[真实安装与七旧版升级](https://github.com/ForceMind/V-UI/actions/runs/42391924480) |
| main（PR138普通合并） | `558687a76cd29f4e4735284926eb05bf6664472e` | 九组/12 jobs成功；[真实安装与七旧版升级](https://github.com/ForceMind/V-UI/actions/runs/42395305687) |

三个内容树均为`a9000284a7308fccee5ca795dca5dc5c5390dbc0`，两次合并的双父提交及树已回读。各阶段逐项核验最新九组、全部12 jobs和必要步骤，仅12个原有明列浏览器步骤未运行。新装、不可变公开14/15/16/17/19/20/21升级、固定核心、模板、netlink、完整链路和短入口均为必需。四目标只晋升同一main提交的已验制品，发布时不重建。

候选测试组实际执行2709项（233项按各自条件跳过），其中新增七项分享HTTP回归全部实际运行通过；固定核心校验另行通过。纯门禁133项、独审新增11项mock及公开升级fixture12项均通过，但不替代上述准确Actions与真实安装资格。

公开21已包含`content_revision`，21→22必须保持完整响应字段相等，不能再作为新增字段放宽；14–20的原新增派生字段契约保持。数据库、证书材料与订阅快照比较未更改。

### 保留的早期失败

产品冻结点的[手动测试42387952171](https://github.com/ForceMind/V-UI/actions/runs/42387952171)失败：旧21发布测试直接读取新VERSION22，导致`VERSION differs`及后续scope调用断言失败。七项新HTTP回归在该次已全部通过。最终发布准备将旧21 fixture的版本输入明确固定为21，未放宽生产门禁；最终候选/dev/main重新各自完成上述准确验收。早期失败不计为成功。

## 已公开的正式应用

[官方发布流程42398527616](https://github.com/ForceMind/V-UI/actions/runs/42398527616)使用22专属`maintenance_share_no_browser`门禁，晋升main `558687a76cd29f4e4735284926eb05bf6664472e`的受验制品，公开[Release v0.4.22](https://github.com/ForceMind/V-UI/releases/tag/v0.4.22)，Release ID为437741132。

匿名TLS读回确认tag/latest、19项资产集合、GitHub digest、真实`install.sh`/完整`SHA256SUMS`/`RELEASE.json`及source/version一致。旧21 Release ID及每项资产ID、大小、digest保持。

- 安装器真实字节SHA-256：`a20cc30d156ddfb74f2a451cf3642d050a863267b2f9b76804ec3539db8f48eb`。
- 完整SHA256SUMS真实字节SHA-256：`3401b6957bad51e6f129a97273c388a7657fd6b3bf939a2f2e4fa0f4e37475b4`。

## 短入口与后续交付

本提交复用本版本分支，仅切`setup.sh`版本与双摘要、`short-install.yml`冻结公开源及两项直接合同、直接说明。公开应用源固定为558687a，不能用后续入口SHA重标或重新发布22。保留已安装先本地菜单、管理/退出不下载、仅升级/修复下载以及原安全检查。拒绝旧21、未来23和重复22参数；历史七版升级fixture及其他CI/helper保持。

- [x] 最小产品修复、真实HTTP回归及独审。
- [x] 严格22门禁独审、准确候选/dev/main与同源四目标、新装及七旧版升级。
- [x] 正常公开22、独立匿名资产/真实摘要核验，旧21资产保持。
- [x] 入口源码独审及其准确候选/dev/main九组验收，实际`main/setup.sh`公开URL安装验证。
- [x] 入口完成后，按授权完成私有备份、单次正式版升级、真实双CAS分享及原配置数据和健康读回；具体结果仅私存。

入口与运维是应用公开后的独立阶段，未完成项不由应用发布的绿灯代替。阶段结果以相应PR/Actions及私有操作记录回读为准，不在此公开服务器标识、任务、凭据、导出内容或备份。

入口源码独审完成，13项差异无阻断；20项非PTY纯夹具、AST、Bash语法、文档与差异检查通过。完整终端交互和实际安装留Actions，不新增依赖。入口setup字节SHA-256为`a33f3dcf5cc64ab97e7e33764f3847b86c9ed70d6d88fb6ed3aa357c8fa0951a`，应用与安装器等冻结字节和历史七版fixture保持。此时入口自身准确Actions和实际main URL仍待完成。

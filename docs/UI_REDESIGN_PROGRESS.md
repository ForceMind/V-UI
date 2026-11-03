# 全站线稿与安装防火墙流程重整

## 本批交付与边界

用户确认v0.4.14安装完成，并要求按3x-ui对照建议简化防火墙流程、排查导航和返回异常、所有UI重新设计为线稿。当前迭代交付完整可点击低保真线稿、页面/状态/导航契约和未发布的防火墙开发候选。正式UI按线稿审阅后的决定实现。本批不覆盖已公开tag/资产，不部署用户VPS；没有获得新版本公开发布授权。构建与真实安装验收仅GitHub Actions，浏览器未验证。

从本地已核main `49136967d564aa0f517129e0c83fb4de0cacb34e`（公开应用冻结源b0cc174，版本0.4.14）在release/v0.4.15开发；该分支名仅预留下一版本，当前VERSION不升级、不代表0.4.15可下载。main/dev及原未跟踪xcrun_db保留。

## 静态证据与待证实部分

- web/index.html:18起使用Element Plus左侧el-aside；workspace/proxy/certificates使用独立header+nav；账户页另有独立表单布局。不同入口的导航内容和位置不同，确有多套页面壳。
- web/js/app.js:145只识别#inbounds，其他内部currentView并未同步URL；handleSelect:484–492在部分项location.assign、其余项直接赋值之间混用。已有popstate只关闭编辑/分享/导出，不是视图历史同步，因此不能指望Back恢复内部标签。
- routing/subscriptions两项都跳/workspace且不带目标，虽然workspace含subscriptions区块，目标语义丢失。
- main.py:108起根URL固定转/login；account.js:83登录后固定replace('/ui/')。固定地址原本有防开放重定向作用，新设计必须只恢复允许的内部页面，不接受任意外部next参数。
- 用户所述具体“有时右边”的实际渲染未做浏览器复现；不能将静态布局差异泛称唯一根因。当前未进行截图、真实渲染或浏览器返回测试。

## 设计收口

统一桌面左导航+顶部路径/账户、手机同一导航树的抽屉。登录独立，其他已登录页共享一个壳。每个主页面有唯一可刷新URL；明确当前项、面包屑与返回目标；弹窗关闭回原上下文，筛选/页码/滚动恢复；会话过期重登仅恢复内部允许路由。密码/token/敏感草稿不写URL或持久存储。

线稿必须覆盖所有现有功能与关键弹窗/状态，不删已有能力来简化设计。站点托管保持隔离/历史保留状态；UDP双跳、DNS-01等未获资格能力不作为可用功能展示。线稿中新增组织方式/诊断入口是设计意图，清楚标注尚未实现；所有例子均示例数据，不连接API。

## 防火墙候选

普通安装收敛到实际端口占用、CA结果和HTTPS/鉴权就绪，不因unknown连续提示“请手动放行”，不把默认声明10443当已建立节点。独立菜单诊断分本机规则/监听/未知云入口；单机自curl或代理TCP建立不证明全公网可达。手动放行由用户主动选择并明确确认，只安全managed UFW/firewalld、精确端口协议和回读，不自动启用、不猜zone、不改SSH/未知自定义规则。根模块仍从已验kit固定root-owned代码及receipt校验，不从service-writable payload加载root逻辑。

## 进度

- 已创建隔离版本分支，未提交源差异最初为空，保留xcrun_db。
- 两个有界worker分别负责docs/ui-wireframes与防火墙控制/测试，不重叠；无递归派工。
- 线稿与候选开发中。静态语法/契约检查、独审、Actions和最终成果入口在本记录追加；不预填通过。

## 可审阅成果

[完整离线线稿](ui-wireframes/index.html)与[设计/页面映射契约](ui-wireframes/DESIGN.md)已完成：12个主导航、37个审阅入口、登录、节点多协议五组表单及所有关键弹窗，手机抽屉与状态工具。单HTML、CSP禁止网络连接，不接API、无外部依赖、无storage；敏感输入不缓存，所有内容为示例。node语法、89个生成分支与6个非法路由的字符串层断言通过，cold modal关闭与dirty历史逻辑已静态收口；这些不等于真实DOM/浏览器验收，尚未渲染。

防火墙14文件候选已完成：普通安装不全面查规则、不报未知/云提醒，node TCP声明不阻断（dryrun冲突仅参考）；保留80/panel及显式UDP占用、CA和真实HTTPS强检查。root菜单10只读分层诊断、11确认后精确放行，仅已启用安全后端，严格root-owned代码/receipt和旧014精确迁移。联合定向170项通过3skip；新增迁移反例后局部41项通过（不把重复测试累加为总数）。首次HY2整组真实UDP bind被本地sandbox EPERM拒绝，未削弱断言；后续只选纯合成，完整真实安装和旧014升级待Actions。

独立审查进行中。当前正式web/app和公开014资产保持，导航/返回修复仍是设计契约，尚未上线。

独立只读审查：根控制模块、receipt精确旧closure迁移、固定OS执行、协议/端口确认回读、默认安装跳过全面探测与线稿静态隔离均无确定阻断；另35项独立纯mock通过。正式web/app无本批差异。将提交准确候选，推送ForceMind/V-UI的release/v0.4.15并建Draft PR以取得Actions证据，不合main、不发布；真实安装/旧014升级和浏览器当前仍未验证。

首次candidate a4aef6a的Actions：oneclick在构建前遇上游GitHub403限流，仅该job原样重试；portable三目标实际包消费暴露新增root closure的scripts/firewall_support.py未进入payload（FileNotFoundError），非线上014故障。已补builder精确允许文件一项，保持现有19公开asset集合；新提交重新完整验收，原日志保留，不继承部分成功或改断言。旧014→新root closure的真实升级仍须发布前单独证明，不将纯mock迁移当作整机升级证据。

## 用户确认后：正式UI实现

用户于2026-11-03确认线稿并要求开始制作。当前迭代在同release/v0.4.15上实现共享壳和真实导航/返回/弹窗历史，保留现有业务DOM/字段/API/权限/敏感清理/请求代际，不把wireframe示例数据带入产品。桌面固定左导航，手机同导航树抽屉；登录独立，账户登录态共享壳。尚未授权main合并或公开新版本，当前VERSION14仅开发候选，发布批次另按授权更新。

文件分工：shell worker拥有所有HTML/CSS与新shell.js；navigation worker拥有navigation.js和现有业务JS的导航/会话相关改动，必要main.py安全入口；root负责跨文件集成、测试兼容、记录和Actions。无递归派工，现有防火墙候选与xcrun_db保留。不会跳过业务、安全、核心、配置保真断言来适应新布局；旧结构专属断言必要时改为新共同布局契约。

主导航URL：/ui/#dashboard、#inbounds、#cores、#diagnostics、#firewall、#security、#legacy；/workspace#routing、#subscriptions；/proxy、/certificates、/account。返回目标使用固定站内允许集合，登录恢复不得信任任意nextURL；history只存非敏感路由/弹窗标识，密码/token/私钥不入URL或storage。现有站点隔离、通用日志及网页firewall权限边界继续明确，不能虚构可用API。

本轮正式UI开发中，未完成浏览器渲染/交互验收。仅非浏览器静态与mock契约检查，本机不构建Linux包或安装依赖；完整构建及安装在Actions。最终精确候选/失败/修正与结果在Draft PR107记录，不预填通过。

正式实现收口：五个业务入口已接同一shell与手机抽屉，原节点32字段绑定及表单API保持，节点编辑分五组；核心真实状态/重启保留，不可用入口明确说明。导航使用固定站内目标，workspace分区、401恢复、敏感modal Back/Forward与busy/dirty守卫已接入。新15项真实JS VM用例、shell事件/静态测试和既有YAML25项通过；无浏览器。独审发现pushState后nav/crumb/title不更新及refresh position重号，均已修并加源码联动/刷新多步返回回归；手机导航focus回主内容/拒绝回toggle亦补测。workspace Python bridge本地缺fastapi，留Actions不装依赖。

旧browser harness的aside选择器与中文状态断言仅同步当前markup语义，不执行browser；旧CSS历史fixture仍保留不可篡改。正式视觉/触控/浏览器Back验收仍未验证，不把Node mocks当DOM渲染。当前制作候选留Draft，未合main/发布，数据/账号/证书与已发布014保持。

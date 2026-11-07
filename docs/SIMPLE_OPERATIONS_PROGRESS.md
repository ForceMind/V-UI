# 简单操作改版：唯一计划与进度

2026-11-07 用户确认线稿和完整范围，要求小白可用、实现后独审、构建与真实安装全部GitHub Actions、不浏览器、保留原配置和数据。分支 `release/v0.4.21` 从dev076f170创建，基线树与main ef21d4e一致。原目录未提交内容和本地未跟踪文件保持。线稿为同会话 `ue-simple-operations-20261107/index.html`，仅设计参考，不是线上验收。

本轮授权：实现、必要安全契约/测试/说明、提交推送Draft PR、独审及准确候选Actions。不包含合并受保护分支、公开21、覆盖20资产或修改VPS；完成候选后分别申请对应明确授权。产品版本在本次可交付批次完成时更新，不为中间步骤反复升版。

## 完整交付与迭代

1. 三个真实任务：普通节点、中转节点、服务器程序代理。名称/端口建议/唯一正式证书/首次代理账号密码预制；唯一合法上游候选推荐，复杂输入保留明确选择。主按钮内部串联导入、保存、检查、确认和读回，不让用户操纵事务字段。
2. 节点简单编辑和普通/TCP出口分享。只改明确字段，保留原核心、UUID、路径、过期和未知高级配置；不重复创建，不把保存当启动成功。
3. 同一授权维护工作区使用相同流程和实际scope。Bearer只在内存，离开清除；不通过跨页链接或管理员cookie绕过授权。维护升级/恢复从明确任务和成功备份列表操作，自动有界观察而非重复提交。
4. 证书设置/返回、订阅分享、普通管理按钮统一。96项静态模板及运行时按钮逐项核名称、前置、影响、成功/失败/取消/复制失败。技术工具折叠，异常和必要影响确认不隐藏。
5. 独立只读审查（1名gpt-6.1-sol/high，复用），范围内修复。准确候选完整非浏览器Actions、固定核心、四目标、真实安装/升级和普通/TCP客户端验收。浏览器未验证。

## 必须保留的行为

- 首次显式direct仅在明确OFF空基线且无未知草稿/事务时受控初始化；不静默应用他人草稿。检查与应用传真实revision/source_fingerprint，应用不确定时沿同一个幂等键，节点创建POST不盲重发。
- Xray启用/运行时中转门禁不放宽；手动停止的核心不偷偷恢复。需要启动会影响其他已启用节点时先列影响并明确确认。
- 单selected_upstream保持；切出口会影响既有中转/本机代理，必须列明影响并确认。只创建节点不切他人出口，不默改全局relay开关。
- 本机代理仅127.0.0.1、独立鉴权、显式程序使用；不宣称全系统代理，不增加TUN/路由/SSH/防火墙权限。已有凭据保持，明确交付才读取敏感信息，不写日志/URL/浏览器持久存储。
- 唯一证书自动采用，多张才选；CA条款本人确认。端口只是建议，后台仍校验，不能冒充公网可达。
- 不回退DIRECT、不扩UDP/协议/多出口资格，不更换固定核心或前端依赖。

## 验证和证据

代码审阅已核页面事件→API→校验→提交/恢复；原wizard14项Node合成DOM及连接页3项检查通过，不能代替新流程资格。新控制器先做无浏览器状态/请求顺序/权限/错误测试；后端接口及保数据边界、真实核心与系统安装仅Actions。源码/独审/CI/四目标/真实安装/实际部署分别记录。

## 当前进度

- [x] 代码审阅、交互线稿、用户完整范围确认、独立开发分支。
- [x] 共用任务控制器与三个主流程（源码/合成，真实资格待Actions）。
- [x] 节点编辑/两种交付/作用范围与异常（源码/合成）。
- [x] 授权维护/证书/订阅/备份与按钮核对（源码层）。
- [x] 非浏览器定向检查、独审和修复（源码复核；准确Actions另列）。
- [ ] 提交推送Draft PR、准确Actions四目标/真实安装/公开20保数据升级。

早期WIP快照（后续完整实现及独审见下方）：共用控制器和三个任务、基本编辑、两种交付、同页Bearer transport及新增后端CAS/显式本地凭据交付已实现；37项Node状态/合成DOM、维护会话旧契约与3项连接页检查通过。新增后端状态/密码引用/停止偏好回归仅AST已核，依赖完整运行放Actions。证书、订阅、任务及逐按钮核对仍在实施。

核心及完整页面实现已完成，现进入准确候选Actions修复与验收；尚未发布21、未改服务器。后续阶段继续更新本页，不把阶段样例或原20绿灯称作新候选验收。

## 本批代码层按钮核对（尚非浏览器验收）

原线稿96项静态模板，本批新增直连/中转入口和任务核对等后现有98项。以下逐项对应事件与处理归属；历史旧向导、旧嵌入分流/订阅及旧分享对话框作为兼容保留，不再作为默认新建/分享路径。动态按钮在表后单列。只核代码、权限、忙碌/异常/清除状态，未声称真实页面渲染通过。

| 页面位置 | 按钮 | 实际事件或ID | 已核处理边界 |
| --- | --- | --- | --- |
| web/account.html:23 | 登录面板 | `submit` | 账户接口校验；改密/退出清除会话，失败保持输入 |
| web/account.html:37 | 保存并退出全部会话 | `submit` | 账户接口校验；改密/退出清除会话，失败保持输入 |
| web/account.html:39 | 退出登录 | `logout` | 账户接口校验；改密/退出清除会话，失败保持输入 |
| web/certificates.html:18 | 申请证书 | `issue` | 本人CA确认后队列提交/只读刷新；旧材料保留，处理中不重复申请 |
| web/certificates.html:19 | 刷新状态 | `refresh` | 本人CA确认后队列提交/只读刷新；旧材料保留，处理中不重复申请 |
| web/index.html:43 | 重启 Xray | `restartCore('xray')` | 明确整组重启影响、单飞、重新读真实状态；中文错误 |
| web/index.html:50 | 重启 sing-box | `restartCore('sing-box')` | 明确整组重启影响、单飞、重新读真实状态；中文错误 |
| web/index.html:69 | 刷新状态 | `refreshStatus` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:137 | 刷新节点 | `fetchInbounds` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:138 | 创建直连节点 | `openSimpleOperation('direct')` | 默认共享操作组件；实例/内容/出口条件，失效阻止继续 |
| web/index.html:139 | 创建中转节点 | `openSimpleOperation('relay')` | 默认共享操作组件；实例/内容/出口条件，失效阻止继续 |
| web/index.html:146 | 清除 | `inboundSearch = ''` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:167 | 分享连接 | `openSimpleShare(scope.row)` | 默认共享操作组件；实例/内容/出口条件，失效阻止继续 |
| web/index.html:169 | 设置 | `openSimpleEdit(scope.row)` | 默认共享操作组件；实例/内容/出口条件，失效阻止继续 |
| web/index.html:170 | {{ scope.row.enable ? '停用' : '启用' }} | `toggleInbound(scope.row)` | 仅请求启停和实例条件；读取实际运行状态，不改凭据 |
| web/index.html:179 | 完整高级编辑 | `openEditInbound(scope.row)` | 高级字段保留原协议/凭据；实例保护、证书选择、取消和失败保留 |
| web/index.html:181 | 删除节点 | `deleteInbound(scope.row)` | 永久删除确认+实例条件，无旧DELETE兜底；已保存未应用分开 |
| web/index.html:197 | 恢复默认 | `resetRoutingDefaults` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:198 | 保存并应用到订阅 | `saveRouting` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:291 | 全开 | `setCategoryEnabled(group.id, true)` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:297 | 全关 | `setCategoryEnabled(group.id, false)` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:332 | 添加区域 | `addIntranetZone` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:366 | 删除 | `removeIntranetZone(scope.$index)` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:442 | 复制 | `copySubscription(scope.row.format)` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:453 | 刷新状态 | `refreshStatus` | 只读刷新或页面草稿编辑；保存动作才提交，旧嵌入配置保持 |
| web/index.html:459 | 重启 {{ core === 'xray' ? 'Xray' : 'sing-box' }} | `restartCore(core)` | 明确整组重启影响、单飞、重新读真实状态；中文错误 |
| web/index.html:479 | 上传站点（不可用） | `disabled` | 不可用操作明确禁用，不伪造上传成功 |
| web/index.html:512 | 给手机或电脑用 手机 / 电脑 → 本机 → 网站 创建后获取连接链接或二维码。 | `nodeWizard.purpose = 'device'` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:515 | 给另一台服务器用 另一台服务器 → 本机 → 网站 本机提供出口，供另一台 V-UI 导入。 | `nodeWizard.purpose = 'server'` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:537 | 重试读取 | `loadWizardCertificates` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:538 | 重新读取 | `loadWizardCertificates` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:543 | 打开高级新建，选择其他协议或配置 | `wizardAdvanced` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:552 | {{ nodeWizard.purpose === 'device' ? '生成客户端链接 / 二维码' : '复制给另一台 V-UI' }} | `wizardDeliver` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:555 | 刷新节点列表和状态 | `refreshWizardResult` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:560 | 上一步 | `nodeWizard.step = 1` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:561 | {{ nodeWizard.step === 3 ? '返回节点列表' : '取消' }} | `closeNodeWizard` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:562 | 下一步 | `nodeWizard.step = 2` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:563 | 创建节点 | `createSimpleNode` | 旧兼容向导；原假凭据/取消/错误/生成/高级转入契约仍回归 |
| web/index.html:699 | 重新读取证书 | `retryManagedCertificates` | 高级字段保留原协议/凭据；实例保护、证书选择、取消和失败保留 |
| web/index.html:957 | 取消 | `beforeInboundEditorClose` | 高级字段保留原协议/凭据；实例保护、证书选择、取消和失败保留 |
| web/index.html:958 | {{ editingInboundId ? '保存并应用' : '创建并应用' }} | `saveInbound` | 高级字段保留原协议/凭据；实例保护、证书选择、取消和失败保留 |
| web/index.html:985 | 生成上游配置 | `requestUpstreamExport` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:994 | 复制配置 | `copyUpstreamExport` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:995 | 下载配置 | `downloadUpstreamExport` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:1000 | 关闭并清除 | `closeUpstreamExport` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:1013 | 复制链接 | `copyLink` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:1014 | 重新获取 | `retryShare` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/index.html:1018 | 关闭并清除 | `closeQrDialog` | 旧兼容显式交付；敏感响应只在本页，关闭/失效清除 |
| web/maintenance.html:24 | 生成一次性维护链接 | `maintenance-create` | 三档预设仍需明确提交；默认只读30分钟，链接一次显示 |
| web/maintenance.html:25 | 复制链接 | `maintenance-copy` | 仅本页凭据复制/清除，复制失败选中；结束中止读请求 |
| web/maintenance.html:25 | 清除显示 | `maintenance-clear` | 仅本页凭据复制/清除，复制失败选中；结束中止读请求 |
| web/maintenance.html:26 | 刷新授权 | `maintenance-refresh-grants` | 受scope限制的只读读取；失败不假称成功 |
| web/maintenance.html:27 | 清除本页维护会话 | `maintenance-end` | 仅本页凭据复制/清除，复制失败选中；结束中止读请求 |
| web/maintenance.html:30 | 读取安装版本与服务状态 | `maintenance-refresh-status` | 受scope限制的只读读取；失败不假称成功 |
| web/maintenance.html:31 | 读取服务状态 | `status` | 受scope限制的只读读取；失败不假称成功 |
| web/maintenance.html:31 | 创建私有备份 | `backup` | 固定动作、权限与影响确认；唯一任务提交，有界只读观察；恢复只选成功备份 |
| web/maintenance.html:31 | 重启 V-UI | `restart` | 固定动作、权限与影响确认；唯一任务提交，有界只读观察；恢复只选成功备份 |
| web/maintenance.html:31 | 启动 V-UI | `start` | 固定动作、权限与影响确认；唯一任务提交，有界只读观察；恢复只选成功备份 |
| web/maintenance.html:31 | 停止 V-UI | `stop` | 固定动作、权限与影响确认；唯一任务提交，有界只读观察；恢复只选成功备份 |
| web/maintenance.html:31 | 在线升级正式版 | `upgrade` | 固定动作、权限与影响确认；唯一任务提交，有界只读观察；恢复只选成功备份 |
| web/maintenance.html:32 | 停机恢复该私有备份 | `maintenance-restore` | 固定动作、权限与影响确认；唯一任务提交，有界只读观察；恢复只选成功备份 |
| web/maintenance.html:33 | 已核对任务记录，允许下一次操作 | `maintenance-task-ack` | 先读任务再明确确认结果，才解除未知写入锁 |
| web/maintenance.html:34 | 刷新维护任务 | `maintenance-refresh-tasks` | 受scope限制的只读读取；失败不假称成功 |
| web/maintenance.html:35 | 刷新记录 | `maintenance-refresh-audit` | 受scope限制的只读读取；失败不假称成功 |
| web/maintenance.html:36 | 读取近期脱敏诊断 | `maintenance-refresh-logs` | 受scope限制的只读读取；失败不假称成功 |
| web/proxy.html:14 | 打开只读预览 | `open-yaml-preview` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:16 | 配置本机向外转发 | `open-connection-setup` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:18 | 刷新服务器状态 | `refresh` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:22 | 核对后保留页面编辑 | `rebase` | 明确处理页面草稿冲突，不暗中应用服务器草稿 |
| web/proxy.html:22 | 放弃编辑，载入服务器草稿 | `discard` | 明确处理页面草稿冲突，不暗中应用服务器草稿 |
| web/proxy.html:25 | 刷新管理通道状态 | `tailscale-refresh` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:36 | 粘贴另一台服务器的连接配置 | `open-import` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:38 | 保存草稿 | `save` | 高级原始流程；CAS、源/事务键及明确核对保持，不成为默认步骤 |
| web/proxy.html:38 | 检查已保存草稿 | `check` | 高级原始流程；CAS、源/事务键及明确核对保持，不成为默认步骤 |
| web/proxy.html:38 | 应用已保存草稿 | `apply` | 高级原始流程；CAS、源/事务键及明确核对保持，不成为默认步骤 |
| web/proxy.html:38 | 重试同一次应用 | `retry-apply` | 高级原始流程；CAS、源/事务键及明确核对保持，不成为默认步骤 |
| web/proxy.html:41 | 确认以上内容并应用此修订 | `confirm-apply` | 高级原始流程；CAS、源/事务键及明确核对保持，不成为默认步骤 |
| web/proxy.html:41 | 取消本次核对 | `cancel-apply-review` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:43 | 检查已保存上游线路 | `diagnostics` | 显式诊断服务同意、只操作自身有界任务 |
| web/proxy.html:43 | 取消线路诊断 | `cancel-diagnostics` | 显式诊断服务同意、只操作自身有界任务 |
| web/proxy.html:50 | 下载严格 Mihomo YAML | `export-mihomo` | 明确含凭据下载；活动修订/实际核心条件，失败不回退DIRECT |
| web/proxy.html:50 | 下载严格 sing-box JSON | `export-singbox` | 明确含凭据下载；活动修订/实际核心条件，失败不回退DIRECT |
| web/proxy.html:63 | 取消 | `close-import` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:64 | 预览可导入节点 | `preview-import` | 导入预览单独校验；保存确认候选/忽略项，保留严格字段 |
| web/proxy.html:65 | 保存所选上游 | `save-import` | 导入预览单独校验；保存确认候选/忽略项，保留严格字段 |
| web/proxy.html:67 | 关闭并清空 | `close-yaml-preview` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:73 | 生成只读报告 | `yaml-preview-submit` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/proxy.html:73 | 清空输入与报告 | `clear-yaml-preview` | 高级只读或展开/关闭/清空操作，原接口权限和离开清理保持 |
| web/workspace.html:12 | 重试初次载入 | `initial-retry` | 只读重试或页面草稿编辑，刷新节点清旧选择，避免复用数字ID |
| web/workspace.html:14 | 读取已保存设置 | `reload` | 客户端分流草稿/预览/保存边界，保留40项服务及修订冲突 |
| web/workspace.html:14 | 预览草稿 | `preview` | 客户端分流草稿/预览/保存边界，保留40项服务及修订冲突 |
| web/workspace.html:14 | 保存客户端分流 | `save` | 客户端分流草稿/预览/保存边界，保留40项服务及修订冲突 |
| web/workspace.html:20 | 添加内网区域 | `add-zone` | 只读重试或页面草稿编辑，刷新节点清旧选择，避免复用数字ID |
| web/workspace.html:24 | 刷新节点和订阅 | `refresh-grants` | 只读重试或页面草稿编辑，刷新节点清旧选择，避免复用数字ID |
| web/workspace.html:30 | 生成订阅地址 | `create-grant` | 所显示完整节点身份修订、显式令牌交付；复制失败保留、离开清除 |
| web/workspace.html:31 | 清除地址显示 | `forget` | 所显示完整节点身份修订、显式令牌交付；复制失败保留、离开清除 |
| web/workspace.html:31 | 复制地址 | `copy` | 所显示完整节点身份修订、显式令牌交付；复制失败保留、离开清除 |
| web/workspace.html:31 | 下载 Mihomo YAML | `download` | 所显示完整节点身份修订、显式令牌交付；复制失败保留、离开清除 |

动态按钮核对：

- `simple-operations.js`：三任务/基本编辑/统一出口返回、证书唯一/多选及本人申请、未知草稿继续/恢复、停止组/出口影响确认、创建与核对同一次请求、显式缺失绑定确认、普通/TCP交付/二维码/复制失败、局部启停/删除、订阅生成/轮换/撤销/清除、证书续期/自动续期状态、独立本地代理凭据交付。普通按钮不要求输入修订/任务ID/材料路径。
- `maintenance.js`：动态撤销仅对应当前授权；任务列表只显示固定动作/状态/代码和时间；备份下拉仅接受真实成功备份ID。提交未知时需读取记录并显式解除，自动观察仅GET且有次数上限。
- `certificates.js`：续期、自动续期、应用面板、选择并绑定已有节点、重试应用、下载公有证书链；既有停止偏好和失败旧材料保持；不提供私钥下载。
- `workspace.js`：移除内网区为页面编辑；轮换明确旧地址失效、撤销明确客户端拉取后果；身份缺失/变化不允许轮换修复，必须重选创建。复制失败保留同一地址不重新颁发。
- `proxy.js`：旧上游移除和诊断/导出动态控件仍留在高级区域，原CAS与pending恢复门禁保持。

独审处理中；准确Actions与真实安装尚未完成。候选阶段不更改短入口20、不合并或发布21、不连接VPS。

## 候选独审修复与本机有限检查

唯一新增独审gpt-6.1-sol/high只读检查发现五项实际缺口：事件接收面、modal ID校验、分享GET→导出间实例竞态、同页确认范围漂移、HTTP200截断后可再次创建。已逐项修复，增加失败/恢复回归；审查者初步复核无其他确定阻断，最后差异待复核。本机184项Node合成状态/事件/导航/旧契约及维护会话、连接页3项和状态轮询3项通过；只涉及代码执行，不是浏览器渲染、真实核心或安装资格。新后端持久化/身份/实例导出回归须Actions。

Actions历史：e84ad25的42343296288因模板检查按旧末尾div取范围失败；6dcb8b4的42343662389已执行约2700项单测但有五项失败（旧文案/旧入口/CSP假设及新测试重复no-store假设），没有宣称通过。已修正检查范围/新的明确文案与入口期望，保留原身份/权限/失败断言；核心与安装步骤尚无本批通过证据。新增Node真控制器→真实HTTP→固定核心的三任务端到端方法由既有loopback Actions运行；临时CA/假证书和账户，不连接生产或真实CA。

## PR #133 准确候选验收

[Draft PR #133](https://github.com/ForceMind/V-UI/pull/133)以dev为目标；72d66e3通过独立只读复核。其九组Actions已启动，四目标运行时、选定部署、ACME、ToClash和公开20短入口验收成功；单测2678项有3个固定清单/计数失败及1个文档版本错误，未把运行中步骤算成功。新增Node进程只有受控管道控制器、不启动客户端核心，已将精确调用点纳入原启动屏障清单；公开20真实升级新增后严格更新旧版本固定枚举，不删除旧升级或弱化断言。文档示例统一21候选，稳定短入口仍20。真实链路与安装各自运行结果独立核对。

最后独审delta补了Back取消保留输入、同页维护状态查询、活动任务禁止重复提交、200截断应用沿原事务键、分享从列表点击起冻结内容条件；其定向无浏览器回归通过。后续准确提交及最终资格以PR133最新Checks为准；任何queued/running/skip/失败都不算完成。历史失败不覆盖或改写。不包含受保护分支合并、公开Release、短入口切换或VPS升级。

安装72d66e3的公开14→21验收在完整私有DB/证书字节快照相同后，被公开响应新增`content_revision`的严格键集合拒绝（42344583764）。修正仅限候选响应契约：严格新增64位CAS字段，原能力布尔、实例、名称、路径、绑定、凭据标记及全部旧键仍逐项精确相同；增加畸形token/未知字段/旧字段变化拒绝回归。20加入不可变公开基线、既有维护receipt闭包及已有Xray证书fixture枚举；不跳过历史14/15/16/17/19升级。11项纯升级fixture边界通过，真实重新验收继续Actions；三任务链路72d66e3已实际执行成功，并关闭Node管道的测试句柄警告。

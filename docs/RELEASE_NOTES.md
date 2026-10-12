# V-UI 0.4.7

0.4.7 是累计版本：中文网页后台、固定官方核心、图形化证书、严格订阅导出和受管终端维护。实际公开状态、目标提交与下载资产见[官方 Releases](https://github.com/ForceMind/V-UI/releases)；本文件不以版本号预先证明发布成功。下方文档链接固定到v0.4.7 tag，只有该版本正式创建后才可用，不随master滚动。

## 本版内容

- 管理员认证、会话撤销、专用订阅令牌、节点/格式授权、有效期、轮换及撤销；无默认密码
- sing-box 公开基线：VLESS TCP/WS/gRPC TLS、VLESS direct TCP REALITY/Vision、Trojan TCP/TLS、VMess TCP/TLS、三种Shadowsocks AEAD、Hysteria2、TUIC v5，严格范围见[兼容矩阵](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/COMPATIBILITY.md)
- ToClash两种模式、40项预设、规则预览和完整Mihomo YAML；URI/Base64与sing-box连接JSON不承载同等完整规则
- Certbot HTTP-01单域名、测试/正式隔离、自动续期、面板热更新和TLS节点绑定；失败保留旧材料，不延长旧证书有效期
- 非root包内菜单：状态、安装/活动版本、显式完整校验、可信本地候选准备
- 可选全局受管菜单：固定root-owned入口与降权worker、systemd启停/重启、停机备份、恢复、激活/代码回退及中断恢复；需要显式安装完整可信kit
- 流式完整校验、私有缓存生命周期、GNU allocator和状态轮询等低资源软件修复，保留完整路径/成员/摘要/权限与恢复检查
- VMess TLS负向及HTTPUpgrade记录器的有界证据/清理回归；真实原因、零送达和无DIRECT断言不降级
- 面向用户重整README、安装、菜单和发布文档；历史失败、未验场景与旧记录保留
- 已确认的蓝青 V 标识接入 README、登录/后台导航与 favicon；本地轻量 SVG，无新增字体或运行依赖

## 下载与安装

发布资产共19项：四个CPU/libc目标ZIP，固定安装/控制helper、源码ZIP、RELEASE.json、RELEASE_NOTES.md和SHA256SUMS。具体清单见[发布流程](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/RELEASING.md#发布资产)。选定完整同版本资产，核来源与摘要；不使用latest，不混用旧包。

推荐从[README固定版本安装](https://github.com/ForceMind/V-UI/blob/v0.4.7/README.md#快速安装)开始。系统安装使用管理员权限，应用/核心/证书任务使用专用服务用户；默认TCP80、8443和10443会预检，节点UDP需独立声明。安装器不接管原站点，不修改SSH，不自动启用防火墙。

默认安装**不提供全局v-ui命令**。包内菜单使用服务用户调用；全局入口须按[独立可信kit安装合同](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/MANAGED_OPERATIONS.md#安装消费点与信任)显式安装。不要以root运行服务用户可写的portable Python或payload脚本。

## 升级和数据

1. 保留受限访问的离机备份，并核对当前target、活动release ID及启动锚点。
2. 使用同版本完整可信安装器/包按[受管升级](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/OPERATIONS.md#受管升级)操作；CPU/libc/init迁移不属于普通升级。
3. 菜单stage只准备，不切CURRENT；activate先备份再选择已准备版本。单独切换不会更新root-owned控制层、launcher或服务模板。
4. 备份含未加密秘密。恢复撤销旧会话/订阅授权；失败或RESTORE_PENDING时保持停机，按recover-restore处理，不删journal或空建数据库。

代码回退不恢复旧数据。保留旧bootstrap runtime；不要用删除目录掩盖升级问题。不同版本/schema迁移与真实断电恢复没有被同代码合成A→B→A测试代替。

## 已知限制

- XHTTP/HTTPUpgrade公开导出仍阻断；真实能力刻画不代表完整产品支持。Xray组合不自动继承sing-box公开矩阵。
- 除三种Shadowsocks AEAD已有UDP基线，其他协议的通用应用UDP/DNS扩围未完成。HY2/TUIC节点QUIC需要UDP端口，但本版只验HTTP/TCP应用负载。
- OpenRC支持既有安装、状态查询与纯候选准备；全局菜单依赖停启的写动作拒绝。受管systemd写事务需要可验证的cgroup v2与固定服务来源。
- DNS-01/通配符、核心自动升级、任意YAML导入合并、容器/NixOS等不在本版范围；后台不提供网站托管、SSH或主机封禁能力。
- 真实1vCPU/512MiB整机及24小时完整负载未获资格。大数据归档场景达到512MiB峰值/零余量；160/320MiB仍是未证工程预算，不能当最低配置保证。
- 固定gRPC Lite错误CA/SNI可能让调用者超时；TLS拒绝由真实诊断确认，不能关闭验证规避。

## 验收与来源

产品代码基线、主线与候选来源、普通CI、真实root/systemd、包消费和公开发布分别记录。准确最终提交必须有自己的八组CI及同提交四目标资产，发布不重新构建另一套包。真实受管root资格仍仅属于[f509历史run](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/MANAGED_OPERATIONS_CLOSURE_20261012.md)，没有移植为每个后续head的root重验。

[主线产品收口](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/MAINLINE_OPERATIONS_CLOSURE_20261012.md) · [PR #36准确文档主线验收](https://github.com/ForceMind/V-UI/pull/36) · [低资源证据](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/LOW_RESOURCE_CLOSURE_20261011.md) · [原发布准备历史](https://github.com/ForceMind/V-UI/blob/v0.4.7/docs/RELEASE_PREPARATION_HISTORY_20261012.md)

V-UI使用[MIT License](https://github.com/ForceMind/V-UI/blob/v0.4.7/LICENSE)。第三方核心、Certbot、ToClash及依赖遵循各自许可；来源与许可见[NOTICE](https://github.com/ForceMind/V-UI/blob/v0.4.7/third_party/NOTICE.md)及包内provenance/license文件。

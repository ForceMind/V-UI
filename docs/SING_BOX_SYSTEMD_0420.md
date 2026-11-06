# v0.4.20：受管 systemd 服务中的 sing-box 启动

2026-11-06，用户在明确的最小20修复、独审、Actions、正常dev→main、公开/短入口与备份上机方案后，清除旧Goal并人工回复“继续”。本轮按该方案交付；自动Goal续行不作为新的授权。分支release/v0.4.20，基线main2ba461c/dev4505029；产品修复冻结31e6ee921082a8fee98ba368942dadf712d751d3。原目录WIP和本地未跟踪文件保留。

## 已确认的缺陷与边界

原panel服务允许AF_INET/AF_INET6/AF_UNIX，固定官方sing-box1.14.2会在启动网络监控时创建AF_NETLINK socket。配置check可通过，实际run却因地址族不支持退出；Xray仍能运行。用户授权实例的实际systemd属性与该限制一致。另一个已确认的8001入站规则缺失由用户先备份后精确放行，原节点已通过真实客户端认证/TCP转发；防火墙重启持久化尚未核验，本批不自动改规则。

最小代码效果仅panel增加AF_NETLINK；v-ui专用非root用户、NoNewPrivileges、空CapabilityBoundingSet及其他sandbox保持，不增加NET_ADMIN/NET_RAW。HTTP01 service/socket在IPv4/双栈和原OpenRC的生成字节及权限不变。应用、数据库、API scope、180天授权、固定核心和客户端pin不改；没有DNS-01、TUN、云安全组、反代或其他功能扩围。

首次direct节点的“保存后显式检查/应用”事务契约保持；不能激活未知草稿或未选外部上游。失败新增项只按自身实例修订回退，不删原节点/重置凭据或数据库。现场诊断的两次自有新增项均撤回，原Xray、证书、数据和两个开关保留；本轮升级后再用真实授权验证本机作为TCP上游，不臆造另一台中转机。

## 已有准确证据

- 230ddfa诊断首轮[42177775842](https://github.com/ForceMind/V-UI/actions/runs/42177775842)：完整单元通过，但在systemd尚未exec核心前采样到过渡进程，安全断言停止，结论inconclusive；不作为netlink证明，清理完成。
- 0b6e2d8[42179574722](https://github.com/ForceMind/V-UI/actions/runs/42179574722)：修正采样后，旧三族check通过、run明确报create netlink socket/address family not supported；仅增加AF_NETLINK后，同一假TLS/端口/路由、非root/零能力集拥有真实监听。该隔离实验不是VPS测试。
- 最小修复31e6ee9已独立gpt-6.1-sol/high只读审查；[42183162060](https://github.com/ForceMind/V-UI/actions/runs/42183162060)完整2638项单元（232环境跳过）、固定真实核心及修后canonical/旧限制对照成功，清理完成。本机仅4项纯服务测试、7项纯探针自检和AST/diff；无产品构建/真实安装。

## 本轮交付与必要验收

1. 只补20版本、严格专用netlink_service_no_browser门禁和直接说明。原19及其他例外不修改；仅12个明列浏览器步骤可skip，其他步骤全部成功，browser未验证。
2. 在明确为空的Actions主机完成真实受管v-ui.service内的SingBox/Xray共存、TLS/认证/HTTP转发、普通分享及TCP-only上游导出，保留原升级/修复/数据断言；仅在全部原断言后清理已识别的CI自有合成夹具。
3. 保留公开14/15/16/17升级，新增不可变公开19源a2b9c188658f082b189e77d237787a96eb9cf921到20候选升级，核受管unit及签收刷新、账户/节点/凭据/证书/会话/开关保留。
4. 证书测试不伪造公信链：空CI的provided临时材料通过管理员API使用；对应客户端显式信任临时CA，不改系统信任或生产验证。维护bearer成功绑定真实公信certificate_id留到已授权VPS升级后补验，不能把假production记录的保真断言称作托管签发/绑定成功。
5. 准确候选、dev及main各自按同SHA每workflow最新run/attempt，核全部job/非browser步骤、固定核心/模板/完整loopback和四目标；不要求额外历史push事件都成功，也不把未结算旧run算通过。
6. 人工晋升准确默认main已受验同源套件，不重新构建，不覆盖旧tag/Release/资产。匿名核tag/latest、19项资产与真实installer/完整清单摘要及旧19不变后，独立切短入口并核自身候选/dev/main及实际main URL。
7. 通过现有维护授权发起官方稳定升级，自带私有备份和失败恢复；升级短暂重启服务。再核原8001真实连接、数据/证书/权限/180天会话保持，并创建绑定已有真实证书的SingBox10443、实际连接/分享/上游导出；不修改SSH/防火墙或全局中转。

## 当前进度

- [x] 最小生产模板修复及隔离对照已实现、独审和单套准确Actions通过。
- [ ] 20版本化、严格发布门禁与实装/公开19升级回归、最终完整hash及独审。
- [ ] 准确候选/dev/main完整资格与四目标。
- [ ] 公开20及匿名资产读回、旧资产保留。
- [ ] 短入口独立合并及真实main URL验收。
- [ ] 授权VPS备份升级与本机上游功能实测。

构建和真实安装全部GitHub Actions，不本机下载产品ZIP/编译核心，不浏览器。根因/失败/复验与最终准确SHA/run/job/step在承载PR和Release集中保留，避免回写自身SHA循环提交；源码本页是提交时快照，不提前写通过或已发布。不得将真实维护链接、会话、用户名密码、节点UUID/私钥或生产配置写入源码、CI inputs或公开日志。

# sing-box VLESS HTTPUpgrade 的 Mihomo YAML 导出

v0.4.28 支持在官方 sing-box 1.14.2 服务端和 Mihomo 1.19.32 客户端之间使用此 YAML 导出转发 HTTP/TCP。使用节点编辑器选择 sing-box、VLESS、HTTP Upgrade 和 TLS，填写单节点的 SNI、Host、Path 以及现有证书和私钥路径。启用证书校验，使用 Chrome 指纹和空 flow。保存后在订阅入口选择节点和 Mihomo YAML。服务端显式使用 HTTP/1.1，客户端输出 `network: ws` 和 `ws-opts.v2ray-http-upgrade: true`，并设置 `udp: false`。

节点 URI、raw 订阅、sing-box JSON 和中转上游拒绝该传输。包含该节点的多格式申请整体拒绝。已有授权在下载时重新核对节点，YAML 之外的格式继续服从上述限制。普通节点摘要不返回证书路径、私钥路径或用户凭据。无法在编辑器中保真的现有高级选项会阻止编辑。

隔离 Actions 使用实际节点 API、授权下载、适配器配置和固定核心。正向试验证明代理送达完整 HTTP 正文。分别改变 UUID、CA、SNI、Host、Path 和 Upgrade 标志，检查新增核心拒绝原因、应用目标零送达且无 DIRECT。原样 URI 和普通 TCP 对照由[历史刻画](XHTTP_CHARACTERIZATION_048.md#httpupgrade-独立缺口)的既有测试覆盖；[D2 #167](https://github.com/ForceMind/V-UI/pull/167) 保留原报告。

## 候选验收记录

原草稿准确提交 `e6da6cf590f25940cc114f81af0e8cb44b04ff45` 的真实链路已通过。隔离安装第二轮的 v0.4.21、v0.4.25 在 GitHub 匿名下载阶段收到 HTTP 403，两项未进入升级。保留原日志及失败记录。 [原真实链路](https://github.com/ForceMind/V-UI/actions/runs/44788969199) · [原安装矩阵](https://github.com/ForceMind/V-UI/actions/runs/44788969959/attempts/2)

草稿同步 v0.4.27 的 `96452a3b6c742940e6a3b013c2f9cbecbd3427e0` 基线，沿用只读 CI 元数据与匿名制品下载。组合提交需重新验证真实转发、失败关闭、格式拒绝、四个目标和同轮一键安装。矩阵包括一次构建、一次新装及 12 个公开旧版升级，其中包含 v0.4.21 和 v0.4.25。公开短入口独立检验当前稳定版。准确结果见 [草稿 #179](https://github.com/ForceMind/V-UI/pull/179)。浏览器未验证。

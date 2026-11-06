# V-UI 0.4.20

修复受管systemd服务限制导致sing-box无法启动：仅panel服务允许AF_NETLINK，供固定核心网络接口监控使用，保持v-ui专用非root用户、NoNewPrivileges、空CapabilityBoundingSet及其他安全限制。HTTP01/OpenRC配置不变，不增加NET_ADMIN或NET_RAW能力，不修改SSH或防火墙。

保留现有账户、节点、UUID/密码、证书、180天维护会话及代理/中转开关；不切换原Xray节点或端口。安装回归补充受管服务下的SingBox/Xray共存、TLS/认证/HTTP转发和不可变公开19→候选升级，旧升级和数据恢复保护保持。首次显式直连的检查/应用事务契约不变，不激活未知草稿或外部上游。

当前为发布准备，20尚未公开。构建、四目标与真实安装只在GitHub Actions，浏览器未验证。准备、准确合并、公开、短入口及生产升级分别在[本批记录](SING_BOX_SYSTEMD_0420.md)及承载PR/Release保存，不借旧候选绿灯、不覆盖旧资产。

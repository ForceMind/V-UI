# Xray VLESS XHTTP 分享

在入站编辑器选择 Xray、VLESS、XHTTP、TLS 和 Stream One。填写可验证的证书与私钥路径、DNS SNI 和 Host。Path 以 `/` 开头和结束，最多 256 个 ASCII 字符，允许字母、数字、`/`、`.`、`_`、`~` 和 `-`。拒绝重复斜杠、点路径段、查询、片段和百分号转义。启用 h2 以分享给 Mihomo，保持 Chrome、证书校验、空 flow 及单一 UUID。已有高级 XHTTP/TLS 字段若无法由此编辑器保真，编辑会被拒绝。

保存后可通过节点分享获取 VLESS URI，也可创建仅授权所选节点的 Mihomo YAML 或 URI 订阅。资格限定固定官方 Xray 26.3.27 → Mihomo 1.19.32 的 HTTP/TCP。原生 Mihomo YAML 显式设置 `udp: false`，固定 URI 导入器会设置 UDP 能力，因此 URI 本身没有禁用 UDP。XHTTP 节点不具备 sing-box JSON 格式资格，包含此节点的授权在选择该格式时整单拒绝。已有授权遇到同一节点改为 XHTTP 时，该 JSON 输出拒绝，合格的其他格式仍按授权提供。证书和密钥路径只供服务端使用。

托管证书、REALITY、其他 XHTTP mode、反代/CDN、应用 UDP 和中转上游不属于本版分享范围。HTTPUpgrade 的 [刻画结论](XHTTP_CHARACTERIZATION_048.md#httpupgrade-独立缺口)保留。UDP 双跳另见 [D2 草稿 #167](https://github.com/ForceMind/V-UI/pull/167)。

## 验收

在准确候选提交的 **Real loopback proxy and DNS chain** 中运行 `tests/test_xhttp_public_loopback.py`。测试从管理员节点 API 保存配置，通过真实 Xray 适配器生成服务端配置，再以无管理员会话的授权链接下载 Mihomo YAML 与 URI。Mihomo 导入两种制品，测试监听与路由仅指向该代理，应用目标先证明可达。两种导入均须取得完整应用正文。逐项改变 UUID、CA、SNI、Path、Host、mode 时均须取得新的核心拒绝日志、零应用送达且无 DIRECT。独立 TLS 探测证明服务端 h2。历史 HTTPUpgrade 刻画仍由原套件执行。

# 常见故障

| 现象 | 检查与处理 |
| --- | --- |
| 安装提示平台不支持 | 检查 CPU/libc/init 能力是否属于四目标 Linux 与 systemd/OpenRC 范围；不要改检测条件强行安装，见[兼容矩阵](COMPATIBILITY.md) |
| Port80 already in use | 查看原网站/容器占用；安装器不会自动停止它。当前不自动接管已有反代 |
| 包摘要不一致 | 停止，不执行。重新从同一可信提交取得套件和摘要；不能改预期值让测试通过 |
| reserved paths / foreign service | 已有目录、账号或unit不属于该安装器；备份并人工核对，不自动覆盖 |
| CA验证失败 | 检查A/AAAA、IPv6、TCP80、CAA、系统时间、socket与CA诊断日志；先测试签发 |
| 面板证书错误 | 确认访问域名与证书SNI相同、时间有效、完整证书链正确；不得常态使用跳过校验 |
| 401 | 管理会话过期/撤销，重新登录；订阅token不能用于管理API |
| 403 | Origin、Host或X-VUI-Request不满足保护条件；使用固定公开来源，不任意改代理转发头 |
| 404订阅 | 令牌无效、到期、撤销、格式不在授权范围，或管理员密码已变更 |
| 409导出 | 节点组合不在验证矩阵、被禁用/删除或配置未准备好；不是无条件忽略的告警 |
| VLESS/WS 导出拒绝 | 核对严格 path、可选 Host、单 UUID、空 flow、TLS/SNI、ALPN；不允许 early data、额外 header 或未知字段 |
| VLESS/WS 错误 path 无法连接 | 客户端 path 必须匹配服务端；不能通过 query/百分号转义或关闭 TLS 校验修复 |
| 更换合法 WS Host 仍可连接 | sing-box 不强制校验请求 Host，它只是客户端路由信息；这不表示 TLS SNI/CA 验证失效，勿把 Host 当作白名单 |
| VLESS/gRPC 导出或编辑拒绝 | 检查单 UUID、空 flow、TLS/SNI、字面 `[A-Za-z0-9._-]{1,128}` service name、ALPN 省略或仅 h2；已有未知字段不得靠编辑静默丢弃 |
| VLESS/gRPC service name 不匹配 | 客户端必须逐字匹配，区分大小写；不要加 `/`、`/Tun`、query 或百分号转义。`.` / `..` 是合法字面 service，不能按路径段归一化 |
| VLESS/gRPC 错误 CA/SNI 只显示超时 | 固定 sing-box gRPC Lite 可能未及时返回 x509 原因；核对 CA、明确 SNI、实际日志。超时本身不是正确 TLS 拒绝证据，不关闭验证；[测试诊断记录](VLESS_GRPC_044.md)使用局部 HTTP/2 日志取得证据 |
| gRPC authority/Host、headers、timer 或多模式不可保存 | gRPC 基线未验收这些参数，固定核心不实施 authority/Host 白名单；不替换核心、增加反代或悄悄忽略字段以伪造支持 |
| Hysteria2 没有连通但 TCP 已开放 | QUIC 使用节点 UDP；核对主机/云安全组的准确 UDP 端口，TCP 同号放行不能替代。新安装可显式 `--node-udp-port`，升级须重传并人工核对所有权 |
| Hysteria2 导出拒绝 | 仅单密码、明确验证 SNI、原生 QUIC 默认值；obfs、hopping、带宽、ALPN/uTLS 覆盖、多用户或未知字段不在严格公开范围，不靠丢字段或关闭 TLS 修复 |
| Hysteria2 编辑返回 422 | 检查已有 users/password 和 TLS 是否有效、字段是否能由表单表达；密码须 1–256 字面字符，无全空白、Unicode 控制字符或无效 UTF-8，不能借空值生成掩盖坏导入 |
| Hysteria2 空密码或高级草稿 | 密码输入空字符串：新建生成、编辑保留，响应不回传秘密。新默认无带宽/Chrome；已有高级草稿可以保留，不表示能够公开导出 |
| Hysteria2 超时或错误密码/CA/SNI | 核对真实认证/x509 错误；Mihomo 错误密码可在 3 秒请求期限后超时同时记录 authentication failed，不保证立即结构化报错。不能把超时或零目标请求单独当作拒绝原因，保持验证开启。前置失败及有界观察修复见[阶段证据](HYSTERIA2_045.md) |
| Hysteria2 应用 UDP 不通 | 当前只验 HTTP/TCP 负载，Mihomo udp:false、sing-box network:tcp；QUIC 自身 UDP socket 不等于应用 UDP 支持 |
| UDP 安装预检报端口占用 | 新安装分别探测 IPv4/可用 IPv6 UDP，与 TCP 独立；核对端口属主，不停止无关进程。既有安装跳过 bind 预检，必须人工核对 |
| 409分流保存 | 另一页面已更新修订；重新载入，手动合并草稿，不覆盖他人新版本 |
| 428分流保存 | 缺少If-Match；先读取snapshot/ETag，使用当前UI |
| 503规则 | 已保存文件不可读/损坏；从可信备份恢复，系统不会悄悄回到默认规则 |
| certificate有效但绑定有错误 | 签发与应用分开；查看核心状态，处理停止/校验错误后重试应用 |
| 核心手动停止后证书未生效 | 自动续期不会擅自启动核心；主动启动再重试应用 |
| 429登录 | 命中登录限流；按Retry-After等待，不信任伪造X-Forwarded-For来绕过 |
| 网站托管/防火墙接口不可用 | 本版故意隔离网站，主机防火墙由管理员外部管理，不假报操作成功 |

报告问题时提供：精确提交或release_id、系统版本、相关状态码、已脱敏的错误代码、是否使用受管入口。不要提供完整订阅URL、私钥、管理员密码或整个数据目录。

# V-UI

**个人自用的轻量代理面板：管理节点、图形化申请证书、设置 ToClash 分流，直接订阅完整 Mihomo 配置。**

版本目标：**v0.4.4**（VLESS/gRPC/TLS 候选；独立审查和最终 exact-head CI 待完成）。正式发布前须完成 [发布检查](docs/RELEASING.md) 中的全部 exact-head 验收和人工发布动作；版本号不代表 GitHub Release 已公开。

## 能做什么

V-UI 使用 FastAPI + SQLite，不依赖 Redis、常驻 Node 或在线订阅转换服务。运行包包含固定版本的核心、Python wheels 和本地前端资源；不在安装时临时解析 `latest`。

| 功能 | 内容 |
| --- | --- |
| 管理面板 | 真实管理员账号、登录/退出/改密、会话失效与接口鉴权，无默认密码 |
| 节点管理 | Xray/sing-box 核心独立控制，候选配置校验、失败保护、状态区分与重启恢复 |
| 图形化证书 | HTTP-01 自动签发、测试/正式环境、到期状态、自动续期、面板热更新、TLS节点绑定 |
| ToClash 分流 | 两种模式、40项服务预设、直连/代理、自定义内网DNS、CGNAT、规则预览 |
| 客户端订阅 | 节点/格式作用域、一次显示的专用令牌、到期、轮换、撤销；完整Mihomo配置直接导出 |
| 安装运维 | 一个入口完成受管服务设置，开机自启、权限隔离、停机备份、校验恢复、候选切换 |

**已验收的 v0.4.2 主线包含 sing-box + VLESS/TCP/TLS、Trojan/TCP/TLS、Shadowsocks AEAD 与 VMess/TCP/TLS。** 2026-10-05 的准确主线提交 `0225ce4` 已完成 16 个正常合并 PR、八组工作流共 11 个 job 及四目标 Linux 附件核验；当时未创建 Draft Release、版本 tag、公开 Release 或实际部署。详见[主线收口记录](docs/MAINLINE_CLOSURE_20261005.md)。

**v0.4.3 VLESS/WebSocket/TLS 已验收为继承基线。** [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常合并至 `1b3ec40cd3bb640246d12afa104db0aec08ce336`，准确主线八组工作流、11 个 job 和每一步均成功。可选 WebSocket Host 是客户端路由信息，不是服务端访问白名单，也不代替 TLS SNI/证书验证；详见[WS 收口记录](docs/VLESS_WS_CLOSURE_043.md)。合并与 CI 不表示发布或附件晋升。

**v0.4.4 仅新增 sing-box 1.14.2 / VLESS / gRPC / TLS 候选**，客户端保持 Mihomo 1.19.32 与 sing-box 1.14.2。现有官方 sing-box 构建未含 `with_grpc`，实际使用 gRPC Lite；不替换或重编译核心。`service_name` 是 1–128 字符的 ASCII 字母、数字、点、下划线或连字符字面值，区分大小写；单 UUID、空 flow、明确 SNI、正常证书校验，HTTP/2、ALPN 省略或仅 `h2`，Chrome fingerprint 独立可选。只新增 HTTP/TCP，不声明 gRPC UDP。

固定二进制[前置链路](docs/VLESS_GRPC_044.md)在 `a0205fe545fabd958fe7aa80835a3ec6abeada92` 通过 45 项测试，但不能代替集成导出、浏览器、证书及最终候选/主线八组验收。gRPC Lite 错误 CA/SNI 可能向调用者表现为超时，不能承诺及时返回 TLS 错误；测试子进程的 HTTP/2 诊断确认了真实 x509 拒绝和目标无请求。未知字段和不支持组合明确拒绝，不静默丢参数或退成全直连。参见[参数说明](docs/CONFIGURATION.md#044-vlessgrpctls-候选)和[兼容矩阵](docs/COMPATIBILITY.md)。

## 快速安装

安装器不再按 Ubuntu 白名单判断环境，而是检测 CPU、glibc/musl、systemd/OpenRC、包管理器、防火墙和端口。目标运行包覆盖 x86_64/ARM64 × glibc/musl；运行服务使用固定便携 Python 3.12。

将**同一验收提交**的安装套件解压后，在套件目录执行：

```sh
sha256sum -c SHA256SUMS
# 示例：x86_64 + glibc；其他机器使用对应 target 包
sudo bash install.sh --bundle ./vui-linux-x86_64-gnu.zip \
  --sha256 "$(awk '$2=="vui-linux-x86_64-gnu.zip" {print $1}' SHA256SUMS)"
```

脚本会先检查 80、面板端口和默认节点端口。本机 UFW/firewalld 缺规则时只有在你明确输入 `yes` 后才会开放；自定义 nftables/iptables 与云安全组只提示并等待人工确认。随后才创建低权限账号、HTTP-01 验证服务和 HTTPS 面板。

发布后可指定明确版本通过同一入口下载官方Release资产；**正式Release尚未生成时不要把下面命令当作当前可用下载地址**：

```sh
sudo bash install.sh --version v0.4.4
```

安装器本身也必须来自可信仓库/套件，不能只信任来源不明压缩包附带的摘要。详见 [安装指南](docs/INSTALLATION.md)。

## 日常使用

HTTPS登录 → 节点管理 → 新建已验证TLS节点，选择托管证书或填写已有证书路径 → 分流与订阅 → 保存规则 → 选择节点与格式并创建专用订阅 → 客户端导入并刷新。

修改草稿不会影响客户端。保存成功后客户端刷新原URL即可取得新配置，无需重新创建令牌，也无需再次打开ToClash转换。URI/Base64只承载节点；sing-box连接JSON不包含完整ToClash分流，不能与Mihomo完整YAML混称。

证书管理可从导航进入：申请、测试签发、查看到期与失败原因、暂停自动续期、绑定面板或TLS节点。测试证书不能用于上线；续期失败保留旧材料但不会延长旧证书有效期。服务停止期间续期检查暂停，恢复服务后继续。

## 文档

[完整文档入口](docs/README.md) · [安装](docs/INSTALLATION.md) · [证书](docs/CERTIFICATES.md) · [分流/订阅](docs/CONFIGURATION.md) · [维护/备份/恢复](docs/OPERATIONS.md) · [常见故障](docs/TROUBLESHOOTING.md) · [API](docs/API.md) · [兼容范围](docs/COMPATIBILITY.md) · [安全](SECURITY.md) · [开发](CONTRIBUTING.md) · [正式发布](docs/RELEASING.md) · [变更日志](CHANGELOG.md)

V-UI自身沿用原README声明的MIT许可；打包的核心、Certbot、其他依赖和ToClash仍受各自许可约束，不因项目MIT许可而变更。来源和许可见 [third_party/NOTICE.md](third_party/NOTICE.md) 及构建包中的provenance/license文件。

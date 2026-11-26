# 3X-UI 中文精简版

基于 [3X-UI v3.11.0](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0)。管理员面板、安装入口和主要运维菜单使用简体中文。安装包面向使用 systemd 的 Linux x86_64 主机。

## 一条命令安装

在线入口读取本分支的 `packages/stable.txt`，从清单指定的提交下载完整的同版本安装包并校验 SHA-256。清单随获准上传的安装包提供。入口使用服务器上的 Bash 和 curl。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/3x-ui-zh-lite/install-online.sh)
```

安装时可输入用于自动签发证书的域名或 IPv4，也可选择 PostgreSQL 和 Fail2ban。命令后跟 `--ssl example.org`、`--postgres`、`--fail2ban` 或 `--port 2053` 可以直接选定。HTTP 验证需要服务器的 80 端口可达，域名需正确解析到本机。Cloudflare DNS 证书在安装后的 `x-ui` 菜单操作。

出于访问安全，面板默认监听 `127.0.0.1:2053`，由非 root 账号运行。安装器显示随机账号、密码及路径。未申请证书时可以用 SSH 转发在本机访问。通过运维菜单设置域名、证书和面板监听方式，或使用已有 HTTPS 反代。

```bash
ssh -N -L 2053:127.0.0.1:2053 用户名@服务器地址
```

## 运维功能

运行 `sudo x-ui` 打开数字菜单。也可直接运行 `sudo x-ui 证书`、`sudo x-ui 测速`、`sudo x-ui BBR`、`sudo x-ui 防火墙`、`sudo x-ui Fail2ban` 或 `sudo x-ui PostgreSQL`。

| 功能 | 用途 |
| --- | --- |
| 自动证书 | 域名和 IPv4 证书签发、续期、现有证书绑定、Cloudflare DNS 验证。自动安装的证书保存在 `/etc/x-ui-managed-certs`，私钥限 root 与面板服务组读取。 |
| 测速、BBR、防火墙 | 运行 Ookla Speedtest、调整 BBR、管理指定防火墙端口。由管理员在菜单中主动选择。 |
| Fail2ban | 安装及管理节点 IP 限制，在菜单查看封禁记录。 |
| PostgreSQL | 安装服务器并创建面板专用数据库，保留 SQLite 默认选项和已有迁移入口。 |
| 备份 | `sudo x-ui 备份 /root/x-ui-backup.tar.gz` 保存本地数据目录。PostgreSQL 数据通过面板数据库导出，或使用对应的 `pg_dump`。 |
| 更新 | `sudo x-ui 更新` 通过本分支清单更新。传入解包目录可离线更新，保留数据库和现有设置。 |
| 节点批量升级 | 主面板向选中的启用且在线节点发起面板软件更新，记录逐台受理或失败结果。节点自行选择发行渠道，官方节点使用官方渠道，中文版节点使用中文包。受理后的实际版本可在节点刷新后核对。 |

自动签发失败时可继续用 SSH 登录，再从证书菜单重试。系统命令、ACME、Xray 等原始诊断由对应组件输出，部分上游细节提示仍保留原文。

## 本次精简

- 移除了登录页和概览页的赞助广告、赞助页面、赞助接口，以及侧栏捐赠按钮。
- 管理员面板使用简体中文，客户端订阅页保留语言切换。
- 测速、BBR、防火墙、证书、Fail2ban、PostgreSQL、节点批量升级以及地区规则均保留。其他功能的精简需要逐项确认。

## 离线安装或更新

传输 `3x-ui-v3.11.0-zh.3-linux-amd64.tar.gz` 及其 `.sha256` 一起到目标服务器。

```bash
sha256sum -c 3x-ui-v3.11.0-zh.3-linux-amd64.tar.gz.sha256
tar -xzf 3x-ui-v3.11.0-zh.3-linux-amd64.tar.gz
cd 3x-ui-zh-lite
sudo bash install.sh
```

已有本分支安装时运行 `sudo bash update.sh`。全新安装会检查旧面板和保留数据。源码安装包构建入口为 `bash 制作安装包.sh`，使用 Go 1.27.3、Node.js 26、npm 11、GCC 和 Linux x86_64。`XUI_UPSTREAM_ARCHIVE` 可以指定已校验的官方归档缓存。

## 接手与项目记录

上游固定提交 `c621ba9014a6aa21fd7c9ecf45655e44e24ebf13`，已逐文件核对的 Linux 源码基线为 `a0b224e`。首版 `53aad64` 曾裁剪上述运维和节点升级入口，本版将其恢复。中文包构建以源码提交和内外两层 SHA-256 校验为准。未在真实服务器执行安装、公开签发、续期、BBR 或防火墙操作。

恢复后的 Windows 定向 Go 检查覆盖真实路由注册、节点升级并发分发、面板状态和升级请求互斥。前端定向检查包含节点列表、升级入口和弹窗。下一步是取得公开包上传确认，回读远端分支并验证一键入口的清单和字节。目标服务器上的安装启动和续期另行实测。

`zh.3` 修正证书申请前的端口占用检查、绑定结果回读及 HTTPS 就绪检查。全新安装选用证书时，HTTPS 验证失败会保留证书文件并验证已恢复仅本机的 HTTP。独立签发遇到现有监听时保留现有服务，可从证书菜单用 DNS 验证。本地模拟检查覆盖端口占用与空闲、HTTPS 探测参数、证书私钥不匹配、部分写入及重置回读。

开放源码及归属沿用 [3X-UI](https://github.com/MHSanaei/3x-ui) 的 [GNU GPL v3](LICENSE)。随包资源的来源和许可见[第三方许可](licenses/NOTICE.md)。

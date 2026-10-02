# 安装与首次运行

## 1. 支持环境与准备

一键安装器支持Ubuntu24.04 amd64、系统CPython3.12和已启动的systemd。其他Linux版本、ARM64、Docker普通容器、Windows/macOS服务器不属于此入口的验收矩阵；遇到不支持环境会停止，而不是尝试猜测命令。

需要一个你控制的DNS域名、联系邮箱、可用的TCP80、面板端口（默认8443）。域名A/AAAA应到达该服务器；存在AAAA就需要相应IPv6访问正常。节点建议用10443等非特权端口。云安全组和主机防火墙由操作者配置，安装器不改变SSH和网络规则。

如果80端口被Nginx、Apache或其他服务占用，安装器会报错退出，不会把旧网站停掉。该版本不自动合并已有Web服务配置，也不提供DNS-01或通配符签发；可另用已有合法证书，但仍需为后续图形化HTTP-01功能保留验证服务。

## 2. 使用本地验收套件

取得同一提交的完整套件：`install.sh`、`install_system.py`、`vui-linux-amd64.zip`、`SHA256SUMS`、`RELEASE.json`、源码归档及发布说明。先核对来源与摘要：

```sh
sha256sum -c SHA256SUMS
SHA="$(awk '$2=="vui-linux-amd64.zip" {print $1}' SHA256SUMS)"
sudo bash install.sh --bundle ./vui-linux-amd64.zip --sha256 "$SHA"
```

安装器在控制终端询问域名、邮箱及CA协议同意；管理员密码在之后以不回显的方式输入两次，没有默认密码，不接受密码命令参数。

也可以显式给出非秘密参数：

```sh
sudo bash install.sh --bundle ./vui-linux-amd64.zip --sha256 "$SHA" \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --port 8443 --accept-terms
```

`--accept-terms`表示你已阅读并同意Let’s Encrypt条款、控制所填域名并接受公开证书记录，不能不加理解地给别人代填域名。省略该参数且非秘密参数已全部填完时，脚本会拒绝申请。

`--dry-run`可检查参数、完整包、主机和冲突，不创建账号或服务；它仍需要能读取包及主机状态。缺少Python3.12/venv时，正式执行阶段会通过Ubuntu软件源安装所需系统包，不以root运行pip安装应用依赖。

## 3. 安装器实际做什么

先核对整包SHA-256、文件清单、路径和哈希，然后创建独立系统用户`v-ui`。在该用户身份下离线暂存包、安装精确且哈希锁定的Python依赖并执行启动健康检查。

数据放在`/var/lib/v-ui/data`，版本放在`/var/lib/v-ui/releases/<id>`。根目录属`v-ui`、权限0700；root控制配置`/etc/v-ui/service.json`、启动器`/usr/local/lib/v-ui/launcher.py`以及systemd单元。

| systemd单元 | 用途 |
| --- | --- |
| `v-ui.service` | 非root HTTPS面板、核心管理及证书任务 |
| `v-ui-http01.socket` | 由systemd打开公网80的监听socket |
| `v-ui-http01.service` | 非root验证响应，只读取固定challenge目录中的合法token |

服务准备后自动申请面板正式证书、设置管理员并执行本机TLS信任链/域名及未登录拒绝检查，成功后输出登录地址。证书申请失败不会改成HTTP面板或关闭证书校验；排查原因后用相同参数重新运行，已存在的受管数据保留。

首次安装成功后正常重跑会拒绝，避免意外覆盖。只有显式`--upgrade`走受控升级流程。现有不属于本安装器的同名账号、保留目录或同名单元均不自动接管。

## 4. 已有证书模式

有自己合法持有的域名证书时，可用`--cert`和`--key`代替首次申请。文件须为普通文件而非软链接，证书须匹配私钥和面板域名：

```sh
sudo bash install.sh --bundle ./vui-linux-amd64.zip --sha256 "$SHA" \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --cert /secure/fullchain.pem --key /secure/privkey.pem
```

私有CA场景可显式给`--health-ca /secure/root-ca.pem`用于安装后的本机TLS健康检查；它只适用于已有证书模式，不改变ACME信任或系统证书库，不等于给所有客户端安装了该根证书。测试CA不应作为公开部署替代方案。

已有证书会复制到私有数据目录，不自动为它产生ACME续期关系。后续可在面板申请正式托管证书并明确绑定到面板，之后由托管流程续期。

## 5. 在线官方Release安装

只有正式Release公开且资产通过全部门槛后才可用：

```sh
sudo bash install.sh --version v0.3.0
```

入口只接受明确`vX.Y.Z`，只从官方仓库该版本获取固定资产，检查后执行；不会隐式下载latest。`install.sh`本身必须先从可信来源获得并审阅，摘要文件不是数字签名。当前若只有PR/Actions候选包，请使用上面的本地套件流程，不把尚不存在的正式下载链接写进生产自动化。

首次使用见[配置指南](CONFIGURATION.md)，更新及恢复见[运维指南](OPERATIONS.md)。

# 安装与首次运行

## 1. Linux 环境检测与准备

安装入口先检测发行版信息、CPU、libc、服务管理器、包管理器、防火墙和端口，不使用 Ubuntu 名称白名单。

当前目标矩阵：

- x86_64 / ARM64；
- glibc / musl（最低版本由便携运行时和当前安全依赖决定，安装开始时直接检查）；
- systemd / OpenRC；
- Debian/Ubuntu、Fedora/RHEL 系、Arch、openSUSE、Alpine、Gentoo 等常规可变系统按能力进入同一流程。

运行服务使用安装包自带的固定 CPython 3.12 和目标环境 wheels。系统 Python 只用于启动安装器；若低于 3.9，入口先尝试包管理器，仍不足时使用固定 SHA-256 的便携 Python 引导。

未经适配的 NixOS、runit/s6、其他 CPU 或过旧 libc 会显示检测结果并停止，不会猜测系统命令。

### 端口与防火墙

默认检查 TCP 80（HTTP-01）、8443（面板）和 10443（默认节点，可用 `--node-port` 改）。

- 端口已有监听：停止，不杀原进程。
- UFW / firewalld：若规则缺失，交互询问是否开放；只有输入 `yes` 才修改。firewalld 同时保存 runtime/permanent。
- 自定义 nftables / iptables：不自动覆盖，列出端口并等待你确认已经处理。
- 云厂商安全组：本机无法可靠修改，同样列出端口并等待确认。

自动化部署只有在操作者已确认外部规则时才应使用 `--assume-external-ports-open`；这不是检测成功的替代品。

80 被 Nginx/Apache/Caddy/其他服务占用时，当前版本不会自动接管已有网站。HTTP-01、DNS 与证书前提见 [CERTIFICATES.md](CERTIFICATES.md)。
## 2. 使用本地验收套件

取得同一提交的完整套件：`install.sh`、`install_system.py`、`vui-linux-<target>.zip`、`SHA256SUMS`、`RELEASE.json`、源码归档及发布说明。先核对来源与摘要：

```sh
sha256sum -c SHA256SUMS
SHA="$(awk '$2=="vui-linux-amd64.zip" {print $1}' SHA256SUMS)"
sudo bash install.sh --bundle ./vui-linux-<target>.zip --sha256 "$SHA"
```

安装器在控制终端询问域名、邮箱及CA协议同意；管理员密码在之后以不回显的方式输入两次，没有默认密码，不接受密码命令参数。

也可以显式给出非秘密参数：

```sh
sudo bash install.sh --bundle ./vui-linux-<target>.zip --sha256 "$SHA" \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --port 8443 --accept-terms
```

`--accept-terms`表示你已阅读并同意Let’s Encrypt条款、控制所填域名并接受公开证书记录，不能不加理解地给别人代填域名。省略该参数且非秘密参数已全部填完时，脚本会拒绝申请。

`--dry-run` 会输出检测到的发行版/CPU/libc/init、防火墙和端口情况，不创建账号、服务或规则。运行版本不依赖系统 Python 3.12。

## 3. 安装器实际做什么

先核对整包SHA-256、文件清单、路径和哈希，然后创建独立系统用户`v-ui`。在该用户身份下离线暂存包、安装精确且哈希锁定的Python依赖并执行启动健康检查。

数据放在`/var/lib/v-ui/data`，版本放在`/var/lib/v-ui/releases/<id>`。根目录属`v-ui`、权限0700；root控制配置`/etc/v-ui/service.json`、启动器`/usr/local/lib/v-ui/launcher.py`以及systemd单元。

| systemd | 用途 |
| --- | --- |
| `v-ui.service` | 非root HTTPS面板、核心管理及证书任务 |
| `v-ui-http01.socket` / `.service` | systemd 持有80 socket，非root responder处理challenge |

OpenRC 使用 `v-ui` / `v-ui-http01`，由 `supervise-daemon` 运行；只有 HTTP-01 responder 获得绑定80所需 capability，面板本身仍无低端口权限。

服务准备后自动申请面板正式证书、设置管理员并执行本机TLS信任链/域名及未登录拒绝检查，成功后输出登录地址。证书申请失败不会改成HTTP面板或关闭证书校验；排查原因后用相同参数重新运行，已存在的受管数据保留。

首次安装成功后正常重跑会拒绝，避免意外覆盖。只有显式`--upgrade`走受控升级流程。现有不属于本安装器的同名账号、保留目录或同名单元均不自动接管。

## 4. 已有证书模式

有自己合法持有的域名证书时，可用`--cert`和`--key`代替首次申请。文件须为普通文件而非软链接，证书须匹配私钥和面板域名：

```sh
sudo bash install.sh --bundle ./vui-linux-<target>.zip --sha256 "$SHA" \
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

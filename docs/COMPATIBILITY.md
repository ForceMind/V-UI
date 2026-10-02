# 已验证范围

| 层级 | 已验证 |
| --- | --- |
| 一键部署 | 发行版无白名单检测；x86_64/ARM64、glibc/musl、systemd/OpenRC；固定便携Python3.12、非root应用用户、HTTPS |
| 底层核心配置 | 固定sing-box1.14.2、Xray26.3.27的配置检查与状态管理 |
| 公开导出与真实链路 | sing-box VLESS/TCP/TLS、单用户、空flow、证书验证；明确SNI，可选已验证ALPN/Chrome指纹 |
| 客户端 | 固定Mihomo1.19.32的配置加载和对应真实链路 |
| 分流 | ToClash0.3.8固定提交的100个语义场景与40项服务目录；实际默认直连模式DNS/路径测试 |
| ACME | 固定Certbot5.8.0，HTTP-01单域名；测试/正式隔离，实际Pebble2.10.1协议与GUI测试 |

常规模式的地理规则语义通过对照，但这不证明所有用户网络可下载远程地理库，或其具体网站分类永远准确。实际公网速度、运营商限制、站点地区限制和所有移动端客户端版本不属于上述通过证据。

## 尚未验收，不能声称已支持

其他协议或组合（HY2、TUIC、REALITY、VMess、Trojan、Shadowsocks、Vision、非TCP传输）的端到端公开导出、UDP实际转发、sing-box完整ToClash分流迁移、任意现有YAML导入、DNS-01/通配符、DNS服务商API、Docker、反向代理自动接管、自动核心版本升级。NixOS、runit/s6 和其他特殊 init/声明式系统需要独立适配。

历史表单和核心能力不等于V-UI已验证矩阵。后续扩展需新增固定二进制与失败路径测试，不靠隐藏字段或删掉不认识的参数“兼容”。

一键安装器遇到未支持平台应明确退出。安装脚本不能验证真实公网域名控制权，最终以CA验证结果为准；测试中的本机Pebble成功不表示已经为用户真实域名申请成功。


## Linux 兼容原则

“支持 Linux 发行版”指安装器依据实际 CPU/libc/init 能力选择运行包和服务后端，而不是维护发行版名字白名单。Debian、Fedora、Arch、openSUSE、Alpine 等有代表性探测；ARM64 和 musl 由原生构建/启动任务验证。最终支持状态必须以 PR #12 的 portable matrix 和真实安装任务同时通过为准。

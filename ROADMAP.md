# V-UI 0.3.0 收尾与发布

当前版本目标为0.3.0。**公开发布状态以GitHub Release为准**，准确验收状态以最终PR的Checks为准；源码版本号和候选包不是已公开Release的证明。

## 阶段

| 阶段 | 目标 | 记录 |
| --- | --- | --- |
| alpha.1–alpha.2 | 启动基线、真实管理员鉴权 | PR #2–#3 |
| alpha.3–alpha.4 | 独立只读订阅、安全核心应用与恢复 | PR #4–#5 |
| alpha.5–alpha.6 | 固定ToClash一致性、分流/订阅完整操作 | PR #6–#7 |
| rc.1–rc.2 | 真实TLS/DNS链路、固定离线包与非rootHTTPS部署 | PR #8–#9 |
| rc.3 | 图形化Certbot/HTTP-01、自动续期与安全绑定 | PR #10 / 2b24e7f，五组CI验收 |
| rc.4 | 一键systemd安装、自启/引导、文档、正式发布门槛 | 当前收尾，最终证据看对应PR |

原始审查和阶段性实现记录保留在docs的历史文件，当前使用指南统一从[文档入口](docs/README.md)阅读。

## 必须交付的使用闭环

可信验收套件 → 一个安装入口 → 非root服务与HTTP-01验证socket → 面板证书申请/管理员设置 → HTTPS面板创建TLS节点并选择托管证书 → 保存ToClash分流 → 专用URL直接导出完整Mihomo配置 → 客户端刷新。

证书页面必须显示持久状态、实际到期、续期与重试；支持测试签发、正式签发、续期开关和消费者绑定。私钥不出API/订阅。测试证书不用于上线，手动停止的核心不被自动续期启动。

## 验收门槛

最终准确提交上重新通过：常规API/状态、ToClash独立参考、真实代理/DNS、完整固定包安装、真实ACME与证书浏览器、一键sudo/systemd安装、文档/发布契约。环境依赖测试由专门任务执行，不能把skip当成功。

一键安装测试不使用实际用户域名；真实CA流程由固定Pebble验证HTTP-01，系统安装以临时证书测试权限、socket、TLS和启动。实际公网DNS/CAA/端口可达性由用户域名与CA最终验证。

## 发布边界

正式Release流程已经设计为手动动作：目标为当前默认分支HEAD，全套最新检查成功，晋升已测试同一个套件，默认草稿，不覆盖现有tag/Release。前置PR仍需审阅合并；没有自动访问用户VPS或真实数据库。

当前公开导出和真实链路首轮仅限sing-box/VLESS/TCP/TLS单用户、空flow、证书验证。其他协议/UDP、DNS-01/通配符、完整sing-box规则迁移、任意YAML导入、Docker/ARM64和反代接管属于后续独立版本，不在本轮偷偷扩做。

## v0.3.1 — Linux 可移植安装（PR #12）

- 发行版/CPU/libc/init/package manager 自动检测；
- x86_64/ARM64、glibc/musl 固定便携 Python 3.12 目标包；
- systemd/OpenRC 服务后端；
- 80/面板/默认节点端口检查；UFW/firewalld 仅在用户明确 `yes` 后开放；
- 自定义 nftables/iptables 与云安全组等待人工确认；
- 代表性 Debian/Fedora/Arch/openSUSE/Alpine 探测，ARM64/musl 原生构建启动验证。

## v0.3.2 — 节点完整编辑和参数回填

现有节点需要从 `settings` / `stream_settings` 反解为统一编辑表单，保留未修改凭据和证书绑定，保存继续使用候选校验/失败保护。浏览器回归覆盖创建→编辑→刷新→再次编辑→导出。

## v0.4.x — 协议矩阵逐项完成

顺序：Trojan/TLS → Shadowsocks → VMess/TLS → VLESS WS/gRPC → Hysteria2 → TUIC → REALITY/Vision → XHTTP/HTTPUpgrade → UDP专项。每一项都必须同时有服务端配置、编辑UI、URI/Mihomo/sing-box导出、真实核心/客户端检查、正向连接和错误凭据/TLS失败路径，不能因为表单存在就写成已支持。

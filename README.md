# V-UI — 个人代理面板 / v0.3.0-rc.2

在 V-UI 管理节点、设置 ToClash 分流，再让客户端直接订阅完整配置。FastAPI + SQLite，不需要常驻 Node、Redis 或外部转换服务。

**这是经过逐阶段开发的候选版本，不是正式 Release，也没有部署到你的 VPS。** 主计划和证据见 [ROADMAP](ROADMAP.md)、[迭代记录](docs/ITERATIONS.md)及 PR #2～#9 的最终 Checks。旧的大 PR #1 保留为集成草稿，未合并 master。

## 当前能力

- 单管理员安全登录、密码重置、会话过期/撤销、同源请求校验和持久登录限流。
- Xray / sing-box 独立配置校验、期望/生效状态区分、失败保护、核心停启和恢复。
- 专用只读订阅：明确节点与输出格式、独立公开节点地址、到期/轮换/撤销；不能取得管理权限。
- ToClash 0.3.8 固定基准：两种网络模式、40项服务预设、始终直连/代理、CGNAT、内网 DNS、独立节点 DNS、规则覆盖提示。
- 分流草稿/已保存/预览区分，冲突与损坏拒绝。同一订阅 URL 在客户端刷新后返回最新保存设置。
- 完整 Mihomo YAML 有节点、策略组、DNS 与 rules；不需要再次打开 ToClash 手工转换。URI/Base64 和 sing-box JSON 只提供经过验证的连接配置，不冒充完整 ToClash 分流。

## 明确的验证范围

公开导出和端到端链路首轮限定 **sing-box VLESS / 原生 TCP / TLS / 单用户 / 空 flow / 验证证书**。其他已有草稿协议表单不等于已验证；不支持的组合明确报错，不丢参数、不返回全直连兜底。

固定核心：sing-box1.14.2、Xray26.3.27、Mihomo1.19.32。ToClash100个独立场景与40项目录对照；真实TLS连接、代理/直连DNS、错误证书/SNI/UUID拒绝和停止核心不降级直连见 [rc.1](docs/LOOPBACK_RC1.md)。UDP端到端和其他协议仍不在已验证矩阵内。

## 部署方式

首轮只验收 **Ubuntu24.04 amd64 + CPython3.12 + 非 root 专用账号 + 单进程 + 直接 HTTPS**。完整命令、信任边界、备份恢复与回滚见 [DEPLOYMENT_RC2.md](docs/DEPLOYMENT_RC2.md)。

仓库原前端模板作为开发输入，构建时确定性地替换为已校验本地资源并选用已验证节点默认值；模板变化会阻止构建，不能静默回退CDN。候选包包含固定wheel、核心与本地前端资源，安装时不联网解析latest；摘要、清单和许可随包保留。旧 install.sh/install-bin.sh 已停止执行，不能再用 root 一键脚本覆盖原服务。Docker/ARM64/PyInstaller/代理反代不是这一版验收路径。

工作路径：HTTPS登录 → 节点管理（默认sing-box/VLESS/TLS，建议10443）→ 分流与订阅 → 创建专用URL → 客户端导入并刷新。

证书由操作者提供；无ACME自动签发。部署需要实际证书、域名和网络配置，本仓库和测试不会自动更改你的VPS。

## 开发与验证

```sh
pip install -r requirements-test.txt
python -m compileall -q app tests scripts main.py
python -m unittest discover -s tests -v
```

环境依赖的真实核心、独立ToClash参考、浏览器和最终包安装测试有独立CI任务；常规测试中的skip必须由相应任务覆盖，不能计作通过。新代码只在临时数据和假凭据上验证。

[认证](docs/AUTH_ALPHA2.md) · [分流工作区](docs/WORKSPACE_ALPHA6.md) · [真实链路](docs/LOOPBACK_RC1.md) · [发布门槛](docs/DEPLOYMENT_RC2.md)

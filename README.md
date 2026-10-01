# V-UI — 个人双核心代理面板（开发中）

**当前迭代：v0.3.0-alpha.2，管理员认证边界。不是正式 Release，也未完成公网部署验收。**

V-UI 的目标是以 FastAPI + SQLite 管理 Xray/sing-box，并直接导出带 ToClash 分流的 Mihomo 配置。已有双核心、协议表单和导出草稿，但“代码已有”不等于每种组合已验证。

先读 [版本计划](ROADMAP.md)；本轮的启动、创建管理员、会话和回滚说明见 [AUTH_ALPHA2.md](docs/AUTH_ALPHA2.md)。开发前阅读 [AGENTS.md](AGENTS.md)。

## 当前可验证的范围

- 无默认密码或 mock token；本机交互式创建/重置管理员。
- 管理 API、旧接口、文档及当前订阅接口统一鉴权。
- 登录、改名/改密、退出、会话过期/撤销、CSRF、限流。
- 保留原入站数据；同源网站托管暂时隔离，旧文件不删除。

本轮不增加协议、不改 ToClash 规则、不做证书申请和核心升级。旧主面板远程依赖及核心兼容性仍待发布前复核。

## 独立测试目录启动

使用 Python 3.12：

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export VUI_DATA_DIR="$PWD/data"
python -m app.admin create your_admin
python main.py
```

命令会询问密码，不接受密码参数。默认只监听 `127.0.0.1:2053`，通过本机或 SSH 隧道访问 `/login`；“面板设置”进入账户管理和退出页面。已有账号使用 `python -m app.admin set-password your_admin` 显式重置。

不要直接执行旧安装脚本覆盖现有服务。Docker、二进制打包、ARM64、HTTPS 反代和真实代理链路要按版本计划单独验收。

## ToClash 集成目标

alpha.5 验证配置引擎，alpha.6 收口分流设置与订阅使用闭环：

```text
V-UI 节点 + 已保存分流设置 → 受保护的订阅 URL → Mihomo 客户端刷新
```

完整 Mihomo 配置应有 `proxies`、`proxy-groups`、`dns`、`rules`，不再需要手动把节点复制到 ToClash。URI/Base64 不承载这些分流设置；sing-box 完整分流转换不算首轮 Mihomo 集成已完成。

alpha.3 前订阅接口暂时也受管理会话保护，不提供匿名导出，不把管理员凭据塞到 URL。

## 测试

```sh
pip install -r requirements-test.txt
python -m compileall -q app tests main.py
python -m unittest discover -s tests -v
python -m playwright install chromium
VUI_BROWSER_CHECK=1 python -m unittest discover -s tests -p test_startup.py -v
```

测试使用临时数据和假凭据。浏览器烟测只覆盖本地登录/账户页和认证跳转，不等于全部旧面板或代理协议验收。真实结果以该版本 PR 的准确提交和 CI 日志为准。

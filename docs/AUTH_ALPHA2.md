# v0.3.0-alpha.2：管理员认证边界

本轮只解决谁可以访问 V-UI，不宣称全部代理协议或公网部署可用。基于 alpha.1 的独立叠加 PR；前置 PR #2 尚未合并时，不直接部署叠加分支到生产。

## 本机首次使用

建议在独立测试目录、Python 3.12 环境中验证，不覆盖已有 VPS 数据。

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export VUI_DATA_DIR="$PWD/data"
python -m app.admin create your_admin
python main.py
```

创建命令会在终端交互式询问 15–128 字符密码；**没有默认账号/密码，没有公开注册 API**。密码不接受命令行参数、环境变量或非交互输入。面板默认为 `127.0.0.1:2053`，通过本机或 SSH 隧道访问 `/login`。

已有账号或旧哈希需要显式重置：

```sh
python -m app.admin set-password your_admin
```

CLI 必须使用与面板一致的 Python 环境和 `VUI_DATA_DIR`。未知旧哈希/原来的 mock admin 不自动获得权限，不清空或重建旧入站。

## 登录和会话

- 密码使用 Argon2id（19 MiB，2 次迭代，1 路并行）；随机会话标识有 256 bit 随机源，SQLite 只保存其 SHA-256 摘要。
- 浏览器使用 HttpOnly、SameSite=Strict、Path=/ cookie；HTTPS 来源使用 `__Host-` 名称与 Secure。无 Web Storage 凭据，不把会话放入 URL。
- 每个会话最长一小时，不自动无限续期；重新登录轮换当前浏览器会话，最多保留五个同时登录会话。
- 退出删除对应会话；改用户名/密码或本机重置会撤销全部旧会话。修改前要求当前密码；停用账号立即不能继续使用原会话。
- “面板设置”进入 `/account`，可查看真实账号、修改或退出。过期访问返回登录；浏览器历史恢复会重新检查页面。

## 管理 API 和 CSRF

默认保护 `/api/*` 的所有方法、旧 Xray/sing-box 别名、分享/导出、OpenAPI/Docs。唯一匿名 API 是 `POST /api/auth/login`，但它仍要求来源校验、请求头和登录限流。

所有状态变更必须同时有精确 `Origin` 和 `X-VUI-Request: 1`。跨来源和缺失来源不放行；没有通配 CORS。非浏览器脚本也需要明确发送这些头，并用登录返回的 cookie jar 访问，不能拿旧 mock token 或查询参数绕过。

认证请求实际正文不超过 8192 字节、读取不超过 10 秒。认证校验错误返回固定安全提示，不回显输入密码。API 和页面响应使用 no-store / nosniff / no-referrer / DENY。

登录尝试在 SQLite 中计数：每来源地址 8 次/5 分钟，每账号 20 次/15 分钟，全局 60 次/分钟，包含成功尝试。超过限制返回 429/Retry-After。只使用 ASGI 实际对端地址；自带 `X-Forwarded-For` 不改变计数。反向代理下未配置可信转发时所有请求可能共享代理的来源限额，这是有意的保守默认值。

## 来源与 HTTPS

没有 `VUI_PUBLIC_ORIGIN` 时，只允许回环 Host 和回环对端访问受保护接口。禁止把默认 HTTP 入口直接暴露公网。

用于经过受控反向代理的测试时，显式设置例如：

```sh
export VUI_PUBLIC_ORIGIN="https://panel.example.com"
```

只填 scheme/host/port，不加路径、查询或凭据；非回环来源必须 HTTPS。代理应保留 Host，并限制上游回环访问。应用入口不信任转发地址头，不通过它们决定管理员身份或限流来源。这不是完成 HTTPS 部署的步骤，也不表示已配置任何真实域名/证书。

## 暂时不可用的功能

1. **客户端匿名订阅**：alpha.3 前所有导出都要求管理会话，不能把管理员 cookie 放进订阅 URL。独立只读令牌在下一版本实现。
2. **同源站点托管**：不再公开挂载 `wwwroot`；其上传和列表 API 在鉴权后返回 503，旧文件保留。以后需要独立来源及上传安全验收才能恢复。
3. **公网生产发布**：旧主面板仍使用未固定的 CDN 依赖；系统命令权限、核心下载/应用、安装脚本和真实链路尚待各自验收。本轮没有修改这些实现。

## 验证与证据

- `tests/test_auth.py` 对真实 main:app 做路由枚举、未登录/伪造凭据、旧别名、CSRF、会话轮换/撤销/过期、密码更新/重置、限流、正文上限、来源拒绝、数据保留和站点隔离回归。
- `tests/test_startup.py` 在临时目录启动真实 Uvicorn，用本地假账号登录后检查 SQLite、OpenAPI、静态文件和正常退出；没有代理二进制，不连接真实节点。
- `VUI_BROWSER_CHECK=1` 额外以 Chromium 检查本地登录/刷新/改名/重新登录/退出及手机尺寸。为保持隔离，旧主面板的第三方 CDN 请求会被拦截；它不是旧主面板全控件验收。
- 新的 CI 在 PR 上跑相同命令。以最新准确提交的日志为准，不引用前一版本的成功来代替。

## 变更与回滚

新增 `admin_sessions` / `admin_login_buckets`，不删除 `users` 或 `inbounds`。新建数据库默认私有权限；已有数据库在面板启动时收紧至仅属主可读写（POSIX）。

上线前备份数据库和数据目录。出错时先停止服务、保持回环/防火墙隔离，再按备份恢复。旧版本没有认证边界，因此直接回退并继续公开监听**不是安全回滚**。本轮没有执行合并、Release 或 VPS 部署。

## 实现依据

- OWASP Password Storage Cheat Sheet：https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet：https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP CSRF Prevention Cheat Sheet：https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html

遵循这些设计项不等于安全审计通过，仍以明确的测试边界和发布门槛为准。

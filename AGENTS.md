# V-UI 开发约束

先读 `ROADMAP.md`，再核对 PR、分支、父提交和前置依赖。当前唯一目标是 **v0.3.0-alpha.2 管理员认证边界**。

- 一轮一个小版本；本轮验证和交付后停止，不自动推进 alpha.3。
- PR #1 继续冻结为集成草稿；当前分支基于 alpha.1 已验证提交，独立小 PR，不擅自合并前置 PR 或 master。
- 保留 SQLite 和原架构、入站数据；不顺手改协议、ToClash 规则、ACME 或核心管理。
- 同源站点隔离属于本轮认证边界；只暂时禁用站点 API/公开挂载，保留磁盘文件。
- 所有管理 API、旧别名、文档和暂存订阅默认鉴权。不能为客户端兼容保留匿名凭据出口；订阅专用令牌另在 alpha.3 做。
- 不保存/记录明文密码、原会话令牌、实际订阅、私钥或真实公司域名到仓库或测试日志。测试只用临时目录和假凭据。
- 不凭空修改核心字段、协议支持或兼容声明；对应版本要查官方资料、固定真实二进制并验证。自定义预期值不是外部证据。
- 区分代码写好、语法通过、测试通过、协议连通、可发布和已部署。CI 排队/运行中不是成功。
- 范围外问题写入计划，不扩大当前 PR。安全发布阻塞必须明确，不因为个人自用而忽略。
- 未经明确要求，不创建正式 Release、不部署 VPS、不强推或删除分支、不清空用户数据库。

检查：

```sh
pip install -r requirements-test.txt
python -m compileall -q app tests main.py
node --check web/js/account.js
node --check web/js/app.js
python -m unittest discover -s tests -v
python -m playwright install chromium
VUI_BROWSER_CHECK=1 python -m unittest discover -s tests -p test_startup.py -v
```

浏览器检查只覆盖本地登录/账户页面及认证跳转，旧面板外部 CDN 组件不在这一烟测范围。不要把未测试的功能描述为已通过。

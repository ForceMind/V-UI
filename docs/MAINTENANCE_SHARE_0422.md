# v0.4.22 维护分享查询修复：唯一计划与证据

用户对明确的最小修复/独审/Actions非浏览器/正常合并公开22/入口/备份远程升级方案回复“是”。授权已完整取得，版本专属发布和后续入口无需重复确认；所有动作以其准确门禁成功为前提。原数据、凭据、证书、协议、路由、proxy开关、核心与旧资产保持，不browser、不本机构建安装，不源码热改/SSH/防火墙，不将运维或敏感载荷公开。私有本地敏感导出只沿用户已明确批准的目的地，不能猜测其他目的地许可。

## 根因和边界

公开21的普通分享API已接受两个64hex可选条件，并在同ORM对象核对instance/content后导出；共用维护UI也已发送。但AdminAuthMiddleware在Bearer分支的subscription query白名单只有core/host，先返回422。TCP用途JSON POST不经过该query问题。修复只在现有精确GET capability模板`/api/subscription/link/{inbound_id}`增加这两个参数，保留同实例内容保护、旧无条件/instance-only兼容、重复/未知/token query拒绝、scope和来源校验；不全namespace放开，也不移除前端条件。

私有验收记录不替代本批22准确资格；具体环境、载荷与比对结果仅私存，不写公开记录。

## 当前交付与顺序

1. app/middleware/auth.py最小白名单及tests/test_maintenance_share_query.py真实ASGI/SQLite/grant/exchange/Bearer回归；只假凭据、不核心/CA/browser。准确公共RPC正向必须覆盖真实中间件，不能仅调用API函数或mock掉鉴权。
2. 版本22和必要的公开21→22真实保数据门槛。21已包含content_revision，所有旧字段必须完全一致；14-20的原新增派生字段契约保持，私有DB/材料/订阅快照比较不变。
3. 产品/必要CI修复独审并冻结；其后仅22专属严格非browser发布门禁及直接文档。准确候选/dev/main各自九组12jobs、仅原12browser步骤可skip，同源4目标、新装和公开14/15/16/17/19/20/21升级全过才晋升，不继承21绿灯、不发布时重建。
4. 匿名核tag/latest/19资产digest与真实installer/SHA256SUMS字节、旧21资产保持后独立切短入口，再核入口候选/dev/main准确九组及实际main URL。最后私有服务器备份、单次维护升级/GET观察、元数据与真实分享双CAS及静态served字节读回。

## 文件责任与当前进度

worker仅middleware及新增HTTP测试；root仅VERSION/main.py/install.sh严格版本、必要安装CI/helper/合同、直接说明；发布门禁worker后续仅publisher/publish.yml/门禁测试；同一只读审查者复核各有界差异，不递归不超过5个新代理，旧WIP保留。

- [x] 重新核main/dev/公开21，建立release/v0.4.22，未提交原xcrun_db保持。
- [x] 最小修复与7方法真实Bearer HTTP回归源码完成，独审/AST/差异通过；后端实际运行留Actions。
- [ ] 22专属严格发布准备及准确候选/dev/main Actions/四目标/真实安装和七旧版升级。
- [ ] 正常公开22与独立短入口准确验收。
- [ ] 服务器私有备份与单次远程升级/实际双CAS分享及配置健康读回。

本机仅AST/可行纯fixture检查，后端依赖/真实构建安装都在Actions。必要升级fixture12项纯测试通过，新增21字段不允许任意漂移；真实升级仍待准确Actions。22尚未发布，当前短入口和服务器均不能据此记录冒称已22。

产品独审收口：精确capability追加两参数、原API/scope/来源/未知和重复参数拒绝保持；新增真实ASGI授权链未mock中间件；公开21响应全相等及原六版/私有数据保护保持。当前只做必要版本/CI/直接说明，不提前换入口；后端实际运行与真实资格待准确Actions。独审发现的计划中运维信息已在提交前通用化，具体记录仅私存。

产品冻结点为72570cc449c2b4b8ac933588894360a5b284e329，之后仅3门禁文件及本记录/直接说明13项allowlist可变。22专属flag、旧flag互斥、七旧版/新装/真实HTTP所在Run tests/模板/netlink/fullchain/short、精确九组12jobs与12明列browser skip和同源四目标晋升已实现。133项纯门禁测试通过，AST/diff/冻结完整字节与步骤名称已核；最终独审及准确候选资格另行结算，不以本机fixture替代Actions。

22门禁独审收口：三文件完整hash与worker值相同，独审者定向新增11项纯mock通过且未发现重要问题；产品725冻结与13项普通文件scope、旧21例外语义、互斥/CLI/workflow/正文、七旧版/九组12jobs及同源四目标保持。当前只完成源码与有限mock资格，最终候选Actions尚待，不能宣称已合并/发布22。所有公开记录仅产品/CI，不含具体运维数据或载荷。

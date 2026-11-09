# Cloudflare DNS-01 与通配符证书契约

状态：2026-11-09，**v0.4.23已公开**，正式应用源`be5d8459cdd3eeedce73f2ff6e4663e5eb781e42`，19项资产/tag/清单摘要已独立读回，匿名公共安装通过。短入口已独立固定23与公开摘要，候选/主线及实际main URL验收通过；应用及历史资产不改写。真实Cloudflare账号签发、浏览器、物理512MiB整机/24小时未验证，本批没有生产升级或真实DNS写入。

用户操作见[候选DNS指南](DNS01_CLOUDFLARE.md)。[首实现提交7e418b6的设计全文](https://github.com/ForceMind/V-UI/blob/7e418b6e10587e61efcecae290302dfd440a0b6a/docs/DNS01_CLOUDFLARE_CONTRACT.md)固定保留原阶段措辞；当前进度以[路线图](../ROADMAP.md)为准。

## 用户能做什么

在现有“证书”页选择验证方式。原HTTP-01仍默认；DNS方式可以申请单个域名，或一个根域名及其所有一级子域名。Cloudflare凭据只需由管理员保存一次，后续自动续期复用。旧证书、绑定、用户、节点及订阅保持。节点继续选择证书，不填写材料路径。

```text
添加证书
验证方式  [通过80端口验证（默认）] [通过Cloudflare DNS验证]

DNS方式：
  证书域名    example.com
  覆盖范围    [单个域名] [此域名和全部一级子域名（推荐）]
  实际覆盖    example.com、*.example.com
  Cloudflare  [选择已保存凭据] [添加凭据]
  提示        此方式不要求开放80端口；不会关闭其他证书使用的80端口。
  邮箱        输入联系邮箱
  □ 我控制这些域名，同意CA条款及公开证书记录；允许为本次签发和续期管理挑战TXT记录
  [申请证书]

添加Cloudflare凭据（仅管理员）
  名称        例如“主域名”
  DNS托管域名 example.com（实际Cloudflare Zone，不要求与证书域名相同）
  API Token   密码输入框，仅写入、不提供查看或复制
  说明        使用仅限此Zone的Zone:DNS:Edit与Zone:Zone:Read；不使用Global API Key
  [验证并保存]（只读核对Zone，不创建测试TXT）

凭据列表    名称 · 托管域名 · 已配置/禁用/恢复后待确认 · 关联证书数
             [替换Token] [禁用]；失败或结果不明时先重新读取状态，不自动重发写入
证书列表    域名 · HTTP-01/DNS-01 · 单域名/根域名+通配符 · 正式/测试
             签发阶段、剩余天数、续期状态、绑定状态与具体中文错误
```

通配符只覆盖一级：`*.example.com`覆盖`a.example.com`，不覆盖`a.b.example.com`。请求中的根域名是有效literal，不把`*`放入面板origin、节点服务器地址或SNI。HTTP旧简易流程默认根域不变；DNS证书在简易/高级节点流程都要求明确“实际连接域名”，例如`node.example.com`，并同时显示交付的服务器地址与SNI，不能凭签发成功隐式选根域。校验SAN覆盖，保存具体SNI，续期仅换材料。DNS-01仅证明域名控制，不能证明A/AAAA指向本机：提示用户为连接域名配置指向本机的A/AAAA；不自动修改这些记录，不将签发成功当公网连通。

## 权限与接口

现管理员会话及Origin/请求头保护保持。维护中间件有兼容`state.admin`，凭据API必须先拒绝`state.maintenance`，不能只判断admin存在。新端点不加入维护capability白名单；同时携带Bearer与cookie也不能扩大权限。

| 接口 | 契约 |
| --- | --- |
| `GET /api/certificates/dns-credentials` | 仅管理员；allowlist返回ID、label、provider、zone_name、revision、enabled、恢复状态及引用数，无Token/尾号/原始上游错误/路径 |
| `POST /api/certificates/dns-credentials` | 仅管理员；严格有界JSON，label、zone_name、api_token；固定HTTPS只读查找准确Zone，保存新ID和不可变凭据修订 |
| `PUT /api/certificates/dns-credentials/{id}` | 仅管理员；显式expected_revision与新api_token；原Zone身份保持，竞争修改拒绝，空值不删除；正在运行的任务使用原修订 |
| `POST /api/certificates/dns-credentials/{id}/disable` | 仅管理员；expected_revision；禁用后拒绝新签发/续期，不删除材料、历史修订或已创建挑战的清理能力 |
| `POST /api/certificates/dns-credentials/{id}/enable` | 仅管理员；expected_revision及显式确认，重新只读验证后启用；用于恢复后确认，不发起签发；替换Token本身不能隐式清除禁用/待恢复状态 |
| `POST /api/certificates/dns-challenges/{id}/cleanup` | 仅管理员；expected journal/profile revision及明确cleanup-only确认；可选择同Zone当前修订，返回202并由后台处理，只清理原任务记录，不授予新签发 |
| `POST /api/certificates/dns-challenges/{id}/confirm-absence` | 仅管理员；相同修订条件及明确人工确认；有界只读核对后记录manual_confirmed，不当作自动清理或新签发 |
| 原`POST /api/certificates` | 兼容旧payload，默认`method=http-01,wildcard=false`；DNS显式`method=dns-01,wildcard,credential_id`；HTTP带DNS字段或wildcard拒绝；首次申请仍需管理员本人接受条款 |

首轮维护授权可读脱敏DNS证书状态、使用已签发材料和绑定。旧`certificates`授权不自动包含DNS写入：DNS证书的手动renew和auto-renew设置仅管理员可操作，HTTP维护行为保持；管理员明确开启的后台自动续期照常执行。不新增读取Token的API。未来委派DNS写入须另立scope和授权，不能静默扩展长期链接。

令牌用完立即从输入框清除；不进URL、localStorage、通用错误、日志或DOM data属性。新增敏感请求应用有限body、重复JSON键拒绝及现有忙状态/取消/页切换保护。保存或替换结果不明时读回metadata，不能盲目重发。

## 数据与材料兼容

- 旧记录迁为不可变`method=http-01`、SAN恰为`[domain]`；ID、revision、日期、退避、auto_renew及绑定保持。迁移在现有`BEGIN IMMEDIATE`中串行、原子、幂等；新schema标记与缺失字段或损坏身份冲突时拒绝，不默认修补。
- 旧HTTP根域证书、新DNS单域、新DNS根域+通配符允许共存，各有ID；唯一身份为规范`domain/environment/method/SAN集合`，重复新建仍明确拒绝，不能原地转换旧ID。单个记录的method/SAN身份创建后不可变，旧revision按原身份验证。
- SAN首轮只允许`[domain]`或`[domain,*.domain]`，规范排序/去重规则明确；接收证书的DNS SAN必须精确相同，重复、额外/缺失或其他类型SAN拒绝。CSR使用同一精确集合。仍检查密钥、时间和生产信任链，不关闭TLS验证。
- 绑定保存literal SNI，校验SAN覆盖；面板只检查原origin的hostname受覆盖，不改origin/Host白名单/URL。续期保留具体节点SNI、desired/applied分离、手工材料路径保护及核心停止意图。
- 安装升级遇到有效DNS面板绑定时复用已应用旧revision，不重签、不修改联系人。same-ID pending保留期望值、错误及已应用材料，不提升待应用版本；desired/applied证书ID冲突或坏材料拒绝混用。仅换证书保留原协议、传输、UUID与未知字段，停止核心不复活。
- 任务开始冻结method、SAN、profile ID、凭据revision与job ID。保持原单worker/cross-process lease、队列上限、退避和签发/应用分离。DNS失败不回退HTTP、不选其他账号，不覆盖旧材料或applied版本。

## Cloudflare与挑战生命周期

固定`https://api.cloudflare.com/client/v4`，验证HTTPS，禁用代理环境继承和重定向；不接受自定义API地址、任意shell hook或CA URL。Token仅限指定Zone的DNS Edit和Zone Read，保存时按准确zone_name查找并要求唯一匹配；不修改A/AAAA/CNAME、云安全组或防火墙。

沿用固定Certbot与CSR模式，DNS使用程序内固定auth/cleanup hook；命令只含可信程序路径及不透明任务ID，不含Token、挑战值或用户命令。具体hook环境兼容性必须由固定Certbot 5.8.0的Actions实测，不以较新文档替代。HTTP路径不继承DNS凭据，CA仍限既有production/staging目录。

每个挑战先把intent（job、不可猜ownership nonce、精确Zone、TXT owner、value、凭据revision）私有落盘并fsync，再创建单个TXT记录；成功后持久化返回record ID。根域和通配符可同名并存多个TXT值，不修改或删除整个RRset。创建超时结果未知时不盲重试POST，按同owner/value/ownership comment查找并核对自身记录。列表一次查无匹配不证明此前POST已停止或没有远端写入，不能自动记cleanup成功或解除同owner屏障；保留unknown并继续有界核对。只有取得确定记录并按ID清理，或管理员另行明确核对远端无残留并记录“人工确认”后才能释放；人工确认不标记为自动清理通过。清理先按ID读取并核对完整身份，再只删除自身ID；不删除其他TXT、复用他人记录或清理全域。

远端API不提供本地SQLite事务或条件删除保证：GET核对与DELETE之间仍可能有外部编辑，文档明确此边界，不宣称跨系统原子。无法证明归属、权限失效或结果未知时保留可恢复记录及明确错误，禁止猜测删除。

API单次10秒；只对GET作至多一次有限重试，写入不自动重试；分页/响应体/记录数有界。DNS传播默认60秒、范围30–300秒；首轮不追随CNAME/NS委派。任何TXT写入前须按逐label边界证明挑战owner位于选定Zone，检查精确owner的CNAME，以及从owner到Zone之间每个低于Zone的祖先NS委派；查验失败或存在委派均明确拒绝，不按字符串后缀猜测、不写错Zone。DNS总操作预算900秒，HTTP仍240秒；取消/超时终止子进程组，随后执行有界清理。传播等待只是准备阶段，不声明全球可达；CA验证才决定成功。

auth/cleanup hook共用持久化journal。正常完成、验证失败、取消、超时、父进程中断后启动恢复均尝试清理；仅凭临时目录删除或Certbot退出不能记成功。清理待办先恢复并阻止同owner新任务；清理失败明确显示，旧材料保留，新材料不晋升为已完成。管理员禁用profile不阻止旧任务只清理自身记录。已被外部撤销的Token可能需要替换后恢复清理：管理员显式选择“使用当前凭据清理遗留验证记录”，携带journal ID与expected profile revision；只允许同provider、同Zone，原任务身份/nonce/value/ID不变，另记cleanup-only recovery_credential_revision；不重发订单、重建TXT或重启签发。自动清理继续固定原修订，不能偷偷改用新Token；界面分别显示签发状态和清理状态。

## 私有凭据、备份及恢复

凭据及其不可变修订存于`data/certificates/dns-credentials/`，目录0700、普通文件0600、受管属主、O_NOFOLLOW/有界JSON/原子替换/fsync；DB只存引用。JSON/metadata无Token摘要或尾号。当前修订切换使用expected_revision与独占profile lease；同一数据树的data_owner共享写入租约与离线备份/恢复独占租约不冲突。

完整私有备份必须包含续期所需凭据和挑战journal；备份为0600 ZIP，**没有加密**。备份前与恢复暂存区校验引用闭包、文件格式/权限/无符号链接及journal身份；普通节点/订阅/证书链导出、公有包和Actions材料不包含这些数据。旧备份无DNS状态继续兼容，声明DNS却缺必需文件明确拒绝，不静默转HTTP或删除记录。

出现DNS证书、凭据或journal后，备份采用新的不可误认kind `backup-dns01-v1`（现通用manifest schema仍为1），旧v0.4.22 restore因只接受`kind=backup`，必须在替换数据前明确拒绝；无DNS状态的旧格式继续保留。新版本restore只按明确格式验证，不把新格式降级改名为旧backup。受支持的activate/restore/在线升级/终端菜单在接触DNS数据时，要求候选声明受信的DNS schema读取能力，不满足则在切换前拒绝；用真实v0.4.22工具回归旧reader拒绝新备份及新工具拒绝旧候选。人工绕过受支持工具直接运行旧程序不属于可恢复降级方式，文档不能称旧程序兼容这些数据。

恢复保留auto_renew偏好，但所有恢复的DNS profile进入“待管理员重新验证并启用”；避免备份复活曾禁用的旧Token并自动写DNS。待确认期间不签发/续期；仍保留旧证书/节点运行材料。恢复不会让已在Cloudflare撤销的Token重新有效，界面清楚说明；恢复后清理待办也先等待确认，不能先写远端再提示。

## 候选与验收状态

以下为发布前候选阶段的历史快照；当前完成状态以[公开发布与短入口](#公开发布与短入口)节为准。原候选清单与失败判定保留，不代表当前仍未发布。

下面是已有提交的实际记录，不授予当前新候选资格。源码版本升为23后，需在新的准确source重跑完整九组/21 jobs；oneclick为一个构建、一个新装及公开14/15/16/17/19/20/21/22的八个升级角色，共10角色。所有role必须拥有自己的真实成功步骤和同producer/source/run/attempt收据，不能用步骤并集、旧skip或旧材料补齐。

原Cloudflare应用冻结点为`4bd1d9a648a8096fbf2fa05314e35049a2408e7c`，其源码与最终迁移夹具已独审。发布准备声明采用`parallel-v1`及`unverified-non-browser-v1`，只记录合同，不是公开发布授权或CI成功声明；冻结之后仅政策文件和普通Markdown可变。准确发布准备HEAD仍须自己的完整九组/21 jobs、10角色同源收据及四目标资格，不能继承冻结点的成功片段；若再需改变代码/CI/测试，应更新冻结点并重新审验。

- [x] 设计、身份/材料/迁移、私有凭据、CF/journal、API/UI/绑定/备份及安装控制闭包源码完成。
- [x] 应用三块及最终CLI/snapshot/安装控制兼容独审收口；早期夹具/allocator/安装错误及错误DNS传输假设已针对性修正并审查。
- [x] 冻结候选104f8c8的[九组Actions/21个job](https://github.com/ForceMind/V-UI/pull/145/checks)全部成功。PR145已普通合入dev，产品树保持。
- [ ] 最终准确main的全部必要step、四目标、新装、八旧版保数据升级和固定Certbot/Pebble/旧22 reader材料，以及10角色同源收据须另验；公开资产只晋升该同源已验套件。
- [ ] 按2026-11-09明确授权完成受保护分支集成、公开23与独立短入口切换；发布和切换尚未执行，生产升级仍需另行授权。

| 提交/运行 | 实际结果与边界 |
| --- | --- |
| `7e418b6e10587e61efcecae290302dfd440a0b6a`：[单元42463373691](https://github.com/ForceMind/V-UI/actions/runs/42463373691/job/128677267825) | 2903项、21个error、237个skip。旧夹具/allocator/安装兼容问题已修正并独审，历史失败不改判，不能称当前全组通过 |
| 同提交：[ACME42463376913](https://github.com/ForceMind/V-UI/actions/runs/42463376913/job/128677277078) | 4case中3过：负向保留旧材料、错TLS/未知写不晋升及真实旧22 reader拒绝分别有证据。正向签发与新账户续期实际完成，但该case因错误UDP假设断言失败，整个运行仍失败 |
| `d7073b8e7b39d0124e9d69c720cdc15331ca2c2e`：[ACME42465018106](https://github.com/ForceMind/V-UI/actions/runs/42465018106)、[loopback42465021149](https://github.com/ForceMind/V-UI/actions/runs/42465021149) | attempt1/2均在固定工具下载遇HTTP504，未进入新测试；不计真实CA、核心或整套资格通过 |
| `9b699b68c7ac4883c1a34faa80b5862f01ea5b83`：[单元42474830641](https://github.com/ForceMind/V-UI/actions/runs/42474830641)、[ACME42474830550](https://github.com/ForceMind/V-UI/actions/runs/42474830550) | 单元2970项通过，239项按既定条件跳过；独立真实核心2项通过。固定Certbot/Pebble的原HTTP 5例及DNS/旧22 reader 4例均实际通过且无跳过；真实Cloudflare账号未验证 |
| 同提交：[链路42474830751](https://github.com/ForceMind/V-UI/actions/runs/42474830751) | 整组成功，含实际controller的11项及新增两后端DNS通配符TLS轮换/深层域名拒绝2项，无跳过。使用临时CA与固定官方sing-box/Xray，不代表公网、手机或浏览器资格 |
| 同提交：[短入口42474830488](https://github.com/ForceMind/V-UI/actions/runs/42474830488)、[安装42474830643](https://github.com/ForceMind/V-UI/actions/runs/42474830643) | 短入口attempt1停于稳定22元数据HTTP504/502，原入口不改的attempt2成功，安装的是公开22；oneclick实际23新装成功。旧14/22已完成真实升级，但after断言仍按旧控制文件集合，因新增恰5个DNS控制文件而失败；正在补严格版本化迁移检查，不把失败改判成功 |
| 同提交：[原生42474830397](https://github.com/ForceMind/V-UI/actions/runs/42474830397)、[部署42474830543](https://github.com/ForceMind/V-UI/actions/runs/42474830543) | 初轮分别在固定tag元数据HTTP503、固定官方资产API的HTTP503处失败；原生整组attempt2仍有HTTP500。各次成功片段不能合成同attempt矩阵资格；未开始的步骤不算通过 |
| `4bd1d9a648a8096fbf2fa05314e35049a2408e7c`：[安装42476468258](https://github.com/ForceMind/V-UI/actions/runs/42476468258) | attempt1实际新装和六旧版升级通过，旧15安装curl超时、旧19公开资产读取超时/API503失败；attempt2的producer遇官方核心API504，消费者未执行。冷却后attempt3实际10角色全部通过，十份小ZIP收据的外层大小/摘要、source/run/attempt/producer及清单/bundle双摘要已核，producer为13616872596。不将前两轮失败改判；后续准备提交不能复用这些材料授资格 |
| 同提交：[原生42476468236](https://github.com/ForceMind/V-UI/actions/runs/42476468236) | ARM glibc的attempt1遇固定tag元数据503，attempt2/3遇固定官方核心资产API503，候选未建成。两musl与distro成功，但整组仍失败。该源其他八组成功不补原生缺口，真实Cloudflare账号/浏览器/物理整机仍未验 |

固定官方Pebble2.10.1的custom resolver[明确使用TCP](https://github.com/letsencrypt/pebble/blob/v2.10.1/va/va.go#L128-L132)。本地夹具同时监听TCP/UDP不等于CA使用两种传输。修正后的实CA断言要求UDP=0、TCP至少4次且issue/renewal各至少2次；同owner双TXT、新账户重新认证、精确SAN和仅清理own ID均保留。上述9b提交已实际通过这些断言，不称UDP回退；后续源码仍需自己的准确验收。

P1完成状态与90分钟受限回归是独立旧证据：main3606cad自身九组/20 jobs与九份同producer收据、22个线上文件读回完成；[安装42457488310](https://github.com/ForceMind/V-UI/actions/runs/42457488310)首次旧19元数据HTTP403后同源码整组attempt2通过，原失败保留。准确dev2bd6ed3的[90分钟回归42455561022](https://github.com/ForceMind/V-UI/actions/runs/42455561022)含面板/单核心各30分钟idle及1/10/50连接各10分钟，36,600请求零错误/无DIRECT，服务cgroup peak291,479,552字节（约278MiB）、无OOM；发生器/持续客户端/目标在cgroup外。这不是160MiB空闲RSS、物理512MiB整机或24小时资格，也不授予23资格。

## 分工与必要验收

实现按三个相接模块完成并已由未参与实现者独审：身份/迁移/材料、凭据/Cloudflare/journal、API/UI/任务与绑定/备份集成；安装控制兼容另审。只提交本批文件，不升级Go、核心pin或任意协议能力。

Actions的最小证据：旧HTTP fixture迁移及重复/损坏/失败回滚；同根三类证书共存；根域+通配符精确SAN/错误SAN/错误CA/错误key；sing-box及Xray具体子域绑定与续期SNI不变/停止核心不启动；假Cloudflare多TXT共存、错归属、创建结果未知、删除失败、取消和重启清理、轮换冻结；Token不出API/argv/日志/普通导出；管理员/维护者/混合认证边界；私有备份恢复和恢复后禁用；固定Certbot通过本地CA实际双挑战；四目标构建、新装及所有旧版保数据升级。无真实Cloudflare签发、无浏览器、无真实512MiB整机/24小时结论。公开发布、入口切换及生产升级另核明确授权。

## 官方依据

- [Cloudflare TXT创建](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/)、[按ID读取](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/get/)、[按ID删除](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/delete/)、[按名称列举Zone](https://developers.cloudflare.com/api/resources/zones/methods/list/)：限定端点、Token权限及记录身份。
- [Certbot手动auth/cleanup hook](https://eff-certbot.readthedocs.io/en/stable/using.html#pre-and-post-validation-hooks)：固定hook模式及续期条件；文档版本与项目pin分开核验。
- [Let’s Encrypt DNS-01](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge)：通配符、多TXT、传播和最小凭据范围。首轮有意不实现其允许的CNAME/NS委派。

## 正式发布启动修正

2026-11-09发布启动修正：原应用功能仍与`4bd1d9a`相同；发布控制重新冻结于`039453e1a95666b5ddc539e67708fd7b22734103`，仅将工作流改为`python3 -m scripts.publish_release`并加入干净子进程回归。原main `bb33bdf`的九组/21 jobs及10份同源收据均通过，但正式晋升在创建tag/Release前因脚本入口无法导入`scripts`而停止。新冻结点之后仍只允许政策JSON和普通Markdown，最终准确main必须重新完成全套验收。

[正式晋升42604920601](https://github.com/ForceMind/V-UI/actions/runs/42604920601)的原始`ModuleNotFoundError`保留。工作流权限、checkout、参数数组、默认main核对、同源材料及不覆盖旧资产的门禁未改；没有创建新tag/Release。新回归使用实际workflow launcher，在无PYTHONPATH的namespace package临时布局中启动真实publisher，假GH/git仅提供只读前置数据，并在策略入口强制停止，未访问网络或发布。原调用精确失败，模块调用及141项门禁单元通过；最终主线和完整字节晋升仍待Actions。

## 公开发布与短入口

- 正式源 `be5d8459cdd3eeedce73f2ff6e4663e5eb781e42`，应用功能保持4bd1d9a，发布控制冻结039453e；[v0.4.23](https://github.com/ForceMind/V-UI/releases/tag/v0.4.23)公开19项资产。
- [主线安装42610073803](https://github.com/ForceMind/V-UI/actions/runs/42610073803)的producer、新装和八旧版升级均attempt1成功；九组/21 jobs及每个必要step、10份同source/run/attempt收据另核通过。
- [正式晋升42612414262](https://github.com/ForceMind/V-UI/actions/runs/42612414262)完整验证并晋升同源字节，不重建。旧启动失败42604920601及候选3a6ba6d的上游403/同源attempt2记录保留，不称永久修复限流。
- 独立匿名HTTPS读取install.sh、SHA256SUMS与RELEASE.json，三个小文件的实际摘要匹配上传元数据，18项清单逐项匹配其余公开资产digest；tag指向正式源。未在本机下载产品包或执行安装。
- 安装器SHA-256：`82dd8ac84f6a7117762359916769a28f6aa505a6e64607d9edda67b56d5e40e3`；完整SHA256SUMS文件SHA-256：`61d6fba8f3d1c5fb6ae39a890db06e8b44e2afa165d1e13451d450a27e583a30`。
- [匿名公共安装42613270949](https://github.com/ForceMind/V-UI/actions/runs/42613270949)成功。短入口代码源`414472ffaa872bc1212a1cc5ff0fd741bdadf1e8`，候选d864ee0和主线414472f各九组21jobs及全部必要step通过；[实际main URL安装42619332425](https://github.com/ForceMind/V-UI/actions/runs/42619332425)成功，使用的不可变正式应用源为`be5d8459cdd3eeedce73f2ff6e4663e5eb781e42`。
- 原配置、账户、节点、订阅、证书及代理设置的升级保护保持；没有访问或升级生产实例。真实Cloudflare账号签发和浏览器未验证。

入口匿名HTTPS字节读回与Git对象一致，SHA-256：`20421be76bf0ba7e3224620fd9c073d597e0f9afd62200ec9ba86ad228e137d4`。本次收口只改Markdown，入口代码、安装器信任锚、正式应用及19资产保持；文档提交不重新授予应用发布资格。浏览器、真实Cloudflare账号签发及生产升级边界保持。

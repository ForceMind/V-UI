# rc.2 — 一个经过验收的部署路径

此版本的唯一目标是完成既定发布门槛，不增加协议或 ACME。选择 **Ubuntu 24.04 amd64 / CPython 3.12 / 专用非 root 用户 / 单进程 / 直接 HTTPS**。其他发行版、ARM64、Docker、PyInstaller 和反向代理不计入本次验收。

最终是否通过以 PR #9 的最后提交和 CI 日志为准。本文描述实现和复现方式，不能代替测试证据。没有合并 master、创建正式 Release、部署用户 VPS。

## 包的内容与信任边界

构建阶段从官方 PyPI 取得完整精确版本 wheel，从官方 GitHub 固定发布取得核心，从官方 npm 固定归档取得页面资源。核心 SHA-256 和 npm SHA-512 在提取/使用前校验；所有运行依赖精确版本见 requirements-runtime.txt，输出 requirements.lock 包含每一个实际 wheel 的 SHA-256。安装时只有 `--no-index --only-binary --require-hashes`，不再解析最新版本或访问在线转换服务。

包包含应用代码、固定依赖 wheels、sing-box1.14.2/Xray26.3.27、Vue/ElementPlus/icons/Axios/QR JS 和 CSS，上游发布标签解析到精确提交的源码归档，以及选定第三方许可/来源文件。不包含用户数据、节点、测试证书/私钥、字体文件、开发测试依赖或 Node 运行时。首次 public export 只验收 sing-box VLESS/TCP/TLS，包含其他核心二进制不等于所有协议组合可用。

MANIFEST.json 记录每个文件的大小、权限和 SHA-256；外部 zip.sha256 校验整包。**摘要不是数字签名**：只能对照来自可信提交/Actions 记录的摘要使用，不能只信任与来源不明包一起收到的文本。新包必须重新经过全部 CI；不会在面板运行时自动拉取 latest。固定依赖也不等于永远没有漏洞，更新需要新的独立验证。

## 安装与启动（用户明确选择后执行）

准备专用非 root 账号、CPython3.12、可信候选源码/控制脚本和该源码对应的验收包。不要运行旧 install.sh/install-bin.sh；它们已停用，以防以 root 覆盖现有服务。

下面是待执行的部署命令模板，不表示已经替你部署。`scripts/deploy.py` 来自你已核对的候选源码：

```sh
umask 077
ROOT="$HOME/vui-instance"
mkdir -p "$ROOT"
chmod 700 "$ROOT"
# 先从可信 CI 记录确认 sha256 文件和包确属同一提交。
SHA="$(cut -d ' ' -f1 vui-linux-amd64-rc2.zip.sha256)"
python3.12 scripts/deploy.py --root "$ROOT" stage vui-linux-amd64-rc2.zip --sha256 "$SHA"
# 使用 stage 输出的完整 release_id；不把示例当成实际版本。
python3.12 scripts/deploy.py --root "$ROOT" activate '<release_id>'
python3.12 scripts/deploy.py --root "$ROOT" admin create your_admin
```

stage 创建隔离虚拟环境并断网安装，检查依赖、启动候选程序和匿名访问拒绝；成功才写 READY。activate 在旧服务已停止时再次检查候选，然后原子更新 CURRENT.json。它不会自动启动服务，也不会迁移/删除旧入站。

管理员密码只能在交互式终端输入，不写进参数。已有实例先停服务并备份，不直接向原数据目录复制未知旧数据库。入站数据、认证和分流保存在稳定的 `$ROOT/data`；release/payload 不是工作数据目录。

把自己合法持有的证书和私钥放到 `$ROOT/data/certs`，归服务用户所有、目录0700/文件0600；证书须匹配配置的域名、私钥须匹配证书。当前不申请证书或自动续期。

```sh
python3.12 scripts/deploy.py --root "$ROOT" run \
  --origin https://panel.example.com:8443 \
  --cert "$ROOT/data/certs/fullchain.pem" \
  --key "$ROOT/data/certs/privkey.pem" \
  --bind 127.0.0.1 --port 8443
```

示例绑定回环。要对外监听必须自行明确改 bind 并配置云安全组；本轮没有执行这些操作。Direct HTTPS 的 origin/监听端口必须一致，最低TLS1.2，不能跳过客户端证书验证；服务不依赖可信代理头，不用管理员 root 权限。节点端口也使用1024以上，首次建议10443。生产进程管理可由操作者安排，但 systemd/Docker/反向代理不作为已验收路径。

运行实例持有外部 panel lease；同一实例不能同时启动两个进程、切换代码、备份或恢复。应用内部核心也有独立进程租约和恢复状态。未验收的系统防火墙操作返回501，不执行主机命令或假报封禁成功；网站托管保持隔离。

## 操作界面

HTTPS登录 → 节点管理（默认 sing-box/VLESS/TLS/10443，填证书路径和SNI）→ 分流与订阅。

构建脚本对原前端模板进行精确、可测试的资源替换，模板不匹配即停止；开发模板本身不是可发布成品。面板使用包内固定资源，运行时不访问外部CDN，不需要Node常驻服务。工作区保持 alpha.6 的草稿/保存/预览区别、40项服务目录和一次显示专用订阅URL。Mihomo客户端刷新同一个URL即可得到节点、策略组、DNS和规则；不必再手动访问ToClash。

## 备份、恢复和回滚

先正常停止实例，再操作：

```sh
python3.12 scripts/deploy.py --root "$ROOT" backup "$HOME/vui-backup.zip"
# 输出备份的 SHA-256；独立保存摘要及备份。
python3.12 scripts/deploy.py --root "$ROOT" restore "$HOME/vui-backup.zip" --sha256 '<可信备份摘要>'
```

备份是含数据库、节点秘密和可能证书私钥的**未加密敏感文件**，默认0600；应置于受控/加密存储，不上传聊天或公共仓库。没有自动备份调度或自动删除。

备份使用 SQLite backup/integrity_check；只支持恢复到原实例的同一路径，避免运行配置中的绝对证书路径失效。恢复先核对全包摘要、文件路径、清单、数据库结构；临时目录准备成功后才切换数据。原数据保留在 recovery/before-*，不会直接删除。恢复后的全部旧管理员会话和订阅令牌撤销，防止恢复备份重新启用已撤销访问；管理员需重登录、订阅按需轮换。

若遇到进程/磁盘故障导致 RESTORE_PENDING.json 未清理，入口拒绝启动。保持停机，检查记录后运行：

```sh
python3.12 scripts/deploy.py --root "$ROOT" recover-restore
```

该操作恢复原数据，部分恢复数据保留在 recovery/interrupted-*，不自动删除。磁盘损坏/不可写时不能保证自动修复，应从外部备份恢复；不要通过直接删除日志绕过。

代码更新：stage另一个已验收包 → 停服务/备份 → activate新版 → 明确run。切换失败保留旧CURRENT。需要撤回已激活候选时停服务并执行 rollback，选择此前已准备版本，不自动回滚数据：

```sh
python3.12 scripts/deploy.py --root "$ROOT" rollback
```

当前验证包含同一代码生成的第二个测试版本与故意破坏启动的候选，证明暂存、健康门槛、指针切换和回退机制；**不是对任意未来数据库迁移或历史版本的兼容承诺**，尤其不能安全回退到没有鉴权的 alpha.1。

## 验收与未完成边界

独立 release CI 在非 root Ubuntu24.04 runner 构建后测试离线stage、完整精确wheel集合、私有权限、实际HTTPS/SecureCookie、错误CA拒绝、真实本地Vue页面和创建TLS节点、停启核心恢复、备份恢复撤销令牌、候选健康失败保护、代码切换回滚。另有归档路径/篡改/权限/并发/恢复中断的独立失败回归。测试CA和浏览器SPKI信任只用于临时测试证书，不改主机根证书库。

原有全部API/登录/工作区/ToClash100场景/真实5项代理链路在最终提交上重新跑。发布包仅在该部署job成功后作为Actions候选附件生成，不发布正式Release。

不保证实际用户VPS网络、UDP端到端、所有协议、GeoSite远程库下载、反向代理、ARM64、Windows/macOS服务器、ACME、备份加密或未来自动更新。仍需要操作者在自己的服务器按文档配置证书/安全组；本任务不自动访问线上服务。

# 图形化证书管理

本功能使用固定 Certbot 5.8.0，通过 HTTP-01 证明域名控制权。仅支持单个 DNS 域名；不支持通配符、IP 证书或 DNS 服务商令牌。不要把这些边界与代理协议支持范围混淆。

## 使用前提

域名的 A / AAAA 记录须正确指向这台服务器，公网 TCP 80 须到达 HTTP-01 验证服务；面板 HTTPS 仍使用独立端口（默认8443）。有 AAAA 就必须能通过该IPv6地址验证。CAA、错误系统时间、CDN重写或被占用的80端口都可能造成签发失败。

一键部署阶段由 systemd 打开80端口并把监听socket交给非root验证进程；面板和 Certbot 均不以root运行。已有站点占用80时，安装器不得自动停止、覆盖它。此版本不提供自动接管Nginx或DNS-01的替代操作。

## 面板操作

从账户页或分流工作区进入“证书管理”（`/certificates`）。先填写域名和联系邮箱，确认自己控制该域名、同意CA订阅协议及公开证书记录，然后提交。

建议先选择“测试签发”。测试与正式环境分别存储，测试签发成功说明验证流程能运行，但证书不受正常浏览器信任，不能绑定面板或节点。确定DNS和端口正确后再选择正式签发。

任务返回202仅表示已排队。页面显示待处理、正在申请、有效、测试证书、申请失败、续期失败及过期；有效期、续期窗口、重试时间和任务结果均来自持久数据库，不用前端计时伪造进度。

正式证书可以绑定到当前同域名的管理面板，或绑定到已有 sing-box/VLESS/TLS、Trojan/TLS、VMess/TLS 节点。面板的SSL上下文会热更新，不需要重启来载入新证书；节点使用现有安全配置应用流程。签发成功与应用成功是两个状态：应用失败必须查看绑定错误并重试，不能只看到“有效”就认为所有位置已换证。

v0.4.3 的 VLESS/WS/TLS 候选沿用上述正式托管证书、绑定和续期流程，最终准确提交验收仍待完成。WebSocket Host 是客户端路由信息，不是证书域名；绑定和证书验证仍使用 TLS SNI，不能用不同 Host 绕过域名检查。

手动停止的核心不会因续期被自动启动。新证书路径可保存并验证，页面显示待应用；主动启动核心后再“重试应用”。手工改过节点证书路径时，自动续期不会覆盖该改动，需重新明确绑定。

## 自动续期

管理服务运行时每分钟检查持久任务。续期窗口按证书实际生命周期的三分之二计算，不假设所有证书都是90天。新证书签发后自动尝试应用到已绑定位置。服务停机时自动检查暂停；systemd恢复服务后继续读取持久计划。

可以暂停/启用自动续期，也可以手动检查续期。未到窗口时不会重复请求CA。失败至少冷却一小时，连续失败按指数退避，最长一天；失败和中断保留旧证书与应用记录。暂停自动续期不取消已由用户明确提交的任务。

旧证书只能在其原有效期内继续正常使用；“失败保留旧证书”不延长其有效期。长期停机导致面板证书过期时，保持面板停止，使用本机bootstrap恢复命令重新签发，再启动HTTPS服务。

## 数据、密钥与备份

数据库新增managed_certificates、certificate_jobs和certificate_bindings。材料保存于数据目录的 `certificates/revisions/<id>/<revision>/`，每次新签发生成新ECDSA私钥。证书和私钥先验证域名、密钥匹配、有效期及正式证书的可信链，再写入私有不可变版本目录，最后提交数据库指针。

管理API和订阅不导出私钥。页面只提供公有证书链下载；服务端私钥路径也不进入客户端订阅。私钥和CA账号数据仅保存在服务器属主可读写目录。Certbot日志保留在 `certificates/logs/<environment>/`，供本机排错，不把包含上游内容的原始日志直接回显到浏览器。

停机备份应包含整个data目录（包括证书、私钥及CA账号），必须按敏感未加密备份管理。不要上传这些实际文件至聊天或仓库。恢复备份会沿用原有策略撤销旧管理会话和订阅令牌。

## 本机引导与恢复

在已验证的运行环境中，以服务用户执行：

```sh
python -m app.certificates.cli test-issuance --root /var/lib/v-ui \
  --domain panel.example.com --email admin@example.com --accept-terms
python -m app.certificates.cli bootstrap --root /var/lib/v-ui \
  --domain panel.example.com --email admin@example.com --accept-terms
```

这两个命令要求面板停止并取得实例操作锁；必须使用正确的活动版本虚拟环境。bootstrap会申请或复用同域名的有效正式证书，持久选择为面板材料，不输出私钥。失败时不要强制绕过冷却，也不要删除数据库来制造新订单。

上面的同意参数只能在操作者阅读并同意CA协议后使用。一键安装器会负责交互确认和正确的服务用户/虚拟环境选择；当前对话未申请任何真实用户域名。

## 常见错误

| 提示 | 处理 |
| --- | --- |
| ACME_VALIDATION_FAILED | 检查域名A/AAAA、CAA、80端口、socket服务和本机Certbot日志 |
| STAGING_CANNOT_BE_DEPLOYED | 正常保护；需要正式签发，不要关闭客户端证书校验 |
| PANEL_DOMAIN_MISMATCH | 必须与面板固定公开来源的域名一致 |
| CORE_STOPPED_PENDING_APPLY | 核心被手动停止，启动后重试应用 |
| BINDING_CHANGED_MANUALLY | 节点路径被手动改过，检查后重新绑定 |
| RETRY_COOLDOWN | 等待列表中的重试时间，先修正DNS/端口原因 |
| INVALID_CERTIFICATE_MATERIAL | 信任链、域名、时间或密钥检查失败；旧材料不会因此变成可信 |
| PANEL_HOT_RELOAD_UNAVAILABLE | 当前为开发入口；使用受管HTTPS入口 |

## 测试范围

固定Pebble 2.10.1的真实ACME测试使用实际HTTP-01取文件，不启用“always valid”。实际测试签发、再次签发、验证失败、CA链/私钥隔离；独立TLS socket测试验证热更新与错误材料拒绝。图形界面由Chromium使用同一真实ACME流程验证。

测试的临时CA仅传给测试子进程/构造器，不修改系统信任，不关闭TLS校验，不提供可由管理API指定任意CA URL的参数。测试通过证明实现路径，不等于已经替你的公网域名完成验证。

依据：[Let’s Encrypt 验证方式](https://letsencrypt.org/docs/challenge-types/)；[Certbot 5.8.0 参数](https://eff-certbot.readthedocs.io/en/stable/man/certbot.html)。

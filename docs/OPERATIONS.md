# 运维、升级与恢复

以下命令针对一键安装实例`/var/lib/v-ui`。不要用旧root一键脚本或直接覆盖版本目录升级。版本中的开发脚本也不能替代经过验证的完整套件。

## 服务状态

```sh
sudo systemctl status v-ui.service v-ui-http01.socket v-ui-http01.service
sudo journalctl -u v-ui.service -n 100 --no-pager
sudo systemctl restart v-ui.service
```

验证服务由socket激活；未收到80端口请求时，service尚未运行是正常的。检查socket是否active。面板进程会执行续期任务，停止面板会暂停续期；再次启动后按持久状态继续。

证书CA原始诊断在`/var/lib/v-ui/data/certificates/logs/`，属主私有，只在本机检查。不要把完整日志、订阅地址、数据库或密钥上传公开Issue。

## 受管升级

先确认新包在同一部署矩阵通过全部检查，并核对SHA256SUMS。用原安装参数执行，增加`--upgrade`：

```sh
sudo bash install.sh --bundle ./vui-linux-amd64.zip --sha256 '<可信整包SHA256>' \
  --domain panel.example.com --email admin@example.com --admin my_admin \
  --port 8443 --accept-terms --upgrade
```

已有证书模式需保持同模式及原有效证书参数。脚本不默默修改域名、邮箱、端口、绑定地址或管理员用户名。

流程为：先离线暂存/验证候选 → 停止自己的受管服务 → 创建私有停机备份 → 显式激活 → 恢复服务/HTTPS健康检查。失败时尝试恢复原配置和代码选择；数据不被删除，原版本不存在或磁盘本身故障时不会假报“已自动修复”。仍需保留离机备份。

新旧schema不兼容时不能靠代码回滚解决。当前回滚测试覆盖同schema候选和损坏候选，不保证任意历史/未来迁移兼容，也不得回退到没有鉴权的旧alpha.1继续公网运行。

## 活动版本与管理员恢复

在root终端仅用于查询活动版本，再用服务用户执行应用命令：

```sh
ROOT=/var/lib/v-ui
ID="$(sudo python3 -c 'import json;print(json.load(open("/var/lib/v-ui/CURRENT.json"))["release_id"])')"
PY="$ROOT/releases/$ID/venv/bin/python"
APP="$ROOT/releases/$ID/payload"
sudo systemctl stop v-ui.service
sudo -u v-ui env VUI_DATA_DIR="$ROOT/data" PYTHONPATH="$APP" \
  "$PY" -B -m app.admin set-password my_admin
sudo systemctl start v-ui.service
```

交互式输入新密码，不能放进shell历史。密码重置会撤销旧会话并使旧订阅凭据失效，重新登录后按需轮换订阅。

## 停机备份

停下所有可能写data的受管进程再备份，包括证书验证响应进程：

```sh
sudo systemctl stop v-ui.service v-ui-http01.service v-ui-http01.socket
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" \
  backup "$ROOT/manual-backup.zip"
```

输出备份摘要，独立保存。备份包含SQLite、核心配置、订阅秘密、证书私钥及CA账号，是**未加密敏感文件**，默认600；转移至受控或加密存储。不要把它当作安全可公开的诊断包。

恢复必须到原路径，禁止为绕过绝对路径校验随意改根目录：

```sh
sudo -u v-ui "$PY" -B "$APP/scripts/deploy.py" --root "$ROOT" \
  restore "$ROOT/manual-backup.zip" --sha256 '<可信备份摘要>'
sudo systemctl start v-ui-http01.socket v-ui.service
```

恢复先验证摘要、清单和数据库；原数据保留在recovery目录。旧管理会话和订阅一律撤销，防止已撤销的访问因旧备份恢复而复活。管理员需要重新登录，订阅按需轮换。

出现RESTORE_PENDING.json时保持停机，核对备份并执行`recover-restore`，不能直接删除日志以绕过检查。损坏磁盘可能需要离机备份，不存在无条件成功的恢复承诺。

## 证书过期导致面板无法启动

确认80验证服务可用、DNS正确；面板保持停止，用活动版本执行本地恢复：

```sh
sudo systemctl stop v-ui.service
sudo systemctl start v-ui-http01.socket
sudo -u v-ui env VUI_DATA_DIR="$ROOT/data" PYTHONPATH="$APP" \
  "$PY" -B -m app.certificates.cli bootstrap --root "$ROOT" \
  --domain panel.example.com --email admin@example.com --accept-terms
sudo systemctl start v-ui.service
```

不要关闭浏览器/客户端TLS校验来绕过过期。CA重试冷却不会因重新运行命令被跳过。

## 移除服务而保留数据

先备份，再关闭自启动：

```sh
sudo systemctl disable --now v-ui.service v-ui-http01.socket
sudo systemctl stop v-ui-http01.service
```

不提供默认删除数据库/账号/证书的一键卸载。需要彻底移除时，由操作者确认备份后删除对应三个unit、root控制配置和启动器，再daemon-reload；数据目录、历史备份和专用账号是否删除应分别决定，避免误删仍有价值的证书和节点。

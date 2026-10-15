# 双开关 M1：安全导入与草稿实现

日期：2026-10-15。承接 [M0 合同](DUAL_PROXY_RELAY_CONTRACT.md)和 [PR #40](https://github.com/ForceMind/V-UI/pull/40)。本文记录 M1 代码及验收入口，不表示已经完成运行代理、界面、真实双跳、正式发布或服务器部署。

## 已实现范围

- `upstream_profile.py`：严格不可变 schema 1；单 VLESS UUID、TCP、TLS 或受限 REALITY/Vision。普通摘要不返回 UUID、公钥或 short ID 原值。
- `upstream_import.py`：URI、V2Ray/Xray JSON、Clash/Mihomo YAML，候选可支持/不可支持原因清单；保存必须明确选择索引并确认未导入区块。文件与协议能力分开，不下载 provider 或执行脚本。
- 解析使用独立解释器进程，2 秒绝对期限、128 MiB 地址空间、最多 2 个并发 worker、256 KiB 输出上限；1 MiB 输入、8 KiB URI/字符串、128 候选、16 层结构、32768 token 和有符号 64 位整数边界。超时、输出超限和取消均回收子进程；限制不可用时拒绝，不能回落到无隔离解析。
- `proxy_store.py`：私有不可变秘密与修订文件、初始化标记、线程/进程锁、确定性 revision、If-Match 冲突检查；草稿、活动状态与应用 journal 分开。M1 仅开放草稿接口，不调用事务接口启动核心。
- `/api/proxy/import/preview`、`/profiles`、`/settings` 与 `/capabilities` 复用管理员会话、同源和 CSRF 检查；实际请求 bytes 上限 2 MiB，输入校验错误不回显原文。版本字段严格整数，不接受布尔值或浮点值冒充 `1`。
- 两个草稿开关可独立保存四种组合；任一开启需明确选定上游，本机开启还需已有独立账号密码。所有组合均保持 `active=null`，直到后续运行阶段真正应用。`/capabilities` 明示 `draft-only`、`apply_available=false`。
- 停机备份保留经过验证的 `.proxy-state-initialized` 和全部所需历史修订/秘密，排除瞬态锁与任意隐藏文件。恢复先在私有 staging 中验证，然后才替换数据；兼容 ZIP 不保存空目录及调用者 umask 022 的情形，不能修复或放宽 live 路径权限。

普通节点数据库、入口凭据、端口、导出规则和核心 pins 不因 M1 草稿改变。没有新增公开监听或代理环境设置。

## 恢复安全边界

普通恢复不允许把当前任一本机/接入 ON 状态变成 OFF，包含旧的无代理状态备份和合法但双关的旧备份。需要恢复这类备份时，应先在后续运行功能中明确应用相应关闭策略，而不是借恢复隐式降级。

当前状态损坏、缺失已初始化材料或存在未结束应用事务时，普通恢复拒绝并保留证据。M1 **尚未实现专用离线恢复流程**；错误指向专用离线恢复需求，不能宣称普通“恢复已验备份”已经能修复这些未知状态。不得删除标记、数据库或 journal 来制造新安装的双关默认值。M2/M4 必须完成并验收相应恢复处理后，才能开放完整运行资格。

## 检查入口与证据分类

本地专用测试：

```sh
PYTHONPATH=.:tests python -m unittest \
  test_upstream_profile test_upstream_import \
  test_proxy_store test_proxy_api test_proxy_backup test_proxy_installed_smoke -v
python -m unittest discover -s tests -v
python scripts/check_docs.py
```

工作树专用组合首次最终集成运行 **131 项通过，28.258 秒，无 skip**；这不替代后续准确候选的全套测试和 CI。独立审查另行覆盖模型、解析器、API、存储、备份以及既有 release-tools 回归。后续修改后须重跑，最终数量、SHA 和结果以承载 PR 为准。

`scripts/proxy_import_smoke.py` 已接入既有包消费门槛：x86_64 glibc 的 release deployment，以及 ARM64 glibc / x86_64 musl / ARM64 musl 的 portable stage。它使用包内解释器和模块执行 TLS/REALITY × URI/JSON/YAML 六种解析、危险 YAML 拒绝、秘密隐藏和双关草稿；临时私有数据不接触安装实例数据。源码解释器上的本地 smoke 通过，不代表四个包内运行已经通过；准确 CI 必须分别取得实际结果。

M1 不进行海外连接或用户线路探测。固定核心配置解析、真实双跳、UDP 拒绝、DNS 路径、防回环、浏览器、系统服务、真实外部客户端均不能由本阶段单测替代。

## 开发期问题记录

以下问题在验收过程中发现并修复，记录保留：

1. 导入器与存储的来源格式枚举不一致，初轮 API 10 项中 4 项失败；统一为 `uri`、`v2ray-json`、`clash-yaml` 后重新通过。
2. 存储硬链接负例误选历史修订，改为明确选取当前 revision；未降低安全断言。
3. 原停机备份跳过初始化标记，实际 backup/restore 后无法读取代理状态；增加精确标记及秘密快照、真实往返回归。
4. `Literal[1]` 会接受并转换 `true` / `1.0`，改用严格整数和精确数值约束，三个版本字段均增加负例。
5. 解包的中间目录在 umask 022 下不是 0700，恢复校验拒绝；仅规范化新建的私有 staging 结构，live 不安全路径继续拒绝。
6. 显式 `alpn: null` 曾可能被归一化为省略，已统一拒绝并增加回归。

所有代码阶段仍走独立 Draft PR、准确候选验收、正常合并和准确主线复核；本阶段不创建 tag/Release，不覆盖历史资产，不部署。

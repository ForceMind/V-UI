# Claude 接手入口（2026-10-27）

## 先拉这个分支，不要只拉 master

```sh
git fetch origin
git switch --track origin/handoff/claude-continuation-20261027
```

这是未合并的继续开发 / 交接分支，不是正式发布。用户要求同步已完成但尚未上传的代码、测试、文档供 Claude 接手；本次不增加产品功能，不直接合并。先阅读根目录 AGENTS.md、[开发交接](DEVELOPMENT_HANDOFF.md)与[冻结验收清单](FROZEN_VERSION_TEST_HANDOFF.md)。

## 包含哪些工作

- 基础主线 `f01dbd824ba1c0dcf07ff818103cdb98fbd7c460`：双开关、每入口 TCP、工作区与状态恢复。准确主线八组 / 11 jobs / 126 steps 已验；2026-10-27 只读检查 master 仍为此 SHA。
- 原离线 YAML 结构预览：本地 `2fe0d2235abbf4f6cb39c38d16d9643412e3d357` 与 [Draft PR78](https://github.com/ForceMind/V-UI/pull/78) 的远端 `3281b356d0bb8a6db16f000795ee6dbadc2a8925` 树相同：`091fc71a49e8fbe4729749c2a11bc2a3983613db`。PR78 保留且未合并，不重复实现或随意关闭。
- 本地 `cdfab647f9116c319f5dc74df8e82a3ddbc882bf`：固定词汇中文报告与 `--format text`，默认 JSON 保持，ASCII locale 下明确 UTF-8。
- 本地 `e17330b2fd5bae4eb07089bbbac5d61a211689e5`：现有 `/proxy` 的显式只读 YAML 预览、API、严格 UTF-8 文件及异步生命周期保护。
- 本地 `10f8c96502f642423e5dfdac877b437e6e96d480`：冻结版本交接与仅超时失败采集的 PTY 测试诊断。产品菜单代码未修改。

GitHub Git-data 若重新创建提交，其提交元数据 / SHA 可能不同；原本地提交保留，必须核完整 tree 与上述阶段一致并在 PR 记录对应关系，不能声称推送了不存在的原 SHA。当前分支是三批继续工作的完整累计内容；基础安装测试不必等待可选 YAML 整合。

## 最后一次本地验证及未解决问题

2026-10-26 首次普通套件 **2231 项：2001 通过 / 1 失败 / 229 条件跳过**（266.978 秒）。失败：`test_ops_menu_stage.StageMenuTests.test_actual_pty_eof_and_ctrl_c_at_each_preconfirmation_prompt`，index 0 / Ctrl-C，包路径提示后 60 秒未退出。单独一次和预限定连续 20 次（120 子场景）未重现；**根因未知，不是已修复或已证实环境抖动**。

仅补自建 PTY / 子进程的失败时固定元数据诊断，未放宽断言、超时或重试。独审通过。之后单轮最终普通 **2236 项：2007 通过 / 229 条件跳过**（205.816 秒）；新诊断与 stage 聚合 21 项通过。后次通过不覆盖首败。各阶段历史结果、边界见开发交接；跳过不算原生资格。

复现命令（已有依赖环境；不自动安装工具）：

```sh
PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=.:tests .venv/bin/python -m unittest test_mihomo_preview test_mihomo_preview_report test_mihomo_preview_api test_mihomo_preview_panel test_ops_menu_diagnostics test_ops_menu_stage -v
.venv/bin/python scripts/check_docs.py
node --check web/js/mihomo-preview.js
git diff --check
```

本次发布分支的准确新 CI 以 PR 当前状态为准，创建时尚待运行；不能继承 f01 或 PR78 资格。面板真实浏览器专项此前未取得运行结果，已经加入原有门槛；Node 合成 DOM 不替代 Chromium。不得重试 / 绕过本地既知 Chromium/netlink 拒绝。没有用户手机 / 线路或真实 1 vCPU / 512 MiB 整机连续 24 小时资格。

## 下一步与冻结边界

先确认分支完整树 / CI，查看准确新提交的浏览器及四目标包结果，定位保留的 PTY 失败；如有人接手，先协调同一分支的编辑所有权，不并发覆盖。新增修改须保留历史、独审并重新准确验收，不把旧绿灯授予新提交。

YAML 始终只读、不可应用，不导入 / 合并 / 保存配置；面板文本由用户点击后发送给服务器，不是浏览器内解析。保持认证、Origin/CSRF、输入/资源限制、值无关报告、清空与迟到响应失效。合同见[面板预览](MIHOMO_PANEL_PREVIEW_CONTRACT.md)与[离线报告](OFFLINE_MIHOMO_PREVIEW.md)。

不恢复受限 UDP 工作、不改固定核心 / VERSION、不新增功能、不开新服务；不操作用户 Mac/服务器、凭据、网络安全设置；发布 / 部署仍单独决定。本次上传不是合并或正式发布授权。

## 用户自行测试基础版材料

f01 的已核原始 Actions 附件：[x86 GNU kit](https://github.com/ForceMind/V-UI/actions/runs/40066393778/artifacts/12549905409)、[ARM GNU bundle](https://github.com/ForceMind/V-UI/actions/runs/40066393775/artifacts/12549473169)。它们不是本分支新构建产物，不含可选 YAML。x86 kit 在 2026-11-07 05:30 UTC 到期，ARM bundle 在 2026-10-31 05:34 UTC 到期；过期不可假装仍可下载。ARM 附件只有运行包，安装器须取同 f01 kit 的通用入口并显式选择 ARM 包；不能直接运行 x86 包或混用旧版本。用户已收到单独核验的安装交接；首次安装与升级、安全前提仍依[安装指南](INSTALLATION.md)和[运维指南](OPERATIONS.md)，其中固定 Release 下载命令不等于此候选。正式 v0.4.8 Release 是更早安装器修复基线，不能代替 f01。

已核外层附件 SHA-256（2026-10-26 字节检查）：
- x86 kit：`a4bd7cac4dcbb127bb998b2901aa61bd824ef24739914f71fb09011ef7d61e9e`
- ARM bundle：`bb559000fd475a9e2dd3b0d8b1af46b03ab8a2d62c3a94d5c5f660424542e33a`

不要仅凭摘要信任未知来源；下载失效或校验不符时停止，不改摘要或换不明镜像。

# 2026-10-28 仓库与短安装入口维护

## 批准目标与范围

用户批准先归档再清理、保留 main/dev、每版本独立分支、重写 README，并上线短安装命令。构建与真实安装全部在 GitHub Actions，不做浏览器测试；不覆盖正式 Release/tag/资产，不启动 Go 迁移，不操作用户 VPS。

应用套件保持公开 v0.4.9，source_commit 为 `a39b6e009829dd7f1be772fd3454474903b9858a`。新的仓库引导入口与冻结应用套件分别核对准确源码身份。入口安装器 SHA256 为 `5aecc3bc2c3ade67946c334091c20ade1e577c7b00c8cadcbe18edf05827312f`，完整 SHA256SUMS 文件 SHA256 为 `bae439c5db3586a04406ac5f0455938ed88c4019602c162af89535f1c6c7bde9`。

## 归档与分支

原远端共有 95 个分支，77 个 head 在稳定主线历史中（含 master/dev）；18 个不在主线的 head 经远端 compare 逐项确认后，保存为 `archive/branches/2026-10-28/<原分支名>` 标签，并在清理前再次回读。完整名称、SHA、合并状态及 archive_ref 见[恢复索引](BRANCH_ARCHIVE_20261028.json)。

旧 Draft PR #33、#32、#31、#48、#14 归档关闭，提交与讨论保留，未把未完成设计或 UDP 实验混入产品。默认 master 正常重命名为 main，dev 正常快进到同一稳定提交。93 个旧分支以准确 head 租约原子删除；现有正式版本标签及 v0.4.9 Release 的 ID、正文、19 项资产 ID/大小/摘要均回读不变。

恢复旧未合并工作时，先从索引取得准确 archive_ref 与 sha，在新任务分支中按需恢复，不能直接覆盖 main/dev。例如：

```sh
git fetch origin refs/tags/archive/branches/2026-10-28/docs/service-rule-packs
git switch -c restore-service-rule-packs FETCH_HEAD
```

本批临时维护分支为 `v0.4.9-install-docs`。后续版本采用 `release/vX.Y.Z` → dev → main，已完成临时分支删除，正式版本通过 tag/Release 标识。

## 安装与 CI 契约

- 短入口为 `bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)`，当前只接受默认或显式 `--version v0.4.9`。原有安装选项按 allowlist 原样转发；固定托管开关、版本摘要及交互模式不可被覆盖。
- 提权不依赖 sudo 继承过程替换的 fd；使用固定 PATH、干净环境、root 私有下载目录及固定两摘要。入口内部失败保留非零和清理语义。入口本身尚未下载成功时，外层过程替换的空 Bash 可能返回 0，需核对实际完成输出；见[安装指南](INSTALLATION.md)。
- 新增 short-install.yml；对确切源码的匿名公网 setup.sh 做字节核对，再执行真实短命令。main 使用 README 中的实际 main URL；PR/dev 使用确切 commit URL。旧公开套件 metadata/安装器/manifest/发布正文长命令验证继续保留。
- 复用空白 systemd runner 的真实交互域名/邮箱/管理员/端口/密码、非 root 服务身份、sudoers 前后快照、全局菜单启停、重复拒绝、同包升级/备份与重启恢复。测试使用既有提供证书参数和临时假材料，不称真实域名 CA 签发。
- test/acme/release 的浏览器步骤保留，仅显式 workflow_dispatch + run_browser=true 可执行。本次不选入。发布器补充准确 run_attempt 的所有 jobs/steps 成功、必需浏览器步骤存在、分页界限与重跑状态复核；skip 不授予发布资格。

## 独审与验证状态

独立只读审查发现并要求修正两处：原发布器仅看 workflow success，浏览器 opt-in 可能放松资格；短入口固定交互，重复/升级必须有真实控制终端。现已增加 job/step 与必需步骤检查、skip/缺项/错提交/重跑反例，以及重复/升级 PTY 执行与控制终端/退出码回归。

本地 setup 模拟 10 项与独审抽样通过；后续发布/短入口专项 22 项通过。首次本地专项因 /var 临时目录符号路径与沙箱 /dev/tty EPERM 未通过，使用已有安全临时目录并在无网络沙箱外跑控制终端单测后通过；未构建包、未真实提权或安装。文档/品牌/既有 fixture 专项 20 项及文档检查通过。

准确候选、dev/main 自身 CI、真实公网短入口和四目标构建以本批承载 PR 的最后记录为准，不在本文件预填未来提交或成功。构建制品不下载到本机；浏览器未验证。正式应用版本和固定核心保持。

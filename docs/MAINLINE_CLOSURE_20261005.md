# v0.4.2 主线收口记录（2026-10-05）

这是已完成的 v0.4.2 主线集成验收记录，不是 v0.4.3 候选通过、公开发布或真实部署的声明。后续源码变化须重新验证自己的准确提交，不能复用本页结果或历史 PR 附件。

## 准确主线与合并范围

- 仓库：ForceMind/V-UI；默认分支：`master`
- 最终提交：[0225ce4b1301e70068421e54c409b303d45cf812](https://github.com/ForceMind/V-UI/commit/0225ce4b1301e70068421e54c409b303d45cf812)
- 最终 tree：`1a642d38bbd55d8cd12ebfabdbd421624a7a08f3`
- 正常 merge commit 顺序：PR #1、#2、#3、#4、#5、#6、#7、#8、#9、#10、#11、#12、#13、#15、#16、#17，共 16 个；所选 head 均为最终 master 的祖先，最终 merge parents/tree 已核验
- [PR #14](https://github.com/ForceMind/V-UI/pull/14) 明确排除；收口时仍为 open/draft，保留原分支，不以它替代已选择的 PR #13

部分 retarget 后 GitHub 的临时 test-merge 元数据滞后；核验依据是实际 master 的准确 merge commit、双亲和 tree，不将旧测试合并元数据视为最终提交。合并历史与源分支没有被强推改写。

## 最终 master 的验收

以下为上述准确 master 提交的最新 push 工作流；八组全部 completed/success，共 11 个 job 及其步骤成功，无需重跑。它们不是历史 PR CI 的替代引用。

| 工作流 | 准确 run |
| --- | --- |
| Test V-UI | [37281500136](https://github.com/ForceMind/V-UI/actions/runs/37281500136) |
| ToClash | [37281500100](https://github.com/ForceMind/V-UI/actions/runs/37281500100) |
| Real loopback | [37281500099](https://github.com/ForceMind/V-UI/actions/runs/37281500099) |
| Release deployment gates | [37281500108](https://github.com/ForceMind/V-UI/actions/runs/37281500108) |
| ACME | [37281500082](https://github.com/ForceMind/V-UI/actions/runs/37281500082) |
| One-command installation | [37281500045](https://github.com/ForceMind/V-UI/actions/runs/37281500045) |
| Portable matrix | [37281500094](https://github.com/ForceMind/V-UI/actions/runs/37281500094) |
| Docs | [37281500036](https://github.com/ForceMind/V-UI/actions/runs/37281500036) |

普通 discovery 运行 240 项测试，其中 36 项环境门槛明确 skip；这些 skip 不算通过。独立启用的真实核心、认证/工作区/节点编辑浏览器、25 项真实 loopback、Certbot/Pebble 与证书浏览器、离线部署/恢复、sudo/systemd 安装和四目标 portable 验证均通过。所有部署与签发测试只在临时 CI 环境完成，不代表用户 VPS 或公网 CA 实际上线。

## 同一准确提交的附件

已下载核验一套 one-click release kit 与三套 portable artifact 的传输摘要、包内 checksum 和版本/source/target manifest，共四个 Linux 目标、13 个待晋升资产文件。聚合只校验并使用原始准确附件，不重新构建。

- x86_64/glibc release kit：artifact `11332272318`，来自 one-command run，记录的到期日为 2026-10-19
- ARM64/glibc：artifact `11332787055`；ARM64/musl：`11332138617`；x86_64/musl：`11331893383`，均来自 portable run，记录的到期日为 2026-10-12

到期日只描述当时 Actions 附件保留期，不保证链接永久可用。附件过期按[发布流程](RELEASING.md)重新取得准确提交的验收附件，不改用来源不明的包。

## 未发生的发布动作

收口时没有创建 Draft Release、v0.4.2 tag、公开 Release 或部署；只存在历史 v1.0.0 tag，不把它当作当前版本。主线验收完成、草稿准备、草稿实际创建、公开发布和部署是五个独立状态。

当时只读 Actions 查询观察到：按 `head_sha` 过滤返回空列表，但按 `master` 列举并逐 run 读取可确认上述八组 exact-head 结果。这不是工作流失败，也不是放宽门槛的依据；真实晋升前仍须在所用发布路径诊断查询行为，不能忽略缺失结果。

来源：[该提交的发布约束](https://github.com/ForceMind/V-UI/blob/0225ce4b1301e70068421e54c409b303d45cf812/docs/RELEASING.md)与[原路线图](https://github.com/ForceMind/V-UI/blob/0225ce4b1301e70068421e54c409b303d45cf812/ROADMAP.md)。下一独立版本只推进 VLESS WebSocket；gRPC 与其他协议继续分别验收。

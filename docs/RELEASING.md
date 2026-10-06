# 正式发布流程

正式Release是一次明确的公开写操作，不等同于PR、版本字符串或Actions候选附件。此流程只晋升经过验收的完整套件，不在发布时重新构建“另一份看似相同的包”。

当前 v0.4.7 [REALITY/Vision 已完成主线收口](REALITY_VISION_CLOSURE_047.md)：独立审查、准确候选八组和正常合并后的准确主线八组已验收。首次 REALITY 前置 HEADERS 断言失败、最终 master 继承 VMess/Mihomo CA 被动轮询缺少 x509 原因的首次失败及 unchanged-code attempt 2 通过均保留；重跑不表示旧轮询问题永久修复。[PR #25 XHTTP 刻画](XHTTP_CHARACTERIZATION_CLOSURE_048.md)已正常合并至签名 master `66dbe70cfa86fe140fa1b69d3896244ab17a0e3c`，与候选 `f7996c579418d1ffe5df06fc12677cf22a42e034` 的 tree 相同，独立审查及两端各八组/11 jobs/全部步骤 attempt 1 成功。仅测试/文档，产品 VERSION 仍为 0.4.7，公开 XHTTP/HTTPUpgrade 支持保持阻断。产品范围冻结，先完成本版发布准备和交付决定，再推进独立 UDP/DNS 实现；现有只读审计不构成实现验收。

截至 2026-10-06 本次检查，未晋升附件、创建 Draft Release 或执行新建 tag/公开 Release/生产部署；源码、原始 CI artifact、已核验附件与公开发布分别记录。完成代码、PR、CI 或发布准备不等于获准创建发布草稿或公开发布；任何发布操作仍受下述人工门槛与明确授权边界约束。

## 0.4.7 当前准备状态（2026-10-06）

- v0.4.2、WS 0.4.3、gRPC 0.4.4、HY2 0.4.5、TUIC 0.4.6、REALITY/Vision 0.4.7 与 PR #25 刻画均有各自准确主线验收；这些历史阶段未因此成为正式 Release。当前准备一个累计产品 0.4.7，不回填旧阶段发布、不将刻画称为产品 0.4.8。
- 本次只读检查的 [Releases API](https://api.github.com/repos/ForceMind/V-UI/releases?per_page=100) 返回空列表；已存在历史 tag `v1.0.0` → `196c9b97ded5ef2391e01cefff408f516fbac508`，保持不动，不能据此推导本轮已发布。未发现 v0.4.x tag；本轮未创建 tag/Draft Release/公开 Release 或晋升附件。
- 此最终状态文档自身的候选审查、准确候选八组、正常合并和准确主线八组必须独立完成。本文不预先宣称自身未来提交通过，也不填入未知 SHA；结果记录在对应 PR/发布准备证据中，不为回写自身验收再滚动产生新提交。
- 文档收口后的最终当前 master 通过八组后，才选择它自己的一键套件和三份 portable artifact，核验完整四目标资产。旧 `6b049262` 的套件 metadata 只证明当时来源/大小/过期时间，未证明归档字节、内部 MANIFEST、许可证或聚合摘要；它不是最终套件验收。PR 候选或 `66dbe70c` 的包也不能冒充后续文档收口 master 的包。
- 最终准备检查须下载同一准确 master 的已验收附件，逐项核对 Actions archive digest、包内源码/版本/target MANIFEST、第三方许可证/来源、安装器与 helper/source/说明资产、RELEASE.json 和最终完整 SHA256SUMS；不得发布时重建替代。四目标 package 验收、附件晋升与公开发布分别记录。
- 到上述核验完成前，只能报告“范围冻结、发布准备中”，不能报告完整套件已核验或已发布。完成准备仍不授权创建 Draft Release、tag、上传/晋升或公开发布；保留下面的人工明确确认与真实发布后核验边界。

## 0.4.7 打包范围说明更正（2026-10-06）

[PR #26](https://github.com/ForceMind/V-UI/pull/26) 的候选 `0171f1f010f63bf73fde54c330309274e3d40f2b` 与正常合并 master `a21e31e1df4fd36a6994da780c29d051374279a9` 已分别完成八组/11 jobs/全部步骤验收。候选各组 attempt 1；主线除链路外七组 attempt 1，链路首次继承 GeoSite 下载连接重置后 unchanged-code [attempt 2](https://github.com/ForceMind/V-UI/actions/runs/37494753598/attempts/2) 成功。首次失败记录保留，不把重跑当作外部下载问题永久修复。

该 master 的 13 项最终资产已做独立字节/来源核验：四个目标 ZIP 合计 376,881,626 字节，Actions archive 摘要、包内源码/版本/target MANIFEST、许可证/来源、源码包、安装器/helper、RELEASE.json 和 SHA256SUMS 一致。此结论仍作为旧套件完整性证据保留；未执行包内软件，也未上传、晋升或发布。

核验同时发现打包 UI 在 `core === 'xray'` 警告中使用了全局“REALITY 未验收”的过时说法，MANIFEST 也仍只描述 VLESS/TCP/TLS。旧套件因此被后续修正候选取代，原因是发布展示质量，不是摘要不符或源码替换。修正仅明确 Xray REALITY 未验收/公开拒绝、保留 sing-box REALITY/Vision 有界验收及成功分支，并把累计导出范围指向 [COMPATIBILITY.md](COMPATIBILITY.md)。协议/编辑/导出行为、版本、核心 pin、应用 UDP 范围及 XHTTP/HTTPUpgrade 阻断均不改变。

本次修正仍须独立审查、准确候选八组、正常合并后的准确主线八组，以及该新主线自己的四目标最终套件核验。新增单元回归读取真实 `prepare()` 结果与合成构建 ZIP 的实际 MANIFEST；deployment 门槛也检查实际安装包清单。合成夹具不等于四目标真实包验收，旧通过不能冒充新通过；结果记录在对应 PR/发布准备证据中，不预填未来 SHA 或递归回写成功声明。上述新验收完成前仍为发布准备中，任何 tag、Draft Release、上传、晋升、公开发布或部署均须另行明确授权。

## 前置条件

1. 按依赖顺序审阅并合并前置PR；目标必须是当前默认分支的**准确40位HEAD提交**，不能给一个旧测试结果配新源码。
2. VERSION与main.py版本一致，CHANGELOG、兼容矩阵、证书和安装文档已更新。
3. 默认分支push会触发全部门槛。相同提交必须完成test、toclash、loopback、release、acme、oneclick、portable和docs八组工作流，**每组最新一次**都为completed/success。失败、取消、缺失或排队一律阻断。
4. One-command 任务生成 x86_64/glibc 发布套件；portable matrix 生成 ARM64/glibc 与两套 musl artifact。发布脚本只聚合同一 exact-head 提交且已通过验收的这些附件，不在发布时重建。
5. 检查原有tag/Release不存在；正式版本不覆盖、不悄悄替换已发布资产。

## 人工触发

在默认分支选择 `Publish verified V-UI release` 工作流，填写commit、version与完全匹配的 `RELEASE <version> <sha>` 确认文字。

`publish=false`为默认值：完成校验和资产上传后保留草稿；只有操作者明确设为true才公开。发布脚本先创建draft，上传全部资产并验证各自服务端digest，全部成功后才允许公开。中途失败保留草稿供审查，不误报已完成。

GitHub权限只在该人工工作流的发布job授予contents:write/actions:read；PR测试没有发布权限。输入经环境变量和参数校验，不拼接为任意shell代码。

## 发布资产

- vui-linux-x86_64-gnu.zip：经过同提交 one-click/systemd 验收的固定离线包。
- vui-linux-aarch64-gnu.zip：经过同提交 ARM64 原生 portable 验收的固定离线包。
- vui-linux-x86_64-musl.zip：经过同提交 Alpine/musl 原生 portable 验收的固定离线包。
- vui-linux-aarch64-musl.zip：经过同提交 ARM64 Alpine/musl 原生 portable 验收的固定离线包。
- install.sh、install_system.py：同提交安装入口和root初始化控制器。
- vui-source.zip：准确提交的源码。
- SHA256SUMS：全部固定资产的整文件摘要。
- RELEASE.json：版本、源码提交、release_id和平台元数据。
- RELEASE_NOTES.md：功能、安装、已知限制和运维说明。

发布脚本也验证包内manifest的源码与版本。远端artifact由签名下载URL取得，下载时不把GitHub授权头带给对象存储；完整artifact的digest必须与Actions元数据一致。

## 公开后检查

核对Release公开状态、tag目标、下载文件摘要和文档链接。可在独立临时 Linux 机器复核在线 `install.sh --version v0.4.7` 的自动目标选择路径；线上URL只有该版本确实发布后才存在。

外部CA域名验证、云安全组和真实用户网络需要用户自己的配置，不属于维护者自动取得的授权。不要把发布流程顺手变成登录真实VPS部署。

## 故障与撤回

上传失败时不直接重跑覆盖已有Release；先检查草稿，决定由操作者清理还是继续人工修复。已公开且有问题的版本应公开说明并发布新修订版本，不能偷偷改同一版本二进制。

默认分支变化会使目标提交检查失配，需明确选择新HEAD并等待它自己的全套测试。候选artifact过期则重新运行同提交的一键门槛，并核对其他最新结果；不能从未知备份恢复一个未验证的包。

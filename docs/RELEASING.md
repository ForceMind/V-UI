# 正式发布流程

正式Release是一次明确的公开写操作，不等同于PR、版本字符串或Actions候选附件。此流程只晋升经过验收的完整套件，不在发布时重新构建“另一份看似相同的包”。

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

核对Release公开状态、tag目标、下载文件摘要和文档链接。可在独立临时 Linux 机器复核在线 `install.sh --version v0.3.1` 的自动目标选择路径；线上URL只有该版本确实发布后才存在。

外部CA域名验证、云安全组和真实用户网络需要用户自己的配置，不属于维护者自动取得的授权。不要把发布流程顺手变成登录真实VPS部署。

## 故障与撤回

上传失败时不直接重跑覆盖已有Release；先检查草稿，决定由操作者清理还是继续人工修复。已公开且有问题的版本应公开说明并发布新修订版本，不能偷偷改同一版本二进制。

默认分支变化会使目标提交检查失配，需明确选择新HEAD并等待它自己的全套测试。候选artifact过期则重新运行同提交的一键门槛，并核对其他最新结果；不能从未知备份恢复一个未验证的包。

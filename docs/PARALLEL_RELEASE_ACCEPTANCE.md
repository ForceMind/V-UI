# 并行安装验收与制品身份契约

状态：P1核心已完成源码审查，准备Actions真实producer兼容检查；尚未接入并行矩阵或发布器。当前实现离线kit/receipt校验核心及假数据边界测试，并在既有oneclick构建后增加真实kit形状/复制smoke；不提升应用VERSION，不发布应用、不修改生产实例。上一个文档批次[PR141](https://github.com/ForceMind/V-UI/pull/141)已获明确普通dev→main合并授权并合入dev，[PR142](https://github.com/ForceMind/V-UI/pull/142)负责main同步；本P1代码不混入该纯文档批次，也不继承其合并许可。当前总顺序见[路线图](../ROADMAP.md)。

## 目标与分期

减少串行重复等待，同时保留现有新装和公开14/15/16/17/19/20/21升级的全部真实断言。同一准确SHA的kit只构建一次，之后由独立空白Actions runner消费，不在开发电脑运行产品构建或安装。

1. 当前交付：标准库实现的只读制品身份验证器、严格receipt数据契约和假包测试。可以检查已有kit、在显式指定的新路径准备相同字节的测试bundle/sidecar；不能据此声称安装测试成功。
2. 后续接线：一个build、新装和七个公开基线消费者，共九个oneclick job；相同输入在独立主机运行，fail-fast关闭、并行上限保守。不能把多个安装测试放在同一runner后台执行。
3. 后续发布资格：按角色逐项核对producer/consumer和实际检查，另立未来版本policy。旧v0.4.22例外保持，不能只改job数量、复用旧flag或重新发布22。

## 既有制品，保持原字节

使用现有`build_bundle.py`与`build_release_kit.py`生成的x86_64-gnu安装kit。当前精确集合为16项：producer的11个源码入口/helper、`vui-source.zip`、`RELEASE.json`、`RELEASE_NOTES.md`、`vui-linux-x86_64-gnu.zip`、`SHA256SUMS`。

验证器不能执行kit内Python、shell或二进制，不改原kit，不调用构建或安装脚本，不联网，不设置系统服务或权限。它验证消费来源和字节；真正安装、核心转发及数据恢复仍由原Actions用例证明。

### 消费验证

`verify_kit(kit, *, expected_source, candidate_version, expected_checksums_sha256, bundle_output=None)`必须使用调用者提供的完整SHA256SUMS文件摘要作为外部信任锚。消费者不能从自己刚下载的清单自算一个值，再把它当可信预期。

必须核对：

- 精确文件集合，普通文件且无目录、链接、特殊文件或额外成员；拒绝重复、缺失和路径伪装。
- 清单原字节摘要、每项流式SHA-256及清单集合；坏格式和重复条目拒绝。
- `RELEASE.json`和bundle `MANIFEST.json`的source/version/单目标及既有release/platform一致性，不猜测与现有合法格式冲突的新字段。
- 源码ZIP注释绑定完整SHA；源码入口/helper及发行说明与kit文件逐字节一致。安全Git目录可保留；复制对应文件必须是未加密、有界、无重复的普通文件。
- JSON拒绝重复键、坏UTF-8、NaN/Infinity和无界输入；ZIP元数据在读取内容前检查，不能为了校验而无界解压。

提供`inspect_producer_kit(kit, *, expected_source, candidate_version)`时，结果必须明确是生产者自检。它不能替代消费者的外部摘要信任锚。

### CLI入口

消费者使用标准库模块入口：

```sh
python -B -m scripts.release_acceptance verify-kit \
  --kit /tmp/vui-acceptance/kit \
  --source "$CANDIDATE_SOURCE" --version "$CANDIDATE_VERSION" \
  --expected-checksums-sha256 "$PRODUCER_CHECKSUMS_SHA256"
```

变量必须来自同一受信producer和准确候选，不能填入其他运行的输出。该命令不运行安装。生产者自检命令为`inspect-producer`，结果标识其不同的信任范围。

## 测试bundle与sidecar

现有真实新装测试首先读取`VUI_RELEASE_BUNDLE`旁的`.zip.sha256`。kit只含完整SHA256SUMS，因此消费者可显式指定`bundle_output`，在验证全部通过后把已验证bundle原字节复制到kit目录之外的新路径，并生成测试sidecar。

不重建、不向精确kit增加sidecar或receipt，不覆盖已有路径或跟随链接。文件先在本次独占创建的0700私有暂存目录中准备，再以无覆盖方式发布到调用者指定的新文件名；最终文件为0600数据文件。

最终文件名一旦发布，异常路径不再自动删除它：普通stat后再unlink不是原子所有权证明，可能误删并发替换的新对象。此时必须返回拒绝/非零退出，保留最终文件待核对，不产生成功身份；下游不得据这些残留文件继续安装，也不得自动重试覆盖。自动清理仅针对本次私有暂存namespace内的对象。

复制结束后、返回identity前再次核对整个kit目录和全部源FD身份；源文件变化或复制后hash不符都必须失败。每项清理或close即使失败，也要继续尝试其余自有资源，最后统一报告并保留原操作异常的因果关系，不能在首个错误处漏关后续FD。

## receipt是消费身份，不是测试绿灯

身份固定包含schema、repository、source_sha、version、target、release_id、checksums_sha256、bundle_sha256与validation_scope。receipt保留来源、版本、目标、摘要及scope，再绑定run_id、run_attempt、role及producer artifact ID/digest；release_id由版本和源SHA前缀确定，不重复写入receipt。

- schema当前为1，repository为`ForceMind/V-UI`，目标为`x86_64-gnu`。
- SHA使用规范小写hex，Git源为40位、文件摘要为64位；artifact digest为`sha256:`加64位hex。
- run、attempt、artifact ID必须为严格正整数，拒绝bool及隐式类型转换。
- role仅为build、fresh或七个固定`upgrade-<version>`；旧版本来源必须与原oneclick配方完全一致，不能混入其他旧SHA。
- build只接受明确的producer-inspection身份；fresh/upgrade只接受已使用外锚的consumer身份。validate_receipt按role确定期望scope，不由不可信receipt自行声明可信程度。
- fresh/build禁止旧版本字段；upgrade必须有正确的旧version/source；所有字段集合严格限定，无未知键或重复键。
- 校验时对比调用方独立提供的全部预期：source、version、run/attempt、role、producer ID/digest、清单和bundle摘要。不能只核receipt内部字段互相一致。

receipt不得含“安装已通过”或“job已成功”的假标志。它不能代替真实job、必要步骤、测试退出状态或实际数据断言。缺失、错角色、错来源或跨attempt不能晋升。

## 工作流与发布器接线前的硬边界

当前仅在oneclick组装kit后增加同job真实形状与隔离复制smoke；原新装、七旧版、环境、上传和安装断言逐字保持，不改publish、安装器或旧发布器。smoke不验证跨runner传输、不产生实际job/step资格receipt，也不等于并行矩阵已实现。实际并行接线前须完成独审及定向失败控制：

- producer artifact名称/身份绑定repository、run_id、run_attempt、SHA；consumer各有唯一角色和日志，消费同一套已验字节，不各自build。
- 新装和19/20/21的netlink、Mihomo及installed-core环境逐一保留；14–17保留各自原配方，不把它们扩大称为相同后置核心验证。
- 所有公开基线仍经真实匿名HTTPS安装，保留完整来源、digest、root闭包、每文件下载、账号/hash、数据库、节点、证书/绑定、订阅和授权比较。
- 保留空白主机前置检查、sudoers和本地目录身份保护、失败/回滚、ExitStack清理和原异常传播。未单独证明的“所有残留进程为零”不能声称已验收。
- 只晋升同一准确main的四目标已验制品，不在发布时重建。新policy逐角色核完整矩阵，不采用步骤名并集代替角色。
- 未来新产品版本仍需公开22到新版本的实际保数据资格。工具维护不升应用版本，不绕过helper对同版本假升级的拒绝。

## 元数据缺口处理

PR141的原安装job总览success，但GitHub的步骤API一度返回空列表，后续又逐步恢复；是否完整恢复必须由原严格检查器逐项确认。匹配GitHub digest的原始日志经独审证明准确候选的新装及七旧版实际用例全部运行通过；这能限定说明文档批次的安装功能执行情况，不能补造步骤数组或宣称原严格发布qualifier成功。

新发布资格不能把此诊断处理推广为默认放行路径。必要证据缺失时保持缺失，恢复证据或在明确原因后补验；不修改原测试断言或把缺失当success。

## 验收与仍需外部条件的项目

独审初轮复现最终路径竞争删除、copy期间源变化漏检和清理短路三项异常路径；修复后28项假包测试及原三个独立对抗场景复测通过，三项阻断关闭。核心当前仅获源码与假数据资格，真实producer制品兼容尚待Actions；新增smoke将校验真实manifest/source控制文件及复制原字节，不在开发电脑下载包。

本阶段只运行假文件/ZIP/JSON和临时目录的有意义边界单测及相关静态检查；产品构建、真实安装和并行矩阵资格只在Actions。浏览器不运行，Go迁移暂缓，不新增Secrets、OIDC、签名证书或网络权限。

当前组件通过不代表整个P1完成。接线及未来发布policy另有准确候选/主线资格；真实512MiB整机与24小时、手机及Tailscale接入仍需独立授权环境，不能用cgroup或短测代替。

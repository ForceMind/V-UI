# Mihomo YAML 离线兼容性与差异预览

这是路线图后续 YAML 工作的第一个有界组件：**本地库及 CLI，只读检查，不是任意 YAML 导入或合并器**。不接线上 API，不改变现有单 VLESS 上游导入、工作区保存或运行配置。不会下载 provider、执行输入对象、调用核心、解析网络地址、安装依赖、写入配置或应用结果。

## 使用现有本地环境

```sh
.venv/bin/python scripts/preview_mihomo.py candidate.yaml
.venv/bin/python scripts/preview_mihomo.py candidate.yaml --baseline previous.yaml
.venv/bin/python scripts/preview_mihomo.py candidate.yaml --baseline previous.yaml --format text
```

输入的最终文件组件必须是普通文件；最终组件为符号链接、目录、设备或 FIFO 时拒绝。中间目录可以是符号链接：这不是路径沙箱，不承诺限制父目录遍历。工具不接受 stdin 或远程 URL。只读文件前后核对元数据，检测到变化则拒绝，不承诺对恶意并发写入者提供事务快照。文件路径不会出现在错误结果中。

Python 入口为 `app.services.mihomo_preview.preview_mihomo(candidate, baseline=None)`，参数为 UTF-8 文本或 bytes，返回 JSON 可序列化的报告。解析失败抛出 `MihomoPreviewError`，只含固定错误代码和 candidate / baseline / pair 来源。CLI 成功输出报告时退出 0，即使报告包含不支持项或冲突；解析、I/O 或资源失败退出 2。**退出 0 不代表可导入、兼容或可应用。**

## 中文可读报告（本地后续阶段）

默认和显式 `--format json` 保持原 JSON 结构、格式与退出码；`--format text` 以 UTF-8（包括 C / ASCII locale 下的 stdout）输出固定中文摘要、区块变化、各类诊断解释及位置，方便人工检查，不增加导入或自动修复。位置用“第 2 项（索引 1）”同时展示从 1 开始的列表项及原 JSON 零基索引；**不是 YAML 行号**。诊断按事件计数，同一节点或规则可能有多条，不称为问题节点数量。最多 256 条位置，完整分类计数与省略数仍展示。

可读报告只由已验证的 v1 报告生成，库入口 `app.services.mihomo_preview_report.render_preview_text(report)` 不读取 YAML 或文件。子进程返回和展示入口均验证固定语义标记、准确字段形状、列表顺序、数量与位置范围、诊断分类及汇总一致性；允许词汇出现在错误字段也会拒绝。该检查仅证明报告满足结构契约，不证明原文、真实运行或合并可用。

报告不打印源节点名、域名、凭据、文件路径或隐藏区块名称；诊断解释仅建议检查命名、引用、顺序等，不让用户删掉未知字段来制造“可导入”。零诊断仍明确“不可应用”和真实核心未验。只读同样适用于基线文件，不写输出文件；如需保存报告，由调用者自行重定向 stdout。

**错误仍统一输出原固定 JSON，退出 2**，包括 `--format text` 下的解析、I/O、资源和参数错误；成功文本退出 0 也不代表兼容或可应用。选择器只接受完整 `--format json` 或 `--format text`，不接受缩写。

## 报告究竟证明什么

- `proxies`：逐项使用既有严格上游 profile 校验，计数单个节点是否可由该模型表达。不会输出节点名称、主机、UUID 或材料；可表达计数不代表整份配置、引用关系或实际链路合格。
- `proxy-groups`：仅解释 type 为 select、字段限于 name / type / proxies 且成员列表有效的有限形状；名称独立检查，因此识别形状数量不代表完整分组合格。检查名称冲突、重复成员、未解析引用及受支持 select 子图的全部环成员。内建目标仅识别 DIRECT / REJECT。其他组形状不假装兼容；未识别的内建目标可能报告未解析，而不是断言 Mihomo 不支持它。
- `rules`：仅解释规范字面 DOMAIN / DOMAIN-SUFFIX、规范网络 IP-CIDR / IP-CIDR6，以及 MATCH。参数数量、空白、大小写、域名和网络表示必须满足本工具的窄合同，不自动纠正。计数规则语法、同一精确谓词的目标冲突、重复规则、MATCH 后规则及目标引用；不判断不同谓词覆盖关系、DNS 语义、规则顺序转换或逻辑规则。这些语法计数**不是工作区设置可无损表达的数量**。
- `mode`：仅识别 rule / global / direct 的拼写，不把它们转成 V-UI 工作区模式。
- DNS、TUN、provider 等已知区块明确为未解释；不会执行或访问其中的地址。其他区块包括凭据 / 控制器设置，只提供聚合的 withheld 数量，不回显其键、值或变化。

提供 baseline 时，报告只对列出的固定区块给出 added / removed / changed / same / absent；列表顺序保留，字典键顺序不构成差异，false 与 0 不相等。它比较的是经严格加载后的结构，不是原文、语义等价或服务端修订；隐藏区块不比较，不能以全部可见项 same 推断完整配置相同。baseline 同样必须满足严格解析和规模界限，但其语义诊断不作为另一份报告输出。

输出只有固定词汇、布尔、数量和从 0 开始的位置，不含输入值、原文片段、路径、名称、密码、令牌或输入摘要。即使子进程返回畸形成功结果，父进程也再次按固定词汇验证，拒绝回显任意字符串。诊断最多展示前 256 条，完整各类计数和省略条数仍保留。

`can_apply` 始终 false，`runtime_compatibility` 始终 not_tested，`merge_equivalence` 始终 not_proven。无诊断也不改变这些标记。

## 输入与资源界限

沿用已有、未修改的隔离上游解析器和现有 PyYAML 依赖：每份输入最多 1 MiB、32768 tokens、16 层嵌套、每字符串 8192 UTF-8 bytes、64 位有符号整数；每份最多 128 个节点、128 个组、2048 条规则。候选及可选 baseline 总输入最多 2 MiB，在同一个独立解释器处理。

隔离子进程使用既有 128 MiB 地址空间、2 秒 CPU / 父进程墙钟解析期限、256 KiB 输出上限；资源约束不可用时失败，不回退成无限制解析。最多同时两个本工具解析子进程，结束 / 失败按既有 bounded worker 清理并回收。父进程的文件读取和等待使用者提供文件不属于子进程解析时限；Linux 资源限制不是整机内存资格。

重复键、非字符串键、多文档、任意 tag / directive、anchor / alias / merge、控制字符和过深 / 过大内容均拒绝。额外拒绝 YAML 1.1 的歧义裸字面，如 yes / OFF、非规范大小写布尔、八进制 / 十六进制 / 六十进制时数写法、时间戳及不同解析规则可能歧解的指数。需要字符串时须明确加引号；既有加载器对 NaN / Infinity 一类拼写即使加引号仍拒绝，该历史严格边界未放宽。

## 本地验证和未验收范围

```sh
PYTHONPATH=.:tests .venv/bin/python -m unittest test_mihomo_preview test_mihomo_preview_report -v
```

合成输入覆盖结构、差异、隐私、引用环、冲突、复杂度界限、恶意 YAML、畸形子进程响应、限流和真实 CLI 普通文件路径。后续可读报告测试另覆盖默认 JSON 字节一致、固定解释、零诊断边界、从 1 开始的项号与原索引、诊断省略、文件不变及严格报告拒绝。只使用临时文件和假节点。原上游导入与工作区控制另行回归。

不把这些证据称为真实 Mihomo 配置加载、链路、浏览器、任意配置合并或实际升级 / 部署验收。本阶段没有修改 VERSION、固定核心 / 依赖或网络安全设置；没有外部账户、远程仓库操作或新会话。准确远端 CI、外部独审及未来正式整合均未执行。

# V-UI 0.4.22

## 用户改变

修复维护授权页面的普通分享请求带实例/内容修订条件时，被查询参数白名单错误拒绝为422的问题。既有分享端点现在接受这两个已支持的条件，继续在同一节点对象上核对实例及内容；旧无条件和仅实例条件调用保持兼容。

未知、重复和含凭据的查询参数、权限不足及来源不匹配仍会拒绝。节点协议、UUID/密码、账户、订阅、证书、代理/中转开关、固定核心和安装器行为保持。

## 升级建议

当前稳定版为[v0.4.22](https://github.com/ForceMind/V-UI/releases/tag/v0.4.22)。从旧版升级前先在授权的私有位置备份并核对恢复条件；沿用已有受管安装的升级入口，不删除数据库、材料或重新初始化账号。操作方法见[安装指南](INSTALLATION.md)和[受管操作](MANAGED_OPERATIONS.md)。

本次没有新增产品数据迁移；既有迁移及恢复规则继续适用。真实安装已验证公开14/15/16/17/19/20/21到22的保数据升级，21已有的内容修订字段保持，较旧版本按原契约补齐派生字段。不能据此推断任意未知安装、损坏数据或任意更旧版本都已验收。

## 发布和入口证据

| 对象 | 准确身份和证据 |
| --- | --- |
| 正式应用 | source `558687a76cd29f4e4735284926eb05bf6664472e`；[发布流程42398527616](https://github.com/ForceMind/V-UI/actions/runs/42398527616)；[Release v0.4.22](https://github.com/ForceMind/V-UI/releases/tag/v0.4.22)，ID437741132 |
| 应用验收 | 候选/dev/main各自九组/12 jobs、同源四目标、新装和七旧版升级通过；[main实际安装与升级42395305687](https://github.com/ForceMind/V-UI/actions/runs/42395305687) |
| 短入口 | PR139→dev、PR140→main；入口main `39181e0cdb9643422a19049df942b208f876faf4` 自身及候选/dev九组/12 jobs通过，应用source保持558687a |
| 公开入口安装 | [实际main短命令42405651147](https://github.com/ForceMind/V-UI/actions/runs/42405651147)；[完整安装42405651162](https://github.com/ForceMind/V-UI/actions/runs/42405651162) |

匿名读回已核tag/latest、19项资产digest、真实安装器/完整校验清单/发布元数据，旧21资产保持。全部产品构建与真实安装验收在GitHub Actions完成；新增回归经过实际维护授权/Bearer与ASGI/临时SQLite链路，没有以中间件mock代替授权检查。完整证据及早期失败保留在[本版记录](MAINTENANCE_SHARE_0422.md)。

## 已知限制

- 浏览器未验证；非浏览器、合成、真实核心和安装结果不能替代视觉/交互或手机实机验收。
- 有效中转仍按既有TCP边界执行，未扩大应用UDP/TUN或新增协议/平台支持，详见[兼容矩阵](COMPATIBILITY.md)。
- 发布应用source与后续入口/文档提交不同；不要用入口SHA重新标记或覆盖22资产。
- 服务或网络完全不可达时，远程维护API不能替代已授权的本机终端恢复；未知提交不自动重试。

旧版本说明与当时的验收边界见[历史索引](history/README.md)，原发布说明可从[固定提交39181e0的全文](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/RELEASE_NOTES.md)读取。

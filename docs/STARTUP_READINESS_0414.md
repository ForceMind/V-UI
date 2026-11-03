# v0.4.14：启动等待与回滚前诊断

## 批准与边界

2026-11-03 用户批准本版开发、独立审查、正常dev→main合并、公开发布和短入口更新。构建与真实安装验收全部在GitHub Actions，不做浏览器测试。版本分支release/v0.4.14从dev 5a959b8创建并快进准确main `f81ce5e04aadee8f8e8d53d8b1f4cfc53e1b0f3f`；原工作区WIP和xcrun_db保留。公开稳定版仍为v0.4.13，本候选未发布；不覆盖旧tag/Release/资产，不登录用户VPS、不代同意CA、不改用户安全设置。

## 实际差异与目标行为

用户VPS日志为systemd Started后11秒安装器停止回滚，无程序异常退出记录。只读计时确认verify_payload=2.59秒、verify_runtime=7.57秒，合计10.16秒，已超过旧40次×0.25秒的约10秒连接等待，尚未计模块/数据库/HTTPS初始化。已证实等待窗口不足；没有把所有可能的启动失败都归成慢启动。

面板启动采用最长120秒monotonic预算，从启动请求前开始计算。systemd面板显式异步提交，不把Type=simple active当作HTTP就绪；socket仍同步。connect、TLS握手、发送、响应头读取和服务状态探测都受剩余预算约束，不因分段/慢读无限延长。真实TLS链、域名与HTTP401鉴权验证保持；TLS错误或非401不能被重试掩盖。明确退出快速失败，activating/auto-restart/待执行job不误判；恢复旧面板同样进行有界就绪检查。

失败回滚前采集本次启动时间范围内的有界服务状态与journal事件，先脱敏再截断；未知正文不原样输出，不读取私钥、环境凭据或订阅token。诊断自身超时、过大、权限错误和解析异常不能覆盖原异常或阻止恢复。配置、账号、节点、订阅、会话、证书绑定、CA重试冷却和完整性校验保持，不通过删库、重置密码、跳TLS或跳文件校验制造成功。

## 文件与发布门禁

实现限定scripts/install_system.py、scripts/service_support.py及相关startup、恢复、真实安装测试；版本/文档/发布门禁随批次更新。新startup_no_browser仅适用0.4.14、固定上述f81基线及精确允许文件；main.py和install.sh只能版本替换，builder保持原字节，应用/前端/证书验证/依赖/核心变化拒绝。旧v0.4.11/12/13例外继续版本封闭，不为新版本借用；仅明列浏览器步骤可skip，其余准确提交/最新attempt/jobs/steps、四目标及实际安装门槛不放宽。

## 验收与进度

最小验证：fake clock证明>10秒才就绪成功、120秒截止、慢TLS/慢头读取预算、确定退出/待执行/重启状态、固定HTTP401、异常脱敏和诊断先于恢复。真实Actions对原始已验包安装，使用严格空白hosted CI fixture制造>10秒启动、明确退出和超时；断言真实TLS/API就绪、诊断/回滚和数据保持。延迟fixture必须实际落入health等待期间，不能只让同步systemctl在ExecStartPre中等待后再启动原10秒计时。

当前实现阶段，候选/dev/main、公开套件和最终实际main短命令分别取得自己的证据，不继承旧绿灯。最终提交/结果在承载PR记录，详细临时状态在/private/tmp/vui-startup-0414/state.json。浏览器未验证。

准备阶段：新发布范围与旧例外/准确job门禁30项纯合成测试通过，无依赖安装、本机Linux构建或真实安装。

实现阶段：15项startup预算/状态/真实HTTPResponse慢头解析合成，加恢复、服务控制、交互既有检查，共48项通过；1项既有SQLAlchemy绑定恢复测试因本地未安装依赖未执行，留完整Actions，不安装本机依赖。五个实现/测试文件及新发布门禁已独立静态审查，无剩余确定阻断；候选提交身份和真实Linux结果仍待分别绑定。

真实CI fixture仅在产品>=0.4.14、GitHub hosted/本仓库/显式安装测试/非低资源/空账号组及无预存fixture路径时创建root-owned dropin。systemd面板异步提交后ExecStartPre12秒实际落入health预算；升级演练one-shot exit23和sleep130（TimeoutStartSec150）验证提前失败/120秒截止、诊断先于恢复、配置指针和会话/代理数据保持。应用包和冻结启动器不改；公开0.4.13用例不启这些新fixture。真实结果尚待Actions，不把合成或静态审查称为真实安装通过。

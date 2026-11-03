# v0.4.14：启动等待与回滚前诊断

## 批准与边界

2026-11-03 用户批准本版开发、独立审查、正常dev→main合并、公开发布和短入口更新。构建与真实安装验收全部在GitHub Actions，不做浏览器测试。版本分支release/v0.4.14从dev 5a959b8创建并快进准确main `f81ce5e04aadee8f8e8d53d8b1f4cfc53e1b0f3f`；原工作区WIP和xcrun_db保留。以下开发阶段记录按当时事实保留；v0.4.14已公开，短入口后续自身资格仍待验收；不覆盖旧tag/Release/资产，不登录用户VPS、不代同意CA、不改用户安全设置。

## 实际差异与目标行为

用户VPS日志为systemd Started后11秒安装器停止回滚，无程序异常退出记录。只读计时确认verify_payload=2.59秒、verify_runtime=7.57秒，合计10.16秒，已超过旧40次×0.25秒的约10秒连接等待，尚未计模块/数据库/HTTPS初始化。已证实等待窗口不足；没有把所有可能的启动失败都归成慢启动。

面板启动采用最长120秒monotonic预算，从启动请求前开始计算。systemd面板显式异步提交，不把Type=simple active当作HTTP就绪；socket仍同步。connect、TLS握手、发送、响应头读取和服务状态探测都受剩余预算约束，不因分段/慢读无限延长。真实TLS链、域名与HTTP401鉴权验证保持；TLS错误或非401不能被重试掩盖。明确退出快速失败，activating/auto-restart/待执行job不误判；恢复旧面板同样进行有界就绪检查。

失败回滚前采集本次启动时间范围内的有界服务状态与journal事件，先脱敏再截断；未知正文不原样输出，不读取私钥、环境凭据或订阅token。诊断自身超时、过大、权限错误和解析异常不能覆盖原异常或阻止恢复。配置、账号、节点、订阅、会话、证书绑定、CA重试冷却和完整性校验保持，不通过删库、重置密码、跳TLS或跳文件校验制造成功。

## 文件与发布门禁

实现限定scripts/install_system.py、scripts/service_support.py及相关startup、恢复、真实安装测试；版本/文档/发布门禁随批次更新。新startup_no_browser仅适用0.4.14、固定上述f81基线及精确允许文件；main.py和install.sh只能版本替换，builder保持原字节，应用/前端/证书验证/依赖/核心变化拒绝。旧v0.4.11/12/13例外继续版本封闭，不为新版本借用；仅明列浏览器步骤可skip，其余准确提交/最新attempt/jobs/steps、四目标及实际安装门槛不放宽。

## 验收与进度

最小验证：fake clock证明>10秒才就绪成功、120秒截止、慢TLS/慢头读取预算、确定退出/待执行/重启状态、固定HTTP401、异常脱敏和诊断先于恢复。真实Actions对原始已验包安装，使用严格空白hosted CI fixture制造>10秒启动、明确退出和超时；断言真实TLS/API就绪、诊断/回滚和数据保持。延迟fixture必须实际落入health等待期间，不能只让同步systemctl在ExecStartPre中等待后再启动原10秒计时。

候选/dev/main、公开套件和最终实际main短命令分别取得自己的证据，不继承旧绿灯。最终提交/结果在承载PR记录，详细临时状态在/private/tmp/vui-startup-0414/state.json。浏览器未验证。

准备阶段：新发布范围与旧例外/准确job门禁30项纯合成测试通过，无依赖安装、本机Linux构建或真实安装。

实现阶段：15项startup预算/状态/真实HTTPResponse慢头解析合成，加恢复、服务控制、交互既有检查，共48项通过；1项既有SQLAlchemy绑定恢复测试因本地未安装依赖未执行，留完整Actions，不安装本机依赖。五个实现/测试文件及新发布门禁已独立静态审查，无剩余确定阻断；候选提交身份和真实Linux结果仍待分别绑定。

真实CI fixture仅在产品>=0.4.14、GitHub hosted/本仓库/显式安装测试/非低资源/空账号组及无预存fixture路径时创建root-owned dropin。systemd面板异步提交后ExecStartPre12秒实际落入health预算；升级演练one-shot exit23和sleep130（TimeoutStartSec150）验证提前失败/120秒截止、诊断先于恢复、配置指针和会话/代理数据保持。应用包和冻结启动器不改；公开0.4.13用例不启这些新fixture。真实结果尚待Actions，不把合成或静态审查称为真实安装通过。

独审绑定首提交7c8637d时发现CI fixture缺本仓库断言（已有hosted判定但离线路径可绕过旧helper的repo gate）。已将GITHUB_ACTIONS/RUNNER_ENVIRONMENT/GITHUB_REPOSITORY三重检查放到ExitStack及cleanup注册之前，拒绝时不进行fixture写入或清理。生产逻辑未改，首提交尚未创建PR/触发完整验收，不授予资格。

首个实际候选fda7a16的oneclick run41573495065/job125711588177（99.763秒）在初装慢启动、登录及常规升级后，测试直接读取私有CURRENT.json时PermissionError失败，尚未进入exit/timeout回归。已改为sudo -u v-ui只读该指针再比较；不改用户/目录/文件权限、不改生产逻辑或验收断言。原失败日志保留，新候选必须重新完整Actions，不继承部分成功结果。

候选24c1695的oneclick run41574122520/job125713574932在228.067秒失败：初装12秒慢启动成功；exit23演练已记录failed/exit-code及Control process exited status23、且回滚成功，但Job空属性未显示，保守判断一直等待120秒，未满足明确退出及时失败断言。按[systemd官方show说明](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html#show%20%5BPATTERN%E2%80%A6%7CJOB%E2%80%A6%5D)改为--all保留空属性；仅显式空/0是无job，缺失或未知不误判。新增7种表示与终态联动回归，16项startup+2项service通过；真实退出/超时断言不改，新候选仍须完整Actions。旧源码和失败日志保留，不以重试制造成功。

## 正式发布与短入口切换

最终独审候选96e00d78b5ed8bf296dee67c5ed84f3ff460f642、正常dev1c9e1b97ee6dcd4a061e8bd36ee22415479c0475及main `b0cc174d04d05e61277075412586f0ba0dd231db` 的tree均为5d89d88c01fe8bd1b75207439495acfe0d705081。候选/main各九组12jobs首次通过，dev push+PR共18组24jobs首次通过；各12/24个明列浏览器步骤skip，其他步骤成功。四目标每组artifact来源/大小/真实digest与上传日志核验一致，未本地下载Linux包。

main ordinary2457项=2227通过+230环境skip，288.401秒，真实core/parser各1项通过。实际oneclick run41578259488/job125726738361为333.194秒，12秒延迟后真实TLS/HTTP401成功；exit23总操作15.340秒，120秒启动deadline+恢复总操作135.026秒。诊断在恢复前，config/CURRENT/session/proxy数据保持；既有ready:false、变更、端口冲突及真实TLS失败恢复均通过。

[正式发布run41579671967](https://github.com/ForceMind/V-UI/actions/runs/41579671967)首次成功，[v0.4.14](https://github.com/ForceMind/V-UI/releases/tag/v0.4.14)于2026-11-03 04:43:24 UTC公开19资产，tag/target为上述准确main，仅晋升该提交受验附件，不重建。匿名install.sh逐字节匹配源，完整SHA256SUMS按18资产真实digest重建一致。install.sh SHA256=`019308de74c19aa4e66234cc32d89ddc446a447f565c7ff3c98fcbf9ffae8886`；整个SHA256SUMS SHA256=`fc4656d4c07d86cf84be66fb503e67ef697ff5b04da171fd33c622537094ae8a`。旧v0.4.13 tag/Release正文/19资产身份、大小、digest保持（下载计数除外）。

公开后只把setup默认版本/两摘要、short-install冻结源和契约测试切至公开M，应用、安装器、依赖和核心原字节保持。该bootstrap候选/dev/main和用户实际main短命令仍须各自准确资格，最终提交和结果在承载PR记录，避免只补最后SHA制造无穷文档验收循环。浏览器未验证，未部署用户VPS或代申请真实CA证书。

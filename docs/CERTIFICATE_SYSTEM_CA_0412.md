# v0.4.12：系统 CA 加载兼容修复

## 批准范围与状态

用户已明确批准修复并公开发布 v0.4.12，独立审查、正常 dev→main 合并、公开后更新真实摘要的短安装入口；保留原配置、数据和旧版资产。全部构建与实际安装验收只在 GitHub Actions，不做浏览器测试。

从 dev 24fcc28e5b5ef9a2622b46ae203a41fc277f7ce2 创建 release/v0.4.12，再快进包含稳定 main 4f24e4ce12a5dbe4fbd2da745a936734f82121f6，作为本版固定发布范围基线。当前源码为0.4.12候选，短入口继续消费公开0.4.11，公开新版本后才写入真实资产摘要。未覆盖旧tag/Release/资产。

## 已证实原因与契约

可移植Python默认cafile不存在、capath=/etc/ssl/certs时，ssl.create_default_context().get_ca_certs()可枚举0根；同一系统显式加载Ubuntu bundle有120根。目录CA惰性加载，原代码据空枚举构造验证Store导致INVALID_CERTIFICATE_MATERIAL。诊断只统计公开根，不读取用户私钥或重新签发。

新增系统根加载：默认已加载根优先；为空时使用OpenSSL/支持的Linux系统bundle或hash-indexed capath。显式trusted_roots（包括空值）与存在的SSL环境设置均定义独立来源，不静默回退扩大信任。未知CA、错误域名、有效期、密钥配对、证书链与材料上限仍严格验证；不信任服务器chain作为根，不禁用TLS，不联网下载根，不改变依赖/核心。

错误区分CERTIFICATE_CA_STORE_UNAVAILABLE、CERTIFICATE_CHAIN_VERIFICATION_FAILED和既有材料/密钥/域名/时间错误；对外不返回PEM、私钥或原始上游异常。沿用既有失败事务，不删除数据库或改变用户、订阅、节点、证书绑定与旧运行版本恢复。

## 验收与发布边界

单元覆盖capath空枚举+系统bundle、显式来源隔离、空/坏/缺失仓库、错误CA、域名/配对/有效期及错误脱敏。真实Certbot→Pebble HTTP-01新增默认根（不传trusted_roots）及hash目录回归。

实际oneclick安装后的portable/runtime+已验payload以非root v-ui运行系统CA回归：实际默认根拒绝隔离假CA→只在空白GitHub hosted CI临时加入公开假根后通过→错误域名拒绝→完整恢复CA bundle bytes/mode/owner/hash后再次拒绝；不读取真实密钥或触碰用户VPS。另单列capath-only context条件注入，不把它冒充无mock复现。原服务、配置、认证会话与代理草稿保持断言保留。

发布采用仅限0.4.12的CA兼容修复非浏览器例外，固定上述main基线；只允许material.py、对应测试、版本和发布说明；main.py与install.sh逐字节仅允许版本替换，builder、其他应用、前端、依赖和核心改动拒绝。只容许既有明列12个浏览器步骤skip，其余准确commit/attempt/jobs/steps与四目标产物必须通过。旧0.4.11 installer-only例外保持版本封闭，不用于本次应用证书修改。

准确候选、dev/main、公开Release和最终短入口分别记录证据；结果在承载PR及本记录追加，不预填通过。浏览器未验证。公网CA签发、用户VPS和本机Linux构建/安装均未执行。

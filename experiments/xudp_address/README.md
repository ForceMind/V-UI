# D2 connected-XUDP 地址隔离实验

状态：源码编译与 Go 对照、未改源码真实双跳 RED 均已实际执行；进程账本的客户端期望分类失败，补丁真实双跳尚未执行。产品仍是已验 D1 TCP-only；PR #48 原失败不可覆盖或授予新资格。

## 问题与假设

原候选 `49ec3f8269e0b250a50fe9f9a1d0d56f0e560441` 的真实 CI 中，22 条 guarded 初始回复均把 UDP 应用端口错标成海外 VLESS TCP 监听端口。裸 raw-XUDP 正对照可在同关联把后续帧送到另一个受控私网目标。开启绑定和关闭绑定分别暴露兼容性与安全问题。

`XUDPConn` 嵌入外层 `net.Conn`，未覆写 `RemoteAddr`；sing-box 的 `udp_connect` 路由把它交给 `NewUnbindPacketConn`，捕获了外层传输地址。最小假设是在 XUDP 对象返回构造时冻结的逻辑应用目标；不修改协议帧、路由选择或固定目标写入。

本实验仅给 `sing-vmess/xudp.go` 增加一个地址方法。字面 IP 返回新的 UDPAddr 值，未解析域名保持 Socksaddr，不伪造解析结果。D2 仅接受经过全答案检查后实际选择的 IPv4 目标；未解析域名的 connected 包装器行为不在本次开放范围。

## 固定输入与隔离

- V-UI 起点：上述原候选及其未改的 17 个 preflight 方法和 helper；实验 runner 复用它们，不放宽原断言。
- sing-box 1.14.2 源码：`af6e64c3b69e6132ebaee0e1a3d24e93903f6709`。
- sing-vmess 0.2.8 源码：`31ec11e8790c48a4bf11711806a61e929ee6b89d`。
- 原 core go.mod/go.sum 与 sing `87c33f17688f` 保留。两对照仅以本地 replace 使用对应精确 sing-vmess 源码，不升级依赖。
- 已有 GitHub Ubuntu 24.04 runner 的 Go 1.25.14；`GOTOOLCHAIN=local`、CGO=0、linux/amd64、无可选 build tags、`-trimpath -buildvcs=false -mod=readonly`。缺工具就失败，不自动下载替换工具链。
- baseline 与 patched 使用不同 `1.14.2-vui-xudp-...-experiment-<VUI SHA>` 标记、独立目录及 SHA-256。它们均不是官方核心包。
- 只有国内 fixture 进程可使用源码实验二进制；重启也必须保持该规则。海外 sing-box、sing-box 设备、Mihomo 设备仍是原 checksum-pinned 官方版本。
- 不修改 `scripts/fetch_test_cores.py`、`app/`、安装器、bundle/release 工作流、VERSION、产品配置或主线。独立 push 工作流只上传日志、散列、模块图和逐项送达账本；禁止上传二进制、证书私钥或完整运行配置。

## 真实语义与风险

connected wrapper 对后续不同目的地址的写操作忽略新地址，payload 仍发向初始已审核目标。本实验必须实际记录这一点：禁止目标不可收到数据，但不能把行为描述成“逐包重新审核后拒绝原帧”。它可能使原本想发往 B 的内容发往 A，因此不能在产品交互或能力说明中隐藏这项语义，也不能据此宣称任意 UDP 客户端兼容。

裸 `ReadFrom` / `ReadPacket`、`WriteTo` / `WritePacket` 仍保留逐包地址；最小方法不是裸 UDP 安全修复。必须保留能改目标的裸对照。共享 XUDP 也被 VMess 的 unbound 路径使用：本次不修改 V-UI 的 VMess 产品路径、不授予该共享影响面运行资格。任何旧调用者依赖外层 `RemoteAddr` 的情况都应记录并阻止无审查采用。

## 验收顺序和停止条件

1. 两源树准确 SHA 和 clean；官方 Go 工具链；补丁只改 xudp.go。验证 module sums、编译参数、源码与二进制哈希。
2. 在核心真实依赖图运行 5 项局部 Go 测试。未改源码准确 3 RED / 2 GREEN、8 个子例终态、7 条确切地址/tuple 失败原因；补丁 5 GREEN。编译失败、同名任意错误、缺测试、跳过或额外失败均不是合格 RED。关闭 GOWORK，验证固定 sing 版本和唯一 replace，比较两阶段实际选择的完整依赖图。
3. 只替换国内端运行原 17 项。未改源码必须重现 5 通过、12 失败方法/22 个准确端口失败，无错误/跳过；返回端口逐条等于对应海外监听端口。
4. 补丁必须通过全部原 17 项，并另验两个官方客户端的真实 TCP HTTPS 双跳及“同域名、同端口、仅 DNS 答案改变”独立向量，共 20 项。原 test13 同时改变端口，不能代替新增向量。IP、port、payload 每次都检查；1-byte/1200-byte、raw 帧、受控 DNS 全答案和重绑定、私网/自身/上游/低端口、认证/TLS/停机恢复、空闲清理均须越过初始正向实际执行。
5. 比较各进程角色的实际二进制散列，证明海外与设备未被补丁一并替换。保留每一次初始失败与后续修复，不能通过改成端口相等、移除绑定、删断言、重试业务包或隐藏日志过关。
6. 如新失败显示另一独立固定核心性质，保留证据并报告；不得无限扩大补丁、协议、pin、系统权限或产品范围。即使全部实验通过，只能说隔离源码假设获得证据；产品采用、全目标构建及准确候选/主线资格另议。

## 参考

- [原 D2 Draft 与完整失败账本](https://github.com/ForceMind/V-UI/pull/48)
- [固定 core 的 connected 路由](https://github.com/SagerNet/sing-box/blob/af6e64c3b69e6132ebaee0e1a3d24e93903f6709/route/conn.go#L164-L200)
- [固定 XUDP 类型](https://github.com/SagerNet/sing-vmess/blob/31ec11e8790c48a4bf11711806a61e929ee6b89d/xudp.go#L18-L39)
- [已核官方 runner 工具清单](https://github.com/actions/runner-images/blob/ubuntu24/20261012.334/images/ubuntu/Ubuntu2404-Readme.md)

## 实验失败账本

- 候选 `31540bd22231283ca1d2faf3874a4409820dfd79`，[run 38941756173](https://github.com/ForceMind/V-UI/actions/runs/38941756173)，attempt 1：40 项控制测试通过，两版官方源码均成功编译；Go 基线 3 RED / 2 GREEN、补丁 5 GREEN。证据核验器把 wrapper 实际输出 `[::ffff:127.0.0.2]:24443` 错写成普通 IPv4，因而在两个预期 tuple 失败的文本分类处拒绝。原始端口仍严格等于错误的外层 TCP 端口，此错误未改变 Go 断言或补丁。两个真实运行步骤未执行，不能据此获得双跳资格。
- 首轮 artifact `12062903149`，ZIP SHA-256 `333979f3b35b162612c88ec6570fcd66f92106db331818968fdd4b4eb8cba38b`；15 项内部条目散列均核实，两版归一化模块图逐字节相同，无二进制。原样 Go JSONL/退出状态保存于 `fixtures/run_38941756173/`，测试以原 manifest 摘要锁定，不能只用核验器自身常量合成预期。
- 修复仅把地址文本按实际 IP 规范化（IPv4-mapped 与相应 IPv4 等价），端口、目标、七个具体断言名称与数量保持严格。变 IP、变端口、任意失败原因、缺子例均拒绝。新候选必须重新编译并完整运行 RED/GREEN 及双跳；首轮失败不重写、不作成功资格。

- 候选 `93856d1da922fd765b5a9a6861ac5c1a088185f8`，[run 38943133607](https://github.com/ForceMind/V-UI/actions/runs/38943133607)，attempt 1：43 项控制测试、两版编译与严格 Go RED/GREEN 通过。真实基线 17 项在 21.692 秒内得到 5 PASS、12 FAIL / 22 个准确原端口失败；79 条进程账本只有 test10 客户端身份的预设分类错位：原 fixture 明确启动 sing-box，分类器却因方法名没有 singbox 而推成 Mihomo。未改运行过程、原例和二进制；补丁步骤未执行，不能推断其结果。
- 第二轮 artifact `12062854903`，ZIP SHA-256 `fe78c991a0cbab37c76d5662c868c24e1d1c972b0d299f8134bca5b3dd8d4138`；99 项内部摘要均核实。修复须将所有 case 的客户端角色冻结为显式映射，禁止通过放宽角色、散列、重启次数或进程数量来通过。新候选完整重走两版编译及运行对照。

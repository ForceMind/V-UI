# PR #25 XHTTP 刻画收口（产品 0.4.7）

## 准确提交与结论

[PR #25](https://github.com/ForceMind/V-UI/pull/25) 已在 2026-10-06 正常合并。这里只记录以下准确提交的验收，不替代后续最终文档或包的验收：

- 独立审查后的最终候选：[`f7996c579418d1ffe5df06fc12677cf22a42e034`](https://github.com/ForceMind/V-UI/commit/f7996c579418d1ffe5df06fc12677cf22a42e034)，parent `77c264ab35a0b7a6217638cf9decccae178fd7ea`
- 正常签名 master：[`66dbe70cfa86fe140fa1b69d3896244ab17a0e3c`](https://github.com/ForceMind/V-UI/commit/66dbe70cfa86fe140fa1b69d3896244ab17a0e3c)
- 候选与 master 相同 reviewed tree：`80ad474cfdd159dcc603a14b4a1c9d24427494e7`
- master parents：已验收 REALITY/Vision `6b049262457b259c602c5e74be296ef52780c462` 与最终候选 `f7996c579418d1ffe5df06fc12677cf22a42e034`

最终候选与 master 各八组工作流、11 个 job、全部步骤均为 attempt 1 completed/success。产品 `VERSION` / `main.py` 仍为 **0.4.7**；0.4.8 只是 XHTTP 路线刻画标识。此阶段只改测试/文档，未增加生产协议、UI 或公共客户端导出；XHTTP/HTTPUpgrade 公开导出继续明确拒绝。

## 八组工作流

| 工作流 | 最终候选 f7996c5 | 最终 master 66dbe70c |
| --- | --- | --- |
| Documents and release contracts | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989220) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087710) |
| Test V-UI | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989345/job/112359867898) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087800/job/112363697185) |
| Real loopback proxy and DNS chain | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989214/job/112359867474) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087682/job/112363697299) |
| ToClash reference and export verification | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989180) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087576) |
| ACME certificate acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989352) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087529) |
| One-command installation acceptance | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989173) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087553) |
| Portable Linux runtime matrix | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989268) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087579) |
| Selected release deployment gates | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37489989306) | [attempt 1 成功](https://github.com/ForceMind/V-UI/actions/runs/37491087582) |

Portable 含四个 job；x86_64/glibc 由一键套件贡献，ARM64/glibc 与两个 musl 目标由 portable 贡献。CI 构建/安装/临时 deployment 验收不表示最终归档的独立下载核验、附件晋升或生产部署。

## 实际通过的刻画

两端链路门槛各执行 **101 项**，另执行独立 HY2 托管证书 8 项与 TUIC 托管证书 8 项。两端 Test 默认各发现 496 项，其中实际执行 375 项、121 项为明确环境 skip；真实核心与 Chromium 由单独激活步骤验收，不能将默认 skip 算作 runtime 通过。继承 REALITY 的创建、取消、编辑、刷新、再打开、独立 UUID/short ID 替换、导出、非法输入和损坏后停机恢复均保留。

XHTTP 唯一正向 profile 是固定官方 Xray 26.3.27 服务端 → Mihomo 1.19.32 原生 YAML 和实际 URI provider，单假 UUID、空 flow、显式 `stream-one`、正常验证 TLS、h2、Chrome、字面 SNI/Host/path，仅 HTTP/TCP。两条路径均实际转发预期正文；各自错误 UUID/CA/SNI/path/Host/mode 六类负向都取得真实拒绝原因、零应用送达且无 DIRECT。独立服务端 TLS 1.3/h2 探测与客户端实际转发分开记录，不称抓取了客户端 TLS 协商。

sing-box 1.14.2 实际 parser 以 `unknown transport type: xhttp` 拒绝。Mihomo `-t` 不验证 provider payload 的实际导入；Xray path 前缀、Host 大小写/端口、mode 方向性，以及源码对应上游状态与实际外层错误的区别见[完整刻画契约](XHTTP_CHARACTERIZATION_048.md)。不改 pin、构建或协议来填补三格式/双客户端公共契约。

HTTPUpgrade 观察保持独立，不能合成虚假的格式等价：

1. canonical Mihomo YAML → 实际 sing-box HTTPUpgrade 服务端真实转发成功
2. 原样 `type=httpupgrade` URI → 同一实际 HTTPUpgrade 服务端失败、零应用送达且无 DIRECT；该直接会话没有被改写或替换
3. 独立、正常验证证书/SNI/http1.1 的 TLS recorder 分别观察 canonical 完整 GET Upgrade 和原样 URI 的完整 raw VLESS/假 UUID/TCP/目标/应用请求；recorder 不转发，也不计入应用送达
4. 将 recorder 实际捕获的原样 URI bytes 不改任何字节，经另一条正常验证 TLS 的会话 replay 至实际固定 sing-box HTTPUpgrade，读取真实 `HTTP/1.1 400 Bad Request` 与精确正文 `400 Bad Request`，应用计数不变。该 HTTP400 只属于独立 replay，不称直接 URI 会话抓包
5. 原样 URI → 独立普通 TCP 服务端成功，仅证明固定 importer 错落普通 TCP，不能计作 HTTPUpgrade 支持

## 保留失败与修正

- 首候选 [`77c264ab35a0b7a6217638cf9decccae178fd7ea`](https://github.com/ForceMind/V-UI/commit/77c264ab35a0b7a6217638cf9decccae178fd7ea) 的[首次链路 run 37486293192 / job 112347034954](https://github.com/ForceMind/V-UI/actions/runs/37486293192/job/112347034954) attempt 1 共 100 项，仅 HTTPUpgrade 的晚期 `unexpected response version` 日志断言失败；其余七组成功。失败的链路 shell 未到后续独立 HY2/TUIC managed 命令，不能将它们算入该次通过
- 固定 Mihomo 在成功 dial/write 后的 relay read 不可靠输出该协议错误；源码诊断后改为上节真实 recorder/同 bytes replay，并经过独立复审。超时、EOF、单独零送达或无关成功没有替代拒绝原因；未更改核心/pin/TLS/URI importer/公共导出，也未盲目重跑同代码掩盖失败
- 首次本地完整 11 方法组和修正后 12 方法组均因 HTTPUpgrade 两个 sing-box 服务端启动 netlink EPERM 子测试而整组失败；独立本地 XHTTP/parser/验证 TLS recorder 成功分别记录，不能抹去整组失败或泛化历史环境限制。详细计数和执行时长见[阶段记录](XHTTP_CHARACTERIZATION_048.md)
- [REALITY/Vision](REALITY_VISION_CLOSURE_047.md)的首次 HEADERS 失败、独立审查阻断与 master VMess/Mihomo CA 日志失败/unchanged-code attempt 2，[TUIC](TUIC_CLOSURE_046.md)、[HY2](HYSTERIA2_CLOSURE_045.md)与[响应安全修复](INBOUND_RESPONSE_CLOSURE_20261006.md)的历史结果全部保留。最终 PR #25 成功不证明继承的被动日志轮询问题永久修复

## 产品冻结与发布准备

累计产品 0.4.7 范围冻结：已验收协议、安全编辑/普通响应 allowlist、证书/备份恢复、四目标 Linux、40 项 ToClash 与八组门槛保持。XHTTP/HTTPUpgrade 仍只具上述刻画证据；不扩展应用 UDP/XUDP/DNS、其他模式或公共导出。只读 UDP/DNS 审计保留，实现应在本版交付决定完成后作为独立阶段推进。

截至 2026-10-06 本次检查，本轮没有新建 tag、Draft Release、公开 Release、晋升附件、生产部署、真实 CA/账户或主机信任/防火墙变更。最终文档收口提交还须自己的独立审查、准确候选/正常合并/准确主线验收，再核验其同提交已接受的四目标实际归档；旧 metadata 或本页历史绿色不替代最终包验证。精确 SHA、CI 与附件结果应记入相应 PR/发布准备证据，不在本文预告未知提交的成功。[人工发布流程和当前准备状态](RELEASING.md)保持，完成准备不自动授权创建发布草稿或公开发布。

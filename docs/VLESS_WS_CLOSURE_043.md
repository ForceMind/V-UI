# v0.4.3 VLESS/WebSocket/TLS 主线收口

本页记录 2026-10-05 已完成的 WS 主线验收，修正阶段文档原有“候选待验收”状态。它不表示 v0.4.4 gRPC 通过、附件晋升、公开发布或部署。更早 v0.4.2 的[主线记录](MAINLINE_CLOSURE_20261005.md)与失败历史保持不变，PR #14 继续排除。

## 准确提交

- [PR #18](https://github.com/ForceMind/V-UI/pull/18) 正常 merge，来源分支 `iteration/v0.4.3-vless-websocket` 保留。
- 已独立审查的候选：`f9dfa611edcf5946bb4c8ae3cee59118d36930e9`。
- 最终 master：`1b3ec40cd3bb640246d12afa104db0aec08ce336`。
- merge parents：`0225ce4b1301e70068421e54c409b303d45cf812` 与 `f9dfa611edcf5946bb4c8ae3cee59118d36930e9`。
- 候选与合并后的同一 tree：`7f7e2df30774f614ecfe6989f22fbf8447d842e5`。
- merge object、双 parent、来源 ancestry 与 tree 已在本地和远端核对，终态再次读取 master 未变化。

## 最终 master 八组门槛

下面的准确主线 push 工作流均首次成功：**八组工作流、11 个 job、每一个 step 全部通过**。没有把 queued、running 或 skipped 当成成功。

1. [Test V-UI](https://github.com/ForceMind/V-UI/actions/runs/37285445405)
2. [Release deployment gates](https://github.com/ForceMind/V-UI/actions/runs/37285445385)
3. [Documents and release contracts](https://github.com/ForceMind/V-UI/actions/runs/37285445365)
4. [Real loopback](https://github.com/ForceMind/V-UI/actions/runs/37285445246)
5. [One-command installation](https://github.com/ForceMind/V-UI/actions/runs/37285445348)
6. [ACME](https://github.com/ForceMind/V-UI/actions/runs/37285445417)
7. [ToClash](https://github.com/ForceMind/V-UI/actions/runs/37285445401)
8. [Portable Linux matrix](https://github.com/ForceMind/V-UI/actions/runs/37285445251)

## 已验证范围

固定 sing-box 1.14.2 服务端、Mihomo 1.19.32 与 sing-box 1.14.2 客户端；仅 sing-box / VLESS / WS / TLS、单 UUID、空 flow、明确 SNI 和正常证书校验。path 为 1–256 字符的受限 ASCII 字面路径；Host 是可选、受限 ASCII hostname 客户端路由元数据。实际服务端配置移除该 Host 字段，sing-box WS 不实施请求 Host 白名单；两个客户端换用不同合法 Host 仍可连通。TLS SNI/证书验证保持独立。

ALPN 省略或恰为 `["http/1.1"]`，Chrome fingerprint 独立可选，仅新增 HTTP/TCP；Mihomo `udp: false`，sing-box 出站 `network: tcp`。严格导出保留 URI/Base64、Mihomo YAML 和 sing-box JSON 中所有受支持字段，拒绝未知 TLS/header/early-data 参数和畸形 path/Host/flow；不静默 DIRECT，不导出私钥或服务端材料路径。

- 普通本地及 CI discovery：263 项测试通过，50 个环境门槛明确 skip；激活门槛单独执行，不把这些 skip 算作真实运行通过。
- 固定服务端/客户端检查和正向链路覆盖八种独立 fingerprint/ALPN/Host 组合。
- 真实 loopback 共 37 项测试，含 12 个新增 WS 方法；两个客户端均正确送达 HTTP，错误 UUID/CA/SNI/path 时可达 IP 目标无请求、无 DIRECT 回退，CA/SNI 保留真实 x509 证据。不同合法 Host 的接受是刻画结果。
- 激活 Chromium 覆盖创建、取消、非法 path/Host 拒绝、编辑、刷新/再打开、隐藏 UUID 不变、托管证书保留、三个解析后的订阅输出无服务端材料，以及停机备份/恢复修复损坏的 transport/settings。
- 续期回归保留凭据、传输与 `CORE_STOPPED_PENDING_APPLY`，不启动手动停止的核心。
- 独立审查在修复后对准确 tree 放行，另行通过 50 项聚焦测试，并检查文档、空白、范围及安全边界。

本地沙箱的 sing-box netlink 与 Chromium Unix socket 曾被拒绝，复核后的一次提权尝试仍失败，因此没有声称本地真实运行或浏览器通过。上述准确候选和最终主线 CI 提供了对应真实验收。

## 发布边界

此 WS 阶段未创建 tag、Draft Release、公开 Release 或部署，未下载、晋升或发布其 CI 候选附件。此前 v0.4.2 准确主线套件和清单未被替换或重建。FastAPI/SQLite、40 项 ToClash、四目标 Linux 和托管证书边界保持；gRPC、Xray WS、HY2/TUIC、REALITY/Vision 不属于本页 WS 验收。

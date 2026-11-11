# 发布规范

## 当前稳定版

[v0.4.24](https://github.com/ForceMind/V-UI/releases/tag/v0.4.24) 已公开，正式应用源为 `8b8d7cf82e55609a205455b351f659eb9de91974`，Release ID 为 440129208。准确源的十组 Actions、32 个 job、新装和九个公开旧版保数据升级见[集成验收](https://github.com/ForceMind/V-UI/pull/158)。应用门禁晋升四目标同源制品，19 项资产的清单与公开摘要已匿名比对。[应用发布运行](https://github.com/ForceMind/V-UI/actions/runs/42786336300)

sing-box 1.14.3-r1 与 Xray 26.10.21-r1 依次公开，每个组件的 6 项资产及应用来源均通过匿名回读。两次晋升后，应用 `latest` 仍为 v0.4.24。[sing-box 组件](https://github.com/ForceMind/V-UI/releases/tag/core-sing-box-v1.14.3-r1) · [Xray 组件](https://github.com/ForceMind/V-UI/releases/tag/core-xray-v26.10.21-r1)

公开安装器 SHA-256 为 `722198cbaedeb69fcc945f4869b39276ecbfd011620c66def15a632509240f7d`，完整 `SHA256SUMS` 文件 SHA-256 为 `a3f5cafcd556173a11748c2584e0eb7e096fe4e9e7d51108d6502d6bba2628cc`。短入口单独跟踪，公开应用包使用原冻结源码。浏览器未验证。

## v0.4.22 发行证据（历史）

[v0.4.22](https://github.com/ForceMind/V-UI/releases/tag/v0.4.22) 已公开，正式应用源为 `558687a76cd29f4e4735284926eb05bf6664472e`，Release ID为437741132。[发布流程42398527616](https://github.com/ForceMind/V-UI/actions/runs/42398527616)晋升该提交的已验制品；公开19项资产、真实小文件字节和摘要已匿名核验，旧21资产保持。

短入口随后经 PR139→dev、PR140→main 合入 `39181e0cdb9643422a19049df942b208f876faf4`。入口候选/dev/main各自完成九组/12 jobs；[实际 main 短命令验收42405651147](https://github.com/ForceMind/V-UI/actions/runs/42405651147)和[公开完整安装验收42405651162](https://github.com/ForceMind/V-UI/actions/runs/42405651162)分别通过。入口 SHA 不是应用发布源，不重新生成或重标22资产。完整版本证据见[本版记录](MAINTENANCE_SHARE_0422.md)。浏览器未验证。

## v0.4.23候选状态（历史）

当前源码候选为 **v0.4.23**，Cloudflare DNS-01、通配符、绑定与私有备份兼容源码及独立审查已收口。准确候选完整Actions、四目标、新装与所有旧版升级尚未收口；没有受保护分支合并、公开23、切换入口或生产升级授权。失败与未开始的运行记录见[候选契约](DNS01_CLOUDFLARE_CONTRACT.md#候选与验收状态)。

此轮要求九组工作流、21个job：oneclick为一个producer、一个新装和八个公开旧版升级角色，共10个；每个角色与四目标材料必须属于同一准确source及当前run/attempt，不能借旧22的12-job例外或P1工具批次20-job绿灯取得资格。浏览器仍未验证，仅原固定浏览器步骤按已审策略保留明确skip。

## 版本、说明和兼容

纯文档、协作模板及不改变产品行为的说明更正不提升应用版本。用户可见修复、API/数据契约或协议行为变化需要独立评估版本和迁移范围。`VERSION`、应用版本和候选发行说明一致；用户安装示例及稳定版文档索引跟随已公开短入口的固定版本。候选升版不提前改公开安装命令。

每次发行说明应写清用户改变、升级建议、兼容/迁移边界、已知限制和实际验证。涉及持久化或认证变化时说明原数据、会话、凭据、订阅和证书的保留或失效条件，以及恢复的前置要求。不能把回滚程序等同于可安全回滚已迁移数据。

固定核心和依赖仍按既有 pin 与来源校验；改变 pin、协议支持或目标平台须独立审查和准确提交验收。发行包保留项目 [MIT License](../LICENSE)及[第三方许可证和来源](../third_party/NOTICE.md)，不能删除声明或将第三方许可改称项目自己的许可。

## 从候选到 main

1. 版本分支从 `dev` 开始，冻结本次范围和验收要求。实现、必要回归和说明接受独立审查后，通过正常 PR 合入 `dev`，再通过 PR 合入 `main`，保留历史和并发修改。
2. 候选、dev与最终main分别核对完整 SHA、实际内容树、每个 workflow 的最新 run/attempt、全部 job 和必要 step。不继承其他提交的绿灯；失败及同字节重跑原因保留。
3. 产品构建、制品处理、固定核心和真实系统安装/升级均在 GitHub Actions 的空白临时 Linux runner 执行。确认准确main的四目标包、新装、当前要求的不可变公开旧版升级及数据/恢复门槛，不能以 parser 或合成测试替代真实链路。
4. 人工发布只晋升这个准确main已受验的同一套件，发布时不重新构建。公开、实际部署和生产数据写入均需要对应明确授权。

文档或模板 PR 按改动核验内容、链接、版本和格式，既有 Actions 继续按事件执行；不额外要求每个文档 PR 手动构建全平台或运行无关测试。发布候选仍须满足完整实际门禁，不能因为文档流程较轻而跳过发行资格。

### 浏览器与版本专属例外

浏览器测试默认不运行，须有具体场景的明确授权；未运行即写明“浏览器未验证”。v0.4.22 的 `maintenance_share_no_browser` 是固定于该版已审产品的历史例外：九组/12 jobs及其余步骤全部成功，仅原12个明列浏览器步骤可跳过且必须存在，强制模板编译、netlink、固定核心、完整链路、短入口、新装及公开14/15/16/17/19/20/21保数据升级。

该例外不能用于未来版本、任意测试/工作流修改或扩大 skip 范围。未来发行须先明确自身验收要求并经审查，不复用22许可或降低现有门槛。queued、running、cancelled或未经允许的 skipped 不计成功。当前文档整理不改变 CLI flag、工作流或发布器算法。

## 开发中的并行验收模式

P1提供一次构建、独立角色消费及attempt绑定，已在main `3606cad`完成自身九组/20 jobs、九份同producer收据和22个线上文件读回；应用版本没有因此重发，公开22资格与资产保持。安装[42457488310](https://github.com/ForceMind/V-UI/actions/runs/42457488310)的attempt1因旧19发布元数据HTTP403在安装前失败，整组同源码attempt2通过；不表示上游故障永久解决。详细[契约](PARALLEL_RELEASE_ACCEPTANCE.md)将文件身份和真实job/step分开；旧版本flag继续按原固定scope处理。

23候选及后续版本使用`--parallel-acceptance-no-browser`前，必须先完成产品冻结并提交`.github/release-acceptance.json`；该模式拒绝0.4.22及更早版本，不能重发22。已知公开稳定基线必须被真实升级矩阵覆盖，源码准备仅允许受限元数据/说明，所有角色、步骤、同源材料和四目标必须成功。通用flag不自动授予合并、公开或生产部署授权。

## 发布资产

当前发布器的固定集合共 **19项**，来源、版本及完整性均绑定同一准确应用提交：

| 类别 | 文件 |
| --- | --- |
| 四目标离线包 | `vui-linux-x86_64-gnu.zip`、`vui-linux-aarch64-gnu.zip`、`vui-linux-x86_64-musl.zip`、`vui-linux-aarch64-musl.zip` |
| 安装和系统 helper | `install.sh`、`install_system.py`、`platform_support.py`、`firewall_support.py`、`service_support.py`、`system_launcher.py` |
| 受管控制入口 | `install_ops_control.py`、`system_ops.py`、`ops_worker.py`、`release_tools.py`、`v-ui` |
| 源码与元数据 | `vui-source.zip`、`RELEASE.json`、`RELEASE_NOTES.md`、`SHA256SUMS` |

包内 manifest、`RELEASE.json`、Actions artifact身份及摘要要与准确 SHA/version/target一致。源码、安装和控制 helper不能从新入口或本地WIP拼装。签名下载URL的目标不得继承GitHub授权头；摘要校验失败立即停止，不通过跳过TLS或摘要继续。

先上传草稿并逐项验证资产字节/摘要，再按明确批准公开。已有 tag、Release或资产不覆盖；发现问题以新版本修复，保留旧资产及原失败记录。

## P3 核心组件晋升

组件独立于应用发布。先验准确应用提交的新装、升级和常规门禁；同源组件工作流还须有四目标原生TCP、x86_64 GNU现有协议、ARM64受管及两种核心的生产收据。旧固定核心的loopback结果、仅构建的候选或其他attempt不授组件资格。候选收据本身始终标明未公开、未独立授予最终资格。

本批组件兼容范围只包含实际公开应用的准确source。经分别明确授权后，按“应用公开并读回→同一默认HEAD的组件晋升→短入口独立更新”的顺序执行。现有人工发布工作流的组件分支要求核对核心名称、准确应用VERSION/commit、最新同源候选run，以及`RELEASE CORE <组件tag> <commit>`确认；保留原权限，不用本机运行发布器作演习。

组件晋升仅上传同一受验候选的 `CORE-RELEASE.json`、`SHA256SUMS` 和四目标ZIP。草稿逐项读回且重新核HEAD/run/attempt后，才按 GitHub 的 [`make_latest=false` 发布选项](https://docs.github.com/en/rest/releases/releases#update-a-release)公开并核对应用latest不变。已有tag或Release不覆盖；上传/晋升结果未知时核对已知原记录，不重复发起或删除旧资产。

## 公开核验后再切短入口

公开后独立匿名核对tag目标、latest、公开状态、完整资产集合、每项digest与实际小文件字节，核对`RELEASE.json.source_commit`，并确认旧版资产ID/大小/digest保持。

只有这些检查成功后，才通过独立PR更新`setup.sh`的版本、真实`install.sh`字节SHA-256、完整`SHA256SUMS`字节SHA-256，以及`short-install.yml`的冻结公开产品源和直接合同。清单整文件摘要不等于某个ZIP或清单中一行的摘要。

入口候选/dev/main须独立验收；PR和dev使用准确提交URL，main合并后另验实际可变的`main/setup.sh` URL。应用源与入口源分别记录，不因入口或文档推进而重建已公开套件。以下示例使用短入口锁定的 v0.4.24。摘要占位符使用上方已核验的整份 `SHA256SUMS` 文件摘要。

```sh
bash install.sh --version v0.4.24 --with-global-cli \
  --kit-sha256 '<独立核验的SHA256SUMS整文件SHA-256>' --interactive
```

这需要先取得并核验该版受信安装器，且只用于已明确授权的安装目标；贡献和发布准备不在本机执行安装。参数、已有安装和离线操作见[安装指南](INSTALLATION.md)。

## DNS数据的发行兼容门槛

23不能仅凭普通TLS测试获得DNS资格：还需精确SAN/原子迁移、管理员与维护授权分界、冻结凭据修订及own-TXT清理、未知写入/人工确认、私有备份闭包和恢复后禁用、真实旧22 reader拒绝新kind、固定Certbot与Pebble双挑战，以及安装控制层的stdlib依赖闭包与旧DNS已应用材料复用。

含DNS数据的备份为`backup-dns01-v1`、0600且未加密，旧22 reader在替换数据前拒绝。新restore保留auto_renew偏好但将profile禁用并标记待确认；不支持用不声明DNS读取能力的旧候选切换或回退。签发、材料/身份一致性、SNI、应用和公网连通分别取证，详见[DNS指南](DNS01_CLOUDFLARE.md)。

## 升级和恢复边界

生产升级前在获准的私有位置备份并确认可用，保护当前配置、数据库、凭据、节点、订阅、证书及恢复材料。真实证书首次签发或新增CA条款须由有权的人明确确认；签发成功、服务TLS/主机名/HTTP健康和节点实际转发分别核验，不能互相替代。

维护任务提交一次后保存ID，只查询原任务；响应丢失属于结果未知，先读回任务与服务状态，不自动重发。任务成功后还需确认worker不忙、安装ready及实际版本/服务正常，失败不自动start或restore。具体操作和秘密只在授权私有记录保存，不进入公开Issue、PR、日志或发布附件。详见[受管操作](MANAGED_OPERATIONS.md)和[维护权限契约](REMOTE_MAINTENANCE_CONTRACT.md)。

<a id="047-打包范围说明更正2026-10-06"></a>

## 0.4.7 打包范围说明更正（2026-10-06）

旧深链保留。该阶段原始套件说明、失败和验收边界见[固定提交39181e0的原段](https://github.com/ForceMind/V-UI/blob/39181e0cdb9643422a19049df942b208f876faf4/docs/RELEASING.md#L157)；其他阶段见[历史索引](history/README.md)。历史结论只对应原版本与原提交，不授予未来发行资格。

# 安装包中的第三方组件

Xray-core `v26.10.21`、`mtg-multi` 和默认规则数据来自 3X-UI `v3.11.0` 的 Linux amd64 发行文件。此分支打包时校验整个上游文件的 SHA-256，再提取这些资源。

| 组件 | 来源与许可 |
| --- | --- |
| `x-ui/bin/xray-linux-amd64` | [Xray-core v26.10.21](https://github.com/XTLS/Xray-core/tree/v26.10.21)，原包附带的 Mozilla Public License 2.0 随 `x-ui/bin/LICENSE` 保留。 |
| `x-ui/bin/mtg-linux-amd64` | [mtg-multi 源码](https://github.com/mhsanaei/mtg-multi/tree/a45ebefed80e16949426381ef53fb5f1c1e4958d)，[MIT 许可](mtg-multi.txt)。 |
| `x-ui/bin/geoip.dat`、`x-ui/bin/geosite.dat` | [v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)，版本固定在 3X-UI 发行文件中，[GPL v3 许可](v2ray-rules-dat.txt)。 |
| `x-ui/bin/geoip_IR.dat`、`x-ui/bin/geosite_IR.dat` | [Iran-v2ray-rules](https://github.com/Chocolate4U/Iran-v2ray-rules)，项目使用 [GPL v3](../LICENSE)，上游数据分别适用[来源表中的许可](https://github.com/Chocolate4U/Iran-v2ray-rules#-sources)。 |
| `x-ui/bin/geoip_RU.dat`、`x-ui/bin/geosite_RU.dat` | [russia-v2ray-rules-dat](https://github.com/runetfreedom/russia-v2ray-rules-dat)，[GPL v3](../LICENSE)。数据来源见该项目说明。 |

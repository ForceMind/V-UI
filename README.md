# V-UI — 轻量双核心代理面板

**V-UI** 是一个面向个人 VPS 的轻量管理面板，统一管理 **Xray-core + sing-box**，并把服务器节点直接输出为可用的 **Mihomo / Clash Meta、sing-box JSON 和通用 Base64 订阅**。

当前版本的 Mihomo 分流引擎复用了同一作者项目 **ToClash** 的规则设计：策略组、服务预设、DNS policy、内网 DNS、自定义直连 / 强制代理和两种网络模式使用一致的语义。

## 核心能力

- **双核心**：Xray-core 与 sing-box 可独立生成配置、校验、启动、停止和重启。
- **统一节点管理**：同一张 Inbound 列表管理两个核心，旧 Xray 数据自动兼容。
- **协议**：
  - Xray：VLESS、VMess、Trojan、Shadowsocks。
  - sing-box：VLESS、VMess、Trojan、Shadowsocks、Hysteria2、TUIC。
- **订阅输出**：
  - 单节点分享 URI / 二维码。
  - 通用 Base64。
  - Mihomo / Clash Meta YAML。
  - sing-box JSON。
- **ToClash 分流**：
  - PROXY / AUTO / FORCE_PROXY。
  - 40 项服务预设。
  - 常规“国内直连、其余代理”模式。
  - “默认直连、仅指定服务代理”模式。
  - 用户始终直连 / 始终代理。
  - 本机、局域网、可选 CGNAT 优先保护。
  - 企业 / 家庭内网 DNS。
  - 必须代理规则后追加同条件 REJECT，避免不支持 UDP 时继续落入后续直连规则。
  - 业务 DNS 与节点自身 DNS 分开规划，避免代理节点解析环路。
- **轻量部署**：FastAPI + SQLite，不依赖 Redis、PostgreSQL 或消息队列。
- **amd64 / arm64**：安装脚本与 Docker 均下载对应架构的 Xray 和 sing-box。

## 安装

### 二进制版本

发布 Release 后可使用：

~~~bash
bash <(curl -Ls https://raw.githubusercontent.com/ForceMind/V-UI/master/install-bin.sh)
~~~

安装器会同时准备 V-UI、Xray-core 和 sing-box。

### 源码安装

~~~bash
git clone https://github.com/ForceMind/V-UI.git
cd V-UI
sudo bash install.sh
~~~

### Docker

~~~bash
git clone https://github.com/ForceMind/V-UI.git
cd V-UI
docker compose up -d --build
~~~

Docker 使用 host 网络，使面板创建的代理端口无需逐个映射。

## 使用

面板默认监听：

~~~text
http://服务器IP:2053/ui
~~~

基本流程：

1. 在“入站节点”选择 **Xray** 或 **sing-box** 并创建节点。
2. 在“Mihomo 分流”选择网络模式、服务规则和自定义分流。
3. 在“订阅输出”复制 Mihomo、sing-box 或 Base64 地址。
4. Mihomo 订阅会实时读取已保存的分流设置，不需要重新生成节点。

### Mihomo 两种模式

**常规模式**

~~~text
本机 / 局域网 → DIRECT
用户始终直连 → DIRECT
用户始终代理 → FORCE_PROXY
服务预设 → PROXY / FORCE_PROXY
中国大陆 → DIRECT
其他 → PROXY
~~~

生成：

- PROXY：手动选择，包含 AUTO、DIRECT 和全部节点。
- AUTO：url-test 自动选择节点。
- FORCE_PROXY：不包含 DIRECT，用于不能降级直连的服务。

**默认直连模式**

~~~text
本机 / 局域网 → DIRECT
用户指定 / 已开启服务 → FORCE_PROXY
其他 → DIRECT
~~~

适合当前网络本身已经可以直接访问国际互联网，只希望某些服务使用固定代理出口的场景。

## API

主要接口：

~~~text
GET/POST/PUT/DELETE  /api/inbounds
GET                  /api/cores/status
POST                 /api/cores/{core}/restart

GET                  /api/routing/mihomo
PUT                  /api/routing/mihomo
GET                  /api/routing/mihomo/catalog
GET                  /api/routing/mihomo/preview

GET                  /api/subscription/raw
GET                  /api/subscription/mihomo.yaml
GET                  /api/subscription/sing-box.json
GET                  /api/subscription/link/{id}
~~~

旧的 /api/xray/* 继续保留，便于现有脚本逐步迁移。

## 数据

~~~text
data/
├── v-ui.db                 # SQLite
├── xray.json               # Xray 运行配置
├── sing-box.json           # sing-box 运行配置
└── mihomo-routing.json     # Mihomo / ToClash 分流设置
~~~

已有数据库缺少 core 字段时，启动会自动补字段，并将旧节点视为 xray。

## 协议参数编辑器

新增节点时已经可以直接配置：

- VLESS Reality / Vision。
- Xray RAW / WebSocket / gRPC / XHTTP。
- sing-box WebSocket / gRPC / HTTPUpgrade。
- TLS 的 SNI、证书路径与私钥路径。
- Hysteria2 上传/下载带宽、Salamander / Gecko obfs。
- TUIC congestion control、UDP relay、0-RTT。
- REALITY X25519 密钥自动生成，服务器私钥不会写入客户端订阅。

需要注意：Mihomo 当前文档对 Xray-core v26.7.11+ REALITY 给出了兼容性警告。V-UI 会在订阅页显示对应提示；如果主要客户端是 Mihomo，优先使用 sing-box + VLESS REALITY。

下一阶段主要剩余：

- 节点编辑时的可视化参数回填。
- ACME / acme.sh 证书申请、续期和证书状态管理。
- 更完整的核心版本管理与升级/回滚。
- 清理早期遗留的重复目录和历史代码。

## 项目结构

~~~text
V-UI/
├── app/
│   ├── api/
│   │   ├── inbounds.py
│   │   ├── cores.py
│   │   ├── routing.py
│   │   ├── subscription.py
│   │   ├── xray.py
│   │   └── singbox.py
│   ├── models/
│   └── services/
│       ├── core_manager.py
│       ├── inbound_service.py
│       ├── mihomo_routing.py
│       ├── mihomo_subscription.py
│       └── subscription_service.py
├── bin/
├── data/
├── tests/
├── web/
├── main.py
├── install.sh
├── install-bin.sh
├── Dockerfile
└── docker-compose.yml
~~~

## 相关项目

- **ToClash**：浏览器端代理链接 / Mihomo YAML 转换与分流配置工具。V-UI 的 Mihomo 规则模型与 ToClash 保持同一套设计思路。

## License

MIT License

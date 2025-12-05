# V-UI - Next Generation Xray Panel

## 📖 简介 (Introduction)
**V-UI** 是一个轻量级、高性能、现代化的 Xray/Sing-box 管理面板。它基于 Python **FastAPI** 和 **Vue 3** 构建，旨在提供超越传统面板（如 x-ui, 3x-ui）的用户体验和运维能力。

不同于传统的 Go 语言面板，V-UI 采用 Python 作为后端，拥有更强大的生态扩展能力（如数据分析、机器学习识别恶意流量等），并专为高性能服务器和复杂网络环境设计。

## ✨ 核心特性 (Features)

### 🚀 协议与连接 (Protocols)
- **多核心支持**: 完美兼容 **Xray-core** 与 **Sing-box**。
- **全协议覆盖**: VMess, VLESS, Trojan, Shadowsocks, Dokodemo-door, Socks, HTTP。
- **前沿技术**: 支持 **XTLS-Reality**, **Vision** 流控, **Hysteria 2**, **Tuic v5**, **WireGuard**。
- **多用户管理**: 支持流量统计、到期时间设置、账号限速、IP 限制。

### 🛡️ 安全与防护 (Security)
- **主动防御**: 集成防火墙管理 (iptables/ufw)，支持面板一键封禁恶意 IP。
- **防探测**: 智能识别恶意扫描行为，保护服务器不被探测。
- **系统加固**: SSH 登录日志分析与防爆破 (Fail2Ban 集成)。
- **WAF 防护**: 针对 Web 端口的基础应用层防火墙。

### 📊 运维与监控 (Operations)
- **实时仪表盘**: CPU、内存、磁盘、网络流量实时监控。
- **网页部署**: 内置静态网站托管功能，轻松部署伪装站点 (支持反向代理)。
- **证书管理**: 集成 ACME 协议，支持 Let's Encrypt / ZeroSSL 证书一键申请与自动续期。
- **类宝塔体验**: 提供文件管理、进程守护等基础运维功能。

## 🛠️ 快速开始 (Quick Start)

### 1. 服务器部署 (Ubuntu/Debian)

我们提供了一键安装脚本，适用于 Ubuntu 20.04+ / Debian 10+。

```bash
# 1. 上传项目文件到服务器 /usr/local/v-ui
# (或者使用 git clone)

# 2. 进入目录
cd /usr/local/v-ui

# 3. 赋予脚本执行权限
chmod +x install.sh

# 4. 运行安装脚本 (需要 root 权限)
sudo ./install.sh
```

安装完成后，服务将自动启动并设置为开机自启。

### 2. 本地开发 (Local Development)

如果你想在本地运行或进行二次开发：

**环境要求**: Python 3.10+, Node.js (可选, 仅用于前端深度定制)

```bash
# 1. 克隆项目
git clone https://github.com/your-repo/v-ui.git
cd v-ui

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化数据库
python bin/init_db.py

# 4. 启动服务
python main.py
```

## 📖 使用说明 (Usage)

- **管理面板**: `http://<服务器IP>:2053/ui`
- **API 文档**: `http://<服务器IP>:2053/docs` (Swagger UI)
- **默认端口**: `2053`

### 默认账号
*目前版本处于开发阶段，默认使用模拟验证。*
- **Username**: `admin`
- **Password**: `admin`
*(请在生产环境中修改 `app/api/auth.py` 或等待数据库验证模块上线)*

## 📂 目录结构 (Directory Structure)

```
v-ui/
├── app/                # 后端核心代码
│   ├── api/            # API 路由接口 (System, Xray, Auth, Security)
│   ├── models/         # 数据库模型 (SQLAlchemy)
│   ├── services/       # 业务逻辑服务 (核心控制, 监控等)
│   └── ...
├── bin/                # 二进制文件目录 (存放 xray, sing-box 核心文件)
├── data/               # 数据目录 (数据库 v-ui.db, 配置文件 config.json)
├── web/                # 前端静态资源 (HTML, JS, CSS)
├── main.py             # 程序入口 (FastAPI App)
├── requirements.txt    # Python 依赖列表
└── install.sh          # 自动化部署脚本
```

## ❓ 常见问题 (FAQ)

**Q: 如何更新 Xray 核心?**
A: 将下载好的最新版 `xray` 二进制文件覆盖到 `bin/` 目录下，并在面板右上角点击"重启核心"即可。

**Q: 为什么选择 Python 而不是 Go?**
A: Python 拥有更丰富的运维和安全库 (如 psutil, scapy, fail2ban-client)，能让我们更轻松地实现复杂的服务器管理和安全防护功能。

## 📄 许可证 (License)
MIT License

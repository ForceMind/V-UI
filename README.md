# V-UI - 下一代 Xray 面板 (Next Gen Xray Panel)

**V-UI** 是一个轻量级、高性能、现代化的 Xray/Sing-box 管理面板。
它专为小白用户和高级玩家设计，提供可视化的界面来管理你的 VPN 节点、流量和服务器状态。

## ✨ 核心亮点

*   **简单易用**: 一键安装，开箱即用，全中文界面。
*   **协议全**: 支持 VMess, VLESS, Trojan, Shadowsocks, Hysteria 2, Tuic v5, WireGuard。
*   **防封锁**: 内置支持 Reality 和 Vision 等最新抗干扰技术。
*   **安全**: 自动拦截恶意扫描，保护服务器安全。
*   **多核心**: 同时支持 Xray-core 和 Sing-box。

---

## 📖 小白安装教程 (Installation)

### 准备工作
1.  一台境外 VPS 服务器 (推荐 Ubuntu 20.04+ 或 Debian 10+)。
2.  SSH 连接工具 (如 Xshell, Putty, macOS Terminal)。
3.  **注意**: 如果是阿里云/腾讯云/AWS 等，请务必在后台安全组放行 **2053** 端口，以及你打算使用的节点端口。

### 方式一：二进制极速安装 (推荐)

无需编译，无需安装 Python 环境，下载即用。

```bash
bash <(curl -Ls https://raw.githubusercontent.com/ForceMind/V-UI/master/install-bin.sh)
```

### 方式二：Docker 安装 (推荐)

如果你喜欢干净的环境，推荐使用 Docker。

1.  **安装 Docker** (如果已有可跳过):
    ```bash
    curl -fsSL https://get.docker.com | bash
    ```

2.  **部署 V-UI**:
    ```bash
    # 下载代码
    git clone https://github.com/ForceMind/V-UI.git
    cd V-UI

    # 启动面板
    docker-compose up -d
    ```

---

## 💻 使用说明 (Usage)

### 1. 登录面板
*   **访问地址**: `http://你的服务器IP:2053/ui`
*   **默认账号**: `admin`
*   **默认密码**: `admin`

*(建议登录后立即修改密码)*

### 2. 添加节点 (创建一个 VPN 连接)
1.  点击左侧菜单 **"入站列表 (Inbounds)"**。
2.  点击 **"添加节点"**。
3.  **备注**: 随便填，例如 "我的手机"。
4.  **协议**: 推荐选择 `vless` 或 `vmess`。
5.  **端口**: 默认或自定义 (记得在云服务商防火墙放行该端口)。
6.  点击 **"确定"**。

### 3. 连接使用
目前版本请手动复制节点信息到你的客户端 (v2rayN, Shadowrocket 等) 配置使用。后续将支持二维码和订阅链接。

---

## ❓ 常见问题 (FAQ)

**Q: 安装后无法访问面板?**
A: 请检查服务器防火墙。
1.  云服务商安全组是否放行 2053 端口？
2.  服务器内部防火墙: `ufw allow 2053/tcp`。

**Q: 如何更新面板?**
A:
*   **脚本安装**: 输入 `v-ui` 选择 "1. 安装/更新"。
*   **Docker安装**: 在目录内执行 `docker-compose pull && docker-compose up -d`。

**Q: 核心支持哪些?**
A: 默认内置最新版 Xray-core。

---

## 📂 目录结构

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
├── install.sh          # 自动化部署脚本
├── Dockerfile          # Docker 构建文件
└── docker-compose.yml  # Docker 编排文件
```

## 🛠️ 源码构建 (Build from Source)

如果你想自己编译二进制文件，可以使用我们提供的构建脚本。

### Windows 用户
确保已安装 Docker Desktop，然后在 PowerShell 中运行：
```powershell
.\build_release.ps1
```

### Linux / macOS 用户
确保已安装 Docker，然后在终端运行：
```bash
chmod +x build_release.sh
./build_release.sh
```

构建完成后，二进制文件将位于 `dist/v-ui`。

## 📄 许可证
MIT License

# V-UI Project Plan

## 1. 项目简介 (Project Overview)
V-UI 是一个基于 Python (FastAPI) 和 Vue.js 构建的现代化服务器管理与 VPN 控制面板。它旨在对标并超越 `3x-ui`，提供更强大的服务器运维功能、更完善的安全防护以及极致的用户体验。

## 2. 核心功能 (Core Features)

### 2.1 VPN & 协议管理 (VPN & Protocol Management)
- **多协议支持**: 全面支持 VMess, VLESS, Trojan, Shadowsocks, Dokodemo-door, Socks, HTTP。
- **前沿协议**: 集成 Hysteria 2, Tuic v5, WireGuard。
- **Xray 特性**: 支持 XTLS-Reality, Vision 流控。
- **多核心切换**: 支持 Xray-core 和 Sing-box 核心切换。

### 2.2 服务器运维 (Server Operations - Like Baota)
- **系统监控**: 实时 CPU, 内存, 磁盘, 网络流量监控。
- **进程管理**: 核心进程守护，自动重启。
- **网页部署**: 内置静态网站托管功能，支持反向代理配置。
- **文件管理**: 简单的 Web 文件管理器 (规划中)。

### 2.3 安全防护 (Security & Firewall)
- **防探测/抗扫描**: 自动识别恶意扫描 IP 并通过防火墙 (iptables/nftables) 封禁。
- **Fail2Ban 集成**: 登录失败自动封禁。
- **流量限制**: 用户级和系统级流量控制。
- **WAF (简易版)**: 针对 Web 端口的基础防护。

### 2.4 用户与面板管理 (User & Panel Management)
- **多用户系统**: 完善的用户权限、流量配额、到期时间管理。
- **SSL 自动化**: 集成 ACME 协议，支持 Let's Encrypt/ZeroSSL 证书一键申请与自动续期。
- **Telegram 机器人**: 消息通知与简单的远程控制。

## 3. 技术栈 (Tech Stack)
- **Backend**: Python 3.10+ (FastAPI, Uvicorn, SQLAlchemy)
- **Frontend**: Vue 3 + TypeScript + Element Plus (Planned)
- **Database**: SQLite (Default) / MySQL (Optional)
- **Core**: Xray-core / Sing-box

## 4. 目录结构 (Directory Structure)
- `app/`: 后端核心代码
    - `api/`: API 路由接口
    - `services/`: 业务逻辑 (Xray管理, 系统监控, SSL等)
    - `models/`: 数据库模型
- `web/`: 前端静态资源
- `bin/`: 存放 Xray/Sing-box 二进制文件
- `data/`: 存放数据库和配置文件

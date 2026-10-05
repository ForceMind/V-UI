# rc.1 — 真实 VLESS/TLS 与 DNS 路径验收

依赖 alpha.6 / ab641a2。只新增真实链路验证，不增加协议或改变服务端/客户端生成算法。核心固定为 sing-box 1.14.2、Mihomo 1.19.32；下载必须通过仓库既有 SHA-256 校验。

## 测试如何运行

使用临时 SQLite 中的假账号/节点，通过真实 ASGI API 创建专用订阅，并读取该订阅返回的完整 Mihomo YAML。sing-box 的入站来自现有 V-UI adapter；Mihomo 使用真实订阅中的节点、策略组、规则顺序和 DNS policy 键顺序。

为避免对外联网，测试仅替换以下环境参数：监听端口改成随机回环端口；服务端测试域名交由内存 hosts 解析到回环；生成配置中的公共 DNS 上游替换为本机 UDP / TLS DoH 服务，保留 `#FORCE_PROXY` 策略选择及全部规则顺序。并非把生产策略改成“全部直连”再测试。

测试 CA 每次临时生成，仅通过测试子进程的 SSL_CERT_FILE/SSL_CERT_DIR 信任；不写系统根证书，不设置 skip-cert-verify，不修改系统代理、TUN、DNS、hosts 或防火墙。服务器和测试目标仅监听回环，结束时关闭所有子进程和监听器。

## 五个用例的断言

1. **实际代理 / 直连 / DNS 与停启**：指定代理、显式直连及未指定默认直连请求抵达测试 HTTP 服务；业务 TXT 查询穿过真实 VLESS/TLS 访问本机 HTTPS DoH，直连查询走独立 UDP；节点主机名只进入 bootstrap DNS。关闭 sing-box 后，强制代理不能触达目标但直连仍正常；恢复后代理连通。
2. **错误信任链**：Mihomo 仅信任另一测试 CA 时拒绝连接，不能回退直连。
3. **错误 SNI**：正确 CA 但主机名不匹配时仍拒绝，不关闭 TLS 校验。
4. **错误凭据**：错误 VLESS UUID 被拒绝，不回退直连。
5. **可选 TLS 参数**：Chrome fingerprint + ALPN 实际握手及代理连接成功，保持证书校验。

停启断言针对真实连接失败，不误称 REJECT 行是失败回调。具体五个 unittest 的名称见 tests/test_loopback_chain.py。

## 运行

```sh
pip install -r requirements-test.txt
python scripts/fetch_test_cores.py /tmp/vui-cores
python scripts/fetch_mihomo.py /tmp/vui-client
VUI_TEST_CORES=/tmp/vui-cores VUI_TEST_MIHOMO=/tmp/vui-client/mihomo \
VUI_DATA_DIR="$(mktemp -d)" python -m unittest discover -s tests -p test_loopback_chain.py -v
```

下载发生在显式开发命令中；测试本身不下载东西。CI 的独立 Real loopback proxy and DNS chain job 运行相同命令，并保留无真实凭据的结果及二进制 pin 元数据。

## 不据此保证

- 不验证实际 VPS 公网线路、速度、运营商、防火墙或服务地区限制。
- 不验证其他协议/REALITY/非 TCP 传输；UDP 转发本身仍未做端到端验收。
- DNS 使用本机 DoH 替代公共上游，证明 TLS/路由选择和解析路径，不证明 Cloudflare/Google 公共服务可达。
- 当前真实链路配置为默认直连模式，不依赖 GeoSite/GeoIP；常规模式的100场景语义回归不等于实际地理库下载/分类效果验收。
- CI 或本地通过不等于已合并、正式发布或部署 VPS；rc.2 仍需完成选定部署方式的门槛。

参考：Go crypto/x509 的 SSL_CERT_FILE/SSL_CERT_DIR 环境覆盖；sing-box 官方 DNS Hosts 与 TLS 字段文档。实际兼容结论以本次固定二进制的测试为准，而非未来版本文档。

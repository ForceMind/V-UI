"""Deterministic release-only preparation of the existing frontend templates.

The repository templates remain development inputs. The release never falls back
from missing local assets to an unpinned CDN.
"""
from pathlib import Path

ASSETS = {
    'https://unpkg.com/element-plus/dist/index.css':'vendor/element-plus.css',
    'https://unpkg.com/vue@3/dist/vue.global.js':'vendor/vue.js',
    'https://unpkg.com/element-plus':'vendor/element-plus.js',
    'https://unpkg.com/@element-plus/icons-vue':'vendor/icons.js',
    'https://unpkg.com/axios/dist/axios.min.js':'vendor/axios.js',
    'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js':'vendor/qrcode.js',
}

def replace_once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise ValueError('Frontend template changed; review release preparation before packaging')
    return text.replace(before,after)

def prepare(payload: Path):
    path=payload/'web/index.html';text=path.read_text()
    for source,target in ASSETS.items():
        text=replace_once(text,'"'+source+'"','"'+target+'"')
    text=replace_once(text,'Mihomo 分流页保存的 ToClash 规则','分流与订阅工作区保存的 ToClash 规则')
    text=replace_once(text,
        'Mihomo 官方当前明确警告：Xray-core v26.7.11+ REALITY 存在不兼容。V-UI 会生成配置，但如果主要给 Mihomo 使用，建议改选 sing-box + VLESS REALITY。',
        '当前已验证导出仅覆盖 sing-box VLESS/TCP/TLS。REALITY 在本版未验收，不会作为可用订阅导出。')
    path.write_text(text)
    path=payload/'web/js/app.js';text=path.read_text()
    text=replace_once(text,"core: 'xray',","core: 'sing-box',")
    text=replace_once(text,'port: 443,','port: 10443,')
    text=replace_once(text,"security: 'none',","security: 'tls',")
    text=replace_once(text,"transport: 'raw',","transport: 'direct',")
    text=replace_once(text,'newInbound.port = 443;','newInbound.port = 10443;')
    text=replace_once(text,"['trojan', 'hysteria2', 'tuic'].includes(newInbound.protocol)","['vless', 'trojan', 'hysteria2', 'tuic'].includes(newInbound.protocol)")
    path.write_text(text)

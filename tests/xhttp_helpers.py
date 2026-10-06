"""Bare fixed-core fixtures, not production transport profiles or exporters."""
from urllib.parse import urlencode

UUID = '11111111-1111-4111-8111-111111111111'
WRONG_UUID = '22222222-2222-4222-8222-222222222222'
SNI = 'vpn.example.test'
WRONG_SNI = 'wrong.example.test'
PATH = '/vui-xhttp/'
HOST = SNI
MODE = 'stream-one'
TARGET_BODY = b'VUI-LOOPBACK-TARGET'
FAILURES = ('uuid', 'ca', 'sni', 'path', 'host', 'mode')


def xray_server(port, cert, key):
    return {'log': {'loglevel': 'debug'}, 'inbounds': [{
        'tag': 'xhttp-in', 'listen': '127.0.0.1', 'port': port, 'protocol': 'vless',
        'settings': {'clients': [{'id': UUID}], 'decryption': 'none'},
        'streamSettings': {'network': 'xhttp', 'security': 'tls',
            'tlsSettings': {'alpn': ['h2'], 'certificates': [
                {'certificateFile': str(cert), 'keyFile': str(key)}]},
            'xhttpSettings': {'path': PATH, 'host': HOST, 'mode': MODE}}}],
        'outbounds': [{'protocol': 'freedom', 'tag': 'application'}]}


def xhttp_values(failure=None):
    return {'uuid': WRONG_UUID if failure == 'uuid' else UUID,
        'sni': WRONG_SNI if failure == 'sni' else SNI,
        'path': '/unrelated-xhttp/' if failure == 'path' else PATH,
        'host': 'other.example.test' if failure == 'host' else HOST,
        'mode': 'packet-up' if failure == 'mode' else MODE}


def mihomo_proxy(server_port, *, failure=None, httpupgrade=False):
    values = xhttp_values(failure)
    proxy = {'name': 'ONLY_PROXY', 'type': 'vless', 'server': '127.0.0.1',
        'port': server_port, 'uuid': values['uuid'], 'udp': False,
        'tls': True, 'servername': values['sni'], 'skip-cert-verify': False,
        'client-fingerprint': 'chrome', 'alpn': ['http/1.1' if httpupgrade else 'h2']}
    if httpupgrade:
        proxy.update({'network': 'ws', 'ws-opts': {'path': PATH,
            'headers': {'Host': HOST}, 'v2ray-http-upgrade': True}})
    else:
        proxy.update({'network': 'xhttp', 'xhttp-opts': {
            key: values[key] for key in ('path', 'host', 'mode')}})
    return proxy


def share_uri(server_port, *, failure=None, httpupgrade=False):
    """Emit the actual URI input; never rewrite the pinned converter's output."""
    values = xhttp_values(failure)
    params = {'security': 'tls', 'type': 'httpupgrade' if httpupgrade else 'xhttp',
        'encryption': 'none', 'sni': values['sni'], 'fp': 'chrome',
        'alpn': 'http/1.1' if httpupgrade else 'h2',
        'path': values['path'], 'host': values['host']}
    if not httpupgrade:
        params['mode'] = values['mode']
    return f"vless://{values['uuid']}@127.0.0.1:{server_port}?"+urlencode(params)+'#URI_ONLY'


def mihomo_config(port, *, proxy=None, provider=None):
    if (proxy is None) == (provider is None):
        raise ValueError('Exactly one explicit proxy or file provider is required')
    config = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
        'mode': 'rule', 'log-level': 'debug', 'ipv6': False, 'rules': ['MATCH,ONLY_PROXY']}
    if proxy is not None:
        config['proxies'] = [proxy]
    else:
        config['proxy-providers'] = {'uri': {'type': 'file', 'path': str(provider)}}
        config['proxy-groups'] = [{'name': 'ONLY_PROXY', 'type': 'select', 'use': ['uri']}]
    return config


def singbox_xhttp_client(server_port):
    return {'outbounds': [{'type': 'vless', 'tag': 'ONLY_PROXY', 'server': '127.0.0.1',
        'server_port': server_port, 'uuid': UUID, 'network': 'tcp',
        'tls': {'enabled': True, 'server_name': SNI, 'alpn': ['h2']},
        'transport': {'type': 'xhttp', 'path': PATH, 'host': HOST, 'mode': MODE}}],
        'route': {'final': 'ONLY_PROXY'}}


def singbox_gap_server(port, cert, key, *, httpupgrade=True):
    inbound = {'type': 'vless', 'tag': 'gap-control', 'listen': '127.0.0.1',
        'listen_port': port, 'users': [{'uuid': UUID}],
        'tls': {'enabled': True, 'server_name': SNI, 'certificate_path': str(cert),
                'key_path': str(key), 'alpn': ['http/1.1']}}
    if httpupgrade:
        inbound['transport'] = {'type': 'httpupgrade', 'host': HOST, 'path': PATH}
    return {'log': {'level': 'debug', 'timestamp': False}, 'inbounds': [inbound],
        'outbounds': [{'type': 'direct', 'tag': 'application'}], 'route': {'final': 'application'}}


def rejection_reason(failure):
    """Exact fixed-source reasons, kept distinct from timeout/EOF or old logs."""
    return {'uuid': ('server', ('invalid request user id:', WRONG_UUID)),
        'ca': ('client', ('x509', 'unknown authority')),
        'sni': ('client', ('x509', WRONG_SNI)),
        'path': ('server', ('failed to validate path', '/unrelated-xhttp/')),
        'host': ('server', ('failed to validate host', 'other.example.test')),
        'mode': ('server', ('packet-up mode is not allowed',))}[failure]

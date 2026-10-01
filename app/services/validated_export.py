"""Audited export surface. Draft exporters remain internal, never a public fallback."""
from __future__ import annotations
import base64
import re
from urllib.parse import quote, urlencode
from app.services.subscription_tokens import normalize_server
from app.services.mihomo_routing import unique_proxy_names


class ExportError(ValueError):
    pass


def mapping(value, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ExportError(f"Unsupported {label} fields; export was not generated")
    return value


def node_host(value: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError()
        return normalize_server(value)
    except ValueError:
        raise ExportError('Invalid connection host or TLS name') from None


def validated_node(item, server: str) -> dict:
    if not item.enable or getattr(item, 'expiry_time', 0):
        raise ExportError('Disabled or expiring nodes are not eligible for this export profile')
    if item.core != 'sing-box' or item.protocol != 'vless':
        raise ExportError('Validated export currently requires sing-box VLESS/TCP/TLS')
    server = node_host(server)
    if type(item.port) is not int or not 1 <= item.port <= 65535:
        raise ExportError('Invalid node port')
    name = item.remark or f'node-{item.id}'
    if not isinstance(name, str) or not 1 <= len(name) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ExportError('Invalid node name')
    settings = mapping(item.settings or {}, {'users'}, 'VLESS settings')
    users = settings.get('users')
    if not isinstance(users, list) or len(users) != 1:
        raise ExportError('Select a node with exactly one explicit VLESS user')
    user = mapping(users[0], {'uuid','name','flow'}, 'VLESS user')
    uid = user.get('uuid')
    if not isinstance(uid, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uid):
        raise ExportError('Invalid VLESS UUID')
    if user.get('flow'):
        raise ExportError('This flow is not part of the validated TCP/TLS profile')
    stream = mapping(item.stream_settings or {}, {'tls','transport','_vui'}, 'stream')
    if stream.get('transport') not in (None, {}):
        raise ExportError('Non-TCP transports need a separately validated export profile')
    tls = mapping(stream.get('tls'), {'enabled','server_name','certificate_path','key_path','alpn'}, 'TLS')
    if tls.get('enabled') is not True:
        raise ExportError('Verified TLS is required for this export profile')
    sni = node_host(tls.get('server_name') or '')
    meta = mapping(stream.get('_vui', {}), {'security','server_name','client_fingerprint','skip_cert_verify'}, 'client metadata')
    if meta.get('security', 'tls') != 'tls' or meta.get('skip_cert_verify', False) is not False:
        raise ExportError('TLS verification cannot be disabled in this profile')
    if meta.get('server_name') and node_host(meta['server_name']) != sni:
        raise ExportError('Conflicting client/server TLS names')
    fingerprint = meta.get('client_fingerprint', '')
    if fingerprint not in ('', 'chrome'):
        raise ExportError('Client fingerprint is not validated')
    alpn = tls.get('alpn')
    if alpn is not None and (not isinstance(alpn, list) or not alpn or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9./_-]{1,64}', x) for x in alpn)):
        raise ExportError('Invalid TLS ALPN')
    result = {'name':name, 'type':'vless', 'server':server, 'port':item.port, 'uuid':uid,
              'udp':True, 'tls':True, 'servername':sni, 'network':'tcp',
              'packet-encoding':'xudp', 'skip-cert-verify':False}
    if fingerprint:
        result['client-fingerprint'] = fingerprint
    if alpn:
        result['alpn'] = list(alpn)
    return result


def validated_nodes(items, server: str) -> list[dict]:
    items = list(items)
    if not items:
        raise ExportError('At least one eligible node is required; no DIRECT fallback was generated')
    return unique_proxy_names([validated_node(item, server) for item in items])


def share_link(item, server: str) -> str:
    node = validated_node(item, server)
    host = f"[{node['server']}]" if ':' in node['server'] else node['server']
    params = {'security':'tls', 'type':'tcp', 'sni':node['servername'], 'encryption':'none', 'packetEncoding':'xudp'}
    if node.get('client-fingerprint'):
        params['fp'] = node['client-fingerprint']
    if node.get('alpn'):
        params['alpn'] = ','.join(node['alpn'])
    return f"vless://{node['uuid']}@{host}:{node['port']}?{urlencode(params)}#{quote(node['name'], safe='')}"


def base64_subscription(items, server: str) -> str:
    items = list(items)
    validated_nodes(items, server)
    return base64.b64encode('\n'.join(share_link(item, server) for item in items).encode()).decode()


def singbox_client_config(items, server: str) -> dict:
    proxies = validated_nodes(items, server)
    outbounds = []
    for node in proxies:
        tls = {'enabled':True, 'server_name':node['servername']}
        if node.get('client-fingerprint'):
            tls['utls'] = {'enabled':True, 'fingerprint':node['client-fingerprint']}
        if node.get('alpn'):
            tls['alpn'] = list(node['alpn'])
        outbounds.append({'type':'vless', 'tag':node['name'], 'server':node['server'],
            'server_port':node['port'], 'uuid':node['uuid'], 'packet_encoding':'xudp', 'tls':tls})
    return {'log':{'level':'warn'},
            'inbounds':[{'type':'mixed','tag':'local','listen':'127.0.0.1','listen_port':7890}],
            'outbounds':outbounds, 'route':{'final':outbounds[0]['tag']}}


def export_warnings(items) -> list[dict]:
    warnings = []
    for item in items:
        try:
            validated_node(item, 'example.test')
        except (ValueError, TypeError, AttributeError):
            warnings.append({'inbound_id':item.id,'code':'UNVERIFIED_EXPORT_PROFILE',
                'message':'This node is outside the verified sing-box VLESS/TCP/TLS export profile.'})
    return warnings

"""Strict input boundary around the unchanged ToClash-derived rule planner.

The legacy planner receives canonical values only; public APIs must use this
module rather than its historical permissive input helpers.
"""
from __future__ import annotations
import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit
from app.services.mihomo_routing import default_presets, default_routing, build_rule_plan as _plan


def _unsafe_text(value: str) -> bool:
    return any(c.isspace() or ord(c)<32 or ord(c)==127 for c in value) or '\\' in value


def parse_routing_target(value: str) -> dict[str,str] | None:
    if not isinstance(value,str): return None
    entry=value.strip()
    if not entry or _unsafe_text(entry): return None
    is_url='://' in entry
    wildcard=not is_url and entry.startswith(('*.','+.'))
    host_input=entry[2:] if wildcard else entry
    bare_ipv6=not is_url and not host_input.startswith('[') and host_input.count(':')>1
    try:
        parsed=urlsplit(host_input if is_url else 'http://'+(f'[{host_input}]' if bare_ipv6 else host_input))
        if parsed.scheme.lower() not in {'http','https','ws','wss'} or parsed.username or parsed.password: return None
        _=parsed.port
        if not is_url and (parsed.path not in ('','/') or parsed.query or parsed.fragment): return None
        host=(parsed.hostname or '').lower().rstrip('.')
        if not host: return None
        try:
            ip=ipaddress.ip_address(host)
            if wildcard: return None
            return {'kind':'ipv4' if ip.version==4 else 'ipv6','value':ip.compressed.lower()}
        except ValueError:
            if ':' in host or re.fullmatch(r'[0-9.]+',host): return None
        host=host.encode('idna').decode('ascii')
        if len(host)>253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',x) for x in host.split('.')): return None
        return {'kind':'domain','value':host}
    except (ValueError,UnicodeError): return None


def normalize_dns_server(value: str) -> str | None:
    entry=str(value or '').strip()
    if entry=='system': return 'system'
    if entry.lower().startswith('udp://'): entry=entry[6:]
    if not entry or _unsafe_text(entry) or any(c in entry for c in '/@?#'): return None
    host,port=entry,53
    if entry.startswith('['):
        match=re.fullmatch(r'\[([^\]]+)\](?::(\d+))?',entry)
        if not match: return None
        host=match.group(1)
        if match.group(2): port=int(match.group(2))
    elif entry.count(':')==1:
        host,port_text=entry.rsplit(':',1)
        if not port_text.isdigit(): return None
        port=int(port_text)
    if not 1<=port<=65535: return None
    try: ip=ipaddress.ip_address(host)
    except ValueError: return None
    if ip.is_unspecified: return None
    rendered=f'[{ip.compressed}]' if ip.version==6 else ip.compressed
    return f'udp://{rendered}:{port}'


def normalize_routing(payload: dict[str,Any] | None) -> dict[str,Any]:
    payload={} if payload is None else payload
    if not isinstance(payload,dict) or set(payload)-{'mode','direct_domains','proxy_domains','presets','bypass_cgnat','intranet'}:
        raise ValueError('Invalid routing settings object')
    if type(payload.get('bypass_cgnat',False)) is not bool: raise ValueError('CGNAT switch must be boolean')
    mode=payload.get('mode','standard')
    if mode not in {'standard','direct'}: raise ValueError("mode must be 'standard' or 'direct'")
    def targets(values):
        if values is None: return []
        if not isinstance(values,list) or len(values)>2048: raise ValueError('Invalid routing target list')
        result=[]
        for raw in values:
            target=parse_routing_target(raw)
            if not target: raise ValueError('Invalid routing target; settings were not saved')
            if target['value'] not in result: result.append(target['value'])
        return result
    presets=default_presets()
    supplied=payload.get('presets') or {}
    if not isinstance(supplied,dict): raise ValueError('presets must be an object')
    for preset_id,enabled in supplied.items():
        if preset_id not in presets or type(enabled) is not bool: raise ValueError('Unknown service or non-boolean preset value')
        presets[preset_id]=enabled
    zones=payload.get('intranet') or []
    if not isinstance(zones,list) or len(zones)>256: raise ValueError('Invalid intranet zones')
    seen={}
    for zone in zones:
        if not isinstance(zone,dict) or set(zone)-{'suffix','nameservers'}: raise ValueError('Invalid intranet zone')
        target=parse_routing_target(str(zone.get('suffix') or ''))
        if not target or target['kind']!='domain': raise ValueError('Invalid intranet suffix')
        if not isinstance(zone.get('nameservers'),list): raise ValueError('DNS servers must be a list')
        servers=[]
        for raw in zone['nameservers']:
            server=normalize_dns_server(raw) if isinstance(raw,str) else None
            if not server: raise ValueError('Invalid intranet DNS server')
            if server not in servers: servers.append(server)
        if not servers: raise ValueError('Intranet zone requires DNS servers')
        previous=seen.get(target['value'])
        if previous is not None and previous!=servers: raise ValueError('Conflicting DNS servers for one suffix')
        seen[target['value']]=servers
    intranet=[{'suffix':suffix,'nameservers':servers} for suffix,servers in sorted(seen.items(),key=lambda x:(-len(x[0].split('.')),x[0]))]
    return {'mode':mode,'direct_domains':targets(payload.get('direct_domains')),
        'proxy_domains':targets(payload.get('proxy_domains')),'presets':presets,
        'bypass_cgnat':payload.get('bypass_cgnat',False),'intranet':intranet}


def build_rule_plan(routing: dict | None = None) -> dict:
    return _plan(normalize_routing(routing))

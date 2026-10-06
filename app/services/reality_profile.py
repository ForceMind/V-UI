"""Bounded sing-box VLESS/REALITY/Vision state, without lossy editor repairs."""
from __future__ import annotations

import base64
import ipaddress
import re
import secrets
import uuid
from copy import deepcopy

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

FLOW = 'xtls-rprx-vision'
PROFILE_FIELDS = {'security', 'transport', 'flow', 'reality_target', 'reality_server_name',
                  'reality_uuid', 'reality_uuid_set', 'reality_short_id', 'reality_short_id_set',
                  'reality_private_key_set', 'client_fingerprint', 'skip_cert_verify'}
META_FIELDS = {'security', 'server_name', 'client_fingerprint', 'skip_cert_verify',
               'reality_public_key', 'reality_short_id', 'mihomo_compatibility'}


def _mapping(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unsupported REALITY ' + label + '; no fields were dropped')
    return value


def has_reality(stream):
    """Recognize malformed imports as well as valid states, never eligibility."""
    if not isinstance(stream, dict):
        return False
    tls, meta = stream.get('tls'), stream.get('_vui')
    return ('realitySettings' in stream or 'reality' in stream
            or str(stream.get('security', '')).lower() == 'reality'
            or (isinstance(tls, dict) and 'reality' in tls)
            or (isinstance(meta, dict) and (str(meta.get('security', '')).lower() == 'reality'
                or bool({'reality_public_key', 'reality_short_id'} & meta.keys()))))


def is_direct_candidate(stream):
    """Known non-direct drafts stay segregated; malformed transports fail closed."""
    if not has_reality(stream):
        return False
    transport = stream.get('transport')
    return not (isinstance(transport, dict) and transport.get('type') in ('ws', 'grpc', 'httpupgrade'))


def wants_direct(profile):
    return (isinstance(profile, dict) and isinstance(profile.get('security'), str)
            and profile['security'].lower() == 'reality'
            and profile.get('transport', 'direct') not in ('ws', 'grpc', 'httpupgrade'))


def uuid_value(value):
    if not isinstance(value, str):
        raise ValueError('REALITY UUID must be a canonical UUID string')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('REALITY UUID must be a canonical UUID string') from None
    return value


def credential_user(settings):
    settings = _mapping(settings, {'users'}, 'settings')
    users = settings.get('users')
    if not isinstance(users, list) or len(users) != 1:
        raise ValueError('REALITY requires one explicit UUID; no credential was generated')
    user = _mapping(users[0], {'uuid', 'flow', 'name'}, 'user fields')
    uuid_value(user.get('uuid'))
    if 'name' in user and not isinstance(user['name'], str):
        raise ValueError('REALITY user name must be a string')
    return user


def short_id_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{16}', value):
        raise ValueError('REALITY short ID must be exactly 16 lowercase hexadecimal characters')
    return value


def dns_name(value):
    if (not isinstance(value, str) or len(value) > 253 or not value
            or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', p)
                       for p in value.split('.')) or re.fullmatch(r'[0-9.]+', value)):
        raise ValueError('REALITY requires an explicit literal DNS server name')
    return value


def _host(value):
    if not isinstance(value, str) or not value or value != value.strip() or '%' in value:
        raise ValueError('REALITY handshake server must be a literal hostname or IP address')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return dns_name(value)
    if address.is_unspecified:
        raise ValueError('REALITY handshake server must not be an unspecified address')
    return value


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError('REALITY handshake port must be an integer from 1 to 65535')
    return value


def target_parts(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('REALITY handshake target must be an explicit literal string')
    if value.startswith('['):
        match = re.fullmatch(r'\[([^\]]+)\](?::([0-9]+))?', value)
        if not match:
            raise ValueError('Invalid REALITY handshake target')
        host, port = match.groups()
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError('Bracketed REALITY targets require an IPv6 address') from None
    else:
        if value.count(':') > 1:
            raise ValueError('IPv6 REALITY targets must be bracketed')
        host, sep, port = value.partition(':')
        if not sep:
            port = None
    if port is not None and (not re.fullmatch(r'[1-9][0-9]{0,4}', port)):
        raise ValueError('Invalid REALITY handshake port')
    return _host(host), _port(int(port) if port is not None else 443)


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def key_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        raise ValueError('REALITY keys must be canonical unpadded base64url-encoded 32-byte values')
    raw = base64.b64decode(value + '=', altchars=b'-_', validate=True)
    if len(raw) != 32 or _b64url(raw) != value:
        raise ValueError('REALITY keys must be canonical unpadded base64url-encoded 32-byte values')
    return raw


def _public(private):
    return _b64url(X25519PrivateKey.from_private_bytes(key_bytes(private)).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def generate_keypair():
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    encoded = _b64url(raw)
    return encoded, _public(encoded)


def validate_stored(settings, stream):
    """Return internal validated fields. Callers must select client-safe fields."""
    user = credential_user(settings)
    if user.get('flow') != FLOW:
        raise ValueError('REALITY requires the exact xtls-rprx-vision flow')
    stream = _mapping(stream, {'tls', '_vui'}, 'stream fields')
    tls = _mapping(stream.get('tls'), {'enabled', 'server_name', 'reality'}, 'TLS fields')
    reality = _mapping(tls.get('reality'), {'enabled', 'handshake', 'private_key', 'short_id'}, 'server fields')
    if tls.get('enabled') is not True or reality.get('enabled') is not True:
        raise ValueError('REALITY and TLS must be explicitly enabled')
    sni = dns_name(tls.get('server_name'))
    handshake = _mapping(reality.get('handshake'), {'server', 'server_port'}, 'handshake fields')
    server, port = _host(handshake.get('server')), _port(handshake.get('server_port'))
    shorts = reality.get('short_id')
    if not isinstance(shorts, list) or len(shorts) != 1:
        raise ValueError('REALITY requires exactly one explicit short ID')
    short = short_id_value(shorts[0])
    meta = _mapping(stream.get('_vui'), META_FIELDS, 'client metadata')
    if meta.get('security') != 'reality' or meta.get('client_fingerprint') != 'chrome':
        raise ValueError('REALITY requires explicit REALITY security and the Chrome fingerprint')
    if meta.get('skip_cert_verify', False) is not False:
        raise ValueError('REALITY verification cannot be disabled')
    if 'server_name' in meta and meta['server_name'] != sni:
        raise ValueError('REALITY client/server names conflict')
    if 'reality_short_id' in meta and meta['reality_short_id'] != short:
        raise ValueError('REALITY client/server short IDs conflict')
    if 'mihomo_compatibility' in meta and meta['mihomo_compatibility'] != 'supported':
        raise ValueError('Unsupported REALITY compatibility state')
    private, public = reality.get('private_key'), meta.get('reality_public_key')
    key_bytes(public)
    if _public(private) != public:
        raise ValueError('REALITY public/private key pair does not match')
    return {'uuid': user['uuid'], 'flow': FLOW, 'server_name': sni,
            'handshake_server': server, 'handshake_port': port, 'private_key': private,
            'public_key': public, 'short_id': short}


def _transition_source(settings, stream):
    """Explicit TLS/none migration may remove represented fields, not imports."""
    user = credential_user(settings)
    if user.get('flow', '') not in ('', FLOW):
        raise ValueError('Existing VLESS flow cannot be normalized into REALITY')
    stream = _mapping(stream, {'tls', '_vui', 'transport'}, 'migration stream fields')
    tls = _mapping(stream.get('tls', {}), {'enabled', 'server_name', 'certificate_path', 'key_path'},
                   'migration TLS fields')
    meta = _mapping(stream.get('_vui', {}), {'security', 'server_name', 'client_fingerprint', 'skip_cert_verify'},
                    'migration client fields')
    if 'enabled' in tls and type(tls['enabled']) is not bool:
        raise ValueError('Existing TLS enabled must be a boolean')
    for mapping, names in ((tls, ('server_name', 'certificate_path', 'key_path')),
                           (meta, ('security', 'server_name', 'client_fingerprint'))):
        for name in names:
            if name in mapping and not isinstance(mapping[name], str):
                raise ValueError('Existing TLS fields must be literal strings')
    if 'skip_cert_verify' in meta and type(meta['skip_cert_verify']) is not bool:
        raise ValueError('Existing TLS verification field must be a boolean')
    if 'transport' in stream:
        transport = stream['transport']
        if not isinstance(transport, dict):
            raise ValueError('Existing transport cannot be represented by this editor')
        if transport.get('type') == 'grpc':
            from app.services.grpc_profile import grpc_service_name
            _mapping(transport, {'type', 'service_name'}, 'migration gRPC fields')
            grpc_service_name(transport.get('service_name'))
        elif transport.get('type') == 'ws':
            from app.services.websocket_profile import websocket_host, websocket_path
            _mapping(transport, {'type', 'path', 'headers'}, 'migration WebSocket fields')
            websocket_path(transport.get('path'))
            headers = _mapping(transport.get('headers', {}), {'Host'}, 'migration WebSocket headers')
            if 'Host' in headers:
                websocket_host(headers['Host'])
        else:
            raise ValueError('Existing transport cannot be dropped by this REALITY editor')


def compile_profile(profile, settings, stream, *, creating=False):
    profile = _mapping(profile, PROFILE_FIELDS, 'visual profile fields')
    old = validate_stored(settings, stream) if has_reality(stream) else None
    settings, stream = deepcopy(settings), deepcopy(stream)
    if not old:
        if creating:
            settings = _mapping(settings, {'users'}, 'settings')
            users = settings.get('users', [])
            if not isinstance(users, list) or len(users) > 1:
                raise ValueError('New REALITY nodes require one UUID')
            user = _mapping(users[0], {'uuid', 'flow', 'name'}, 'user fields') if users else {}
            if user.get('uuid', '') == '':
                replacement = profile.get('reality_uuid', '')
                user['uuid'] = uuid_value(replacement) if replacement != '' else str(uuid.uuid4())
            settings['users'] = [user]
        _transition_source(settings, stream)
    user = credential_user(settings)
    for name, expected in (('security', 'reality'), ('transport', 'direct'), ('flow', FLOW),
                           ('client_fingerprint', 'chrome')):
        value = profile.get(name, {'security': 'reality', 'transport': 'direct', 'flow': FLOW,
                                   'client_fingerprint': 'chrome'}[name]) if old else profile.get(name)
        if value != expected or not isinstance(value, str):
            raise ValueError('REALITY requires ' + name + '=' + expected)
    if profile.get('skip_cert_verify', False) is not False:
        raise ValueError('REALITY verification cannot be disabled')
    for field in ('reality_uuid_set', 'reality_short_id_set', 'reality_private_key_set'):
        if field in profile and type(profile[field]) is not bool:
            raise ValueError('REALITY credential presence flags must be booleans')
    for field, validator in (('reality_uuid', uuid_value), ('reality_short_id', short_id_value)):
        if field in profile and profile[field] != '':
            validator(profile[field])
    sni = dns_name(profile.get('reality_server_name', old['server_name'] if old else None))
    if 'reality_target' in profile:
        server, port = target_parts(profile['reality_target'])
    elif old:
        server, port = old['handshake_server'], old['handshake_port']
    else:
        raise ValueError('An explicit REALITY handshake target is required')
    uid = profile.get('reality_uuid') or user['uuid']
    short = profile.get('reality_short_id') or (old['short_id'] if old else secrets.token_hex(8))
    private, public = (old['private_key'], old['public_key']) if old else generate_keypair()
    user.update(uuid=uid, flow=FLOW)
    result = {'tls': {'enabled': True, 'server_name': sni, 'reality': {'enabled': True,
        'handshake': {'server': server, 'server_port': port}, 'private_key': private, 'short_id': [short]}},
        '_vui': {'security': 'reality', 'server_name': sni, 'client_fingerprint': 'chrome',
                 'reality_public_key': public, 'reality_short_id': short}}
    # An ordinary edit preserves the complete validated representation.
    if old:
        result = deepcopy(stream)
        result['tls']['server_name'] = sni
        result['tls']['reality']['handshake'] = {'server': server, 'server_port': port}
        result['tls']['reality']['short_id'] = [short]
        if 'server_name' in result['_vui']:
            result['_vui']['server_name'] = sni
        if 'reality_short_id' in result['_vui']:
            result['_vui']['reality_short_id'] = short
    validate_stored(settings, result)
    return settings, result


def editor_profile(settings, stream):
    value = validate_stored(settings, stream)
    host = value['handshake_server']
    target = ('[' + host + ']' if ':' in host else host) + ':' + str(value['handshake_port'])
    return {'security': 'reality', 'transport': 'direct', 'flow': FLOW,
            'reality_target': target, 'reality_server_name': value['server_name'],
            'client_fingerprint': 'chrome', 'skip_cert_verify': False,
            'reality_uuid': '', 'reality_uuid_set': True,
            'reality_short_id': '', 'reality_short_id_set': True, 'reality_private_key_set': True}

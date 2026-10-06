"""Literal TUIC v5 editor values; imports are retained or explicitly rejected."""
from copy import deepcopy
import re

from app.services.hysteria2_profile import password_value as _password_value, PROFILE_FIELDS as SHARED_FIELDS
from app.services.subscription_tokens import normalize_server

TLS_FIELDS = {'enabled', 'server_name', 'certificate_path', 'key_path', 'alpn'}
META_FIELDS = {'security', 'server_name', 'client_fingerprint', 'skip_cert_verify', 'udp_relay_mode'}
PROFILE_FIELDS = SHARED_FIELDS | {'tuic_uuid', 'tuic_uuid_set', 'tuic_password', 'tuic_password_set'}


def uuid_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        raise ValueError('TUIC UUID must be a literal canonical UUID string')
    return value


def password_value(value):
    try:
        return _password_value(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace('Hysteria2', 'TUIC')) from None


def credential_user(settings):
    users = settings.get('users') if isinstance(settings, dict) else None
    if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
        raise ValueError('TUIC editing requires one explicit UUID/password pair; no credential was generated')
    user = users[0]
    if set(user) - {'uuid', 'password', 'name'}:
        raise ValueError('Unsupported TUIC user fields; no fields were dropped')
    uuid_value(user.get('uuid')); password_value(user.get('password'))
    if 'name' in user and not isinstance(user['name'], str):
        raise ValueError('TUIC user name must be a string')
    return user


def stored_fields(settings, stream):
    if not isinstance(settings, dict) or not isinstance(stream, dict):
        raise ValueError('Existing TUIC settings and stream must be objects')
    if set(settings) - {'users', 'congestion_control', 'zero_rtt_handshake'} or set(stream) - {'tls', '_vui'}:
        raise ValueError('Existing TUIC options cannot be represented by this editor; no fields were dropped')
    credential_user(settings)
    tls, meta = stream.get('tls', {}), stream.get('_vui', {})
    if not isinstance(tls, dict) or not isinstance(meta, dict) or set(tls) - TLS_FIELDS or set(meta) - META_FIELDS:
        raise ValueError('Existing TUIC TLS/client options cannot be represented by this editor; no fields were dropped')
    if 'enabled' in tls and type(tls['enabled']) is not bool:
        raise ValueError('Existing TUIC TLS enabled must be a boolean')
    for values, fields in ((tls, ('server_name', 'certificate_path', 'key_path')),
                           (meta, ('security', 'server_name', 'client_fingerprint'))):
        for field in fields:
            if field in values and not isinstance(values[field], str):
                raise ValueError('Existing TUIC ' + field + ' must be a string')
    if 'skip_cert_verify' in meta and type(meta['skip_cert_verify']) is not bool:
        raise ValueError('Existing TUIC skip_cert_verify must be a boolean')
    if 'alpn' in tls and (not isinstance(tls['alpn'], list) or not tls['alpn']
            or any(not isinstance(v, str) or not v for v in tls['alpn'])):
        raise ValueError('Existing TUIC ALPN cannot be normalized by this editor')
    if tls.get('enabled') is True and (meta.get('security', 'tls') != 'tls'
            or (meta.get('server_name') and meta['server_name'] != tls.get('server_name'))):
        raise ValueError('Existing TUIC client/server TLS fields conflict')
    for values, name, allowed in ((settings, 'congestion_control', ('cubic', 'new_reno', 'bbr')),
                                   (meta, 'udp_relay_mode', ('native', 'quic'))):
        if name in values and values[name] not in allowed:
            raise ValueError('Existing TUIC ' + name + ' cannot be normalized by this editor')
    if 'zero_rtt_handshake' in settings and type(settings['zero_rtt_handshake']) is not bool:
        raise ValueError('Existing TUIC zero_rtt_handshake must be a boolean')
    return tls, meta


def prepare_profile(profile, settings, stream):
    tls, meta = stored_fields(settings, stream)
    if 'tls' in stream and 'alpn' not in tls:
        raise ValueError('Existing TUIC is missing explicit ALPN; repair the raw profile before visual editing')
    if set(profile) - PROFILE_FIELDS:
        raise ValueError('Unsupported TUIC visual profile fields; no fields were dropped')
    foreign_defaults = {'flow': '', 'path': '/', 'host': '', 'service_name': '',
        'xhttp_mode': 'auto', 'reality_target': '', 'reality_server_name': '', 'reality_short_id': '',
        'up_mbps': None, 'down_mbps': None, 'obfs_type': '', 'obfs_password': '', 'obfs_password_set': False,
        'shadowsocks_method': 'aes-128-gcm', 'shadowsocks_password_set': False,
        'hysteria2_password': '', 'hysteria2_password_set': False}
    for name, default in foreign_defaults.items():
        if name in profile and (type(profile[name]) is not type(default) or profile[name] != default):
            raise ValueError('TUIC does not accept the foreign option ' + name)
    if profile.get('transport', 'quic') != 'quic':
        raise ValueError('TUIC uses its native QUIC transport')
    if 'security' in profile:
        if not isinstance(profile['security'], str) or profile['security'].lower() != 'tls':
            raise ValueError('TUIC requires an explicit TLS security string')
    elif 'tls' in stream and tls.get('enabled') is not True:
        raise ValueError('Explicit TLS selection is required to change an existing TUIC security state')
    for name in ('server_name', 'certificate_path', 'key_path'):
        profile.setdefault(name, deepcopy(meta.get(name, tls.get(name, ''))))
    profile.setdefault('client_fingerprint', deepcopy(meta.get('client_fingerprint', '')))
    profile.setdefault('skip_cert_verify', deepcopy(meta.get('skip_cert_verify', False)))
    profile.setdefault('congestion_control', deepcopy(settings.get('congestion_control', 'cubic')))
    profile.setdefault('zero_rtt_handshake', deepcopy(settings.get('zero_rtt_handshake', False)))
    profile.setdefault('udp_relay_mode', deepcopy(meta.get('udp_relay_mode', 'native')))
    for name in ('server_name', 'certificate_path', 'key_path', 'client_fingerprint'):
        if not isinstance(profile[name], str) or profile[name] != profile[name].strip():
            raise ValueError('TUIC ' + name + ' must be a literal string without surrounding whitespace')
    try:
        if normalize_server(profile['server_name']) != profile['server_name']:
            raise ValueError()
    except ValueError:
        raise ValueError('TUIC requires an explicit valid TLS server name') from None
    if type(profile['skip_cert_verify']) is not bool or type(profile['zero_rtt_handshake']) is not bool:
        raise ValueError('TUIC verification and zero-RTT fields must be booleans')
    if profile['congestion_control'] not in ('cubic', 'new_reno', 'bbr') or profile['udp_relay_mode'] not in ('native', 'quic'):
        raise ValueError('Unsupported TUIC congestion/relay preference')
    for name, validate in (('tuic_uuid', uuid_value), ('tuic_password', password_value)):
        if name in profile and profile[name] != '': validate(profile[name])
    return deepcopy(tls.get('alpn', ['h3']))


def editor_profile(settings, stream):
    tls, meta = stored_fields(settings, stream)
    return {
        'security': 'tls' if tls.get('enabled') is True else 'none', 'transport': 'quic',
        'server_name': deepcopy(meta.get('server_name', tls.get('server_name', ''))),
        'certificate_path': deepcopy(tls.get('certificate_path', '')), 'key_path': deepcopy(tls.get('key_path', '')),
        'client_fingerprint': deepcopy(meta.get('client_fingerprint', '')),
        'skip_cert_verify': deepcopy(meta.get('skip_cert_verify', False)),
        'congestion_control': deepcopy(settings.get('congestion_control', 'cubic')),
        'zero_rtt_handshake': deepcopy(settings.get('zero_rtt_handshake', False)),
        'udp_relay_mode': deepcopy(meta.get('udp_relay_mode', 'native')),
        'tuic_uuid': '', 'tuic_password': '', 'tuic_uuid_set': True, 'tuic_password_set': True,
    }

"""The bounded managed-certificate targets shared by API, summaries and renewal."""
from app.services.reality_profile import has_reality


def supports_profile(core, protocol, profile=None):
    profile = profile or {}
    if core == 'sing-box':
        return protocol in {'vless', 'trojan', 'vmess', 'hysteria2', 'tuic'}
    # This adds only the Xray VLESS/raw TLS certificate consumer. It does not
    # qualify other Xray transports, protocols, REALITY or public exports.
    return (core == 'xray' and protocol == 'vless'
            and profile.get('transport', 'raw') in ('raw', 'tcp')
            and not profile.get('flow'))


def target_error(item):
    """Keep established TLS errors distinct from unsupported target shapes."""
    stream = item.stream_settings if isinstance(item.stream_settings, dict) else {}
    if has_reality(stream):
        return 'TLS_NODE_REQUIRED'
    if item.core == 'sing-box':
        if not supports_profile(item.core, item.protocol):
            return 'UNSUPPORTED_CERTIFICATE_TARGET'
        tls = stream.get('tls')
        return None if isinstance(tls, dict) and tls.get('enabled') is True else 'TLS_NODE_REQUIRED'
    if not supports_profile(item.core, item.protocol, {
            'transport': stream.get('method', stream.get('network', 'raw'))}):
        return 'UNSUPPORTED_CERTIFICATE_TARGET'
    settings = item.settings if isinstance(item.settings, dict) else {}
    users = settings.get('users') or settings.get('clients') or []
    if not isinstance(users, list) or any(not isinstance(user, dict) or user.get('flow') for user in users):
        return 'UNSUPPORTED_CERTIFICATE_TARGET'
    tls = stream.get('tlsSettings')
    if stream.get('security') != 'tls' or not isinstance(tls, dict):
        return 'TLS_NODE_REQUIRED'
    certificates = tls.get('certificates', [])
    # Imported multi-certificate/inline material cannot be safely replaced by
    # the single-certificate editor. Reject it without changing the binding.
    if (not isinstance(certificates, list) or len(certificates) > 1
            or not all(isinstance(cert, dict) and not ({'certificate', 'key'} & cert.keys())
                       for cert in certificates)):
        return 'UNSUPPORTED_CERTIFICATE_TARGET'
    return None


def eligible(item):
    return target_error(item) is None


def paths(core, stream):
    if core == 'xray':
        certificates = stream.get('tlsSettings', {}).get('certificates', [])
        cert = certificates[0] if certificates else {}
        return cert.get('certificateFile'), cert.get('keyFile')
    tls = stream.get('tls') or {}
    return tls.get('certificate_path'), tls.get('key_path')


def replace_material(core, stream, certificate_paths, domain):
    if core == 'xray':
        tls = stream['tlsSettings']
        cert = dict((tls.get('certificates') or [{}])[0])
        cert.update(certificateFile=str(certificate_paths[0]), keyFile=str(certificate_paths[1]))
        tls.update(certificates=[cert], serverName=domain)
    else:
        stream['tls'].update(certificate_path=str(certificate_paths[0]),
                             key_path=str(certificate_paths[1]), server_name=domain)
    if isinstance(stream.get('_vui'), dict):
        stream['_vui']['server_name'] = domain

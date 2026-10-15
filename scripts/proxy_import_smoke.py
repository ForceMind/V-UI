"""Consume the installed runtime's bounded parser without touching live data."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from app import release_tools


PROBE = r'''
import base64, json, sys
from app.services.upstream_import import preview_upstreams, select_upstream, UpstreamImportError
from app.services.proxy_store import ProxyStore

uid = '11111111-1111-1111-1111-111111111111'
host = 'synthetic-upstream.example.test'
key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
sid = '0123456789abcdef'
checks = 0
for mode in ('tls', 'reality'):
    flow = '' if mode == 'tls' else 'xtls-rprx-vision'
    query = 'type=tcp&encryption=none&security=' + mode + '&sni=' + host
    tls = {'serverName': host}
    clash = {'name': 'Synthetic', 'type': 'vless', 'server': host, 'port': 443,
             'uuid': uid, 'tls': True, 'servername': host, 'network': 'tcp'}
    if mode == 'reality':
        query += '&flow=' + flow + '&fp=chrome&pbk=' + key + '&sid=' + sid
        tls.update(fingerprint='chrome', publicKey=key, shortId=sid)
        clash.update(flow=flow, **{'client-fingerprint': 'chrome',
                     'reality-opts': {'public-key': key, 'short-id': sid}})
    outbound = {'tag': 'Synthetic', 'protocol': 'vless', 'settings': {'vnext': [{
        'address': host, 'port': 443, 'users': [{'id': uid, 'encryption': 'none', 'flow': flow}]}]},
        'streamSettings': {'network': 'tcp', 'security': mode, mode + 'Settings': tls}}
    import yaml
    inputs = [('uri', 'vless://' + uid + '@' + host + ':443?' + query + '#Synthetic'),
              ('v2ray-json', json.dumps({'outbounds': [outbound]})),
              ('clash-yaml', yaml.safe_dump({'proxies': [clash]}, sort_keys=False))]
    canonical = None
    for source_format, text in inputs:
        preview = preview_upstreams(text, source_format).to_dict()
        assert preview['candidates'][0]['supported'] is True
        assert uid not in json.dumps(preview) and sid not in json.dumps(preview)
        profile = select_upstream(text, 0, source_format).to_dict()
        if canonical is None: canonical = profile
        assert canonical == profile
        checks += 1
store = ProxyStore()
before = store.read_snapshot()
created = store.save_upstream(canonical, before['revision'])
assert uid not in json.dumps(created) and sid not in json.dumps(created)
assert created['snapshot']['active'] is None
assert not created['snapshot']['settings']['local_proxy_enabled']
assert not created['snapshot']['settings']['relay_enabled']
try:
    preview_upstreams('proxies: &nodes []\ncopy: *nodes\n', 'clash-yaml')
except UpstreamImportError:
    checks += 1
else:
    raise AssertionError('unsafe YAML accepted')
print(json.dumps({'proxy_import': 'installed-runtime isolated URI/JSON/YAML TLS+REALITY, safe draft, YAML rejection',
                  'proxy_import_checks': checks, 'proxy_runtime_applied': False}))
'''


def installed_proxy_import_smoke(python: Path, payload: Path, root: Path) -> dict:
    # Separate private data: this probe must not modify a staged installation's
    # real draft, active revision, nodes, or credentials.
    with tempfile.TemporaryDirectory(prefix='.proxy-import-smoke-', dir=root) as temporary:
        data = Path(temporary) / 'data'
        env = release_tools.child_env(payload, data)
        result = subprocess.run([str(python), '-B', '-c', PROBE], cwd=payload,
                                env=env, capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise AssertionError('Installed isolated proxy import smoke failed: ' + result.stderr[-8192:])
        evidence = json.loads(result.stdout)
        if evidence.get('proxy_import_checks') != 7 or evidence.get('proxy_runtime_applied') is not False:
            raise AssertionError('Installed proxy import evidence was incomplete')
        return evidence

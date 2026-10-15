"""Consume installed M2 modules and fixed core without applying a runtime."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from app import release_tools

PROBE = r'''
import base64, json, os, sys
from pathlib import Path
from app.services import proxy_resolution as resolution
from app.services.proxy_policy import AddressPolicy, PolicyError, VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from app.services.core_runtime import CoreRuntime

# Numeric getaddrinfo in the isolated worker cannot make a DNS request. This
# deliberately exercises its required resource limits and bounded IPC even
# though production's fast path would not need a worker for a literal IP.
raw = resolution._bounded_worker_io(
    [sys.executable, '-I', '-B', str(Path(resolution.__file__).resolve()), '--resolve-worker'],
    b'{"host":"127.0.0.1"}')
assert resolution._decode_worker_output(raw) == ('127.0.0.1',)
try:
    AddressPolicy().check_addresses(('8.8.8.8', '127.0.0.1'), self_addresses=('127.0.0.2',))
except PolicyError:
    pass
else:
    raise AssertionError('mixed public/private address set was accepted')
root = Path(os.environ['VUI_DATA_DIR'])
root.mkdir(parents=True, exist_ok=True, mode=0o700)
store = ProxyStore(root)
runtime = CoreRuntime('sing-box', Path(os.environ['VUI_BIN_DIR'])/'sing-box', root/'runtime')
checks = 0
try:
    for security in ('tls', 'reality'):
        profile = {'schema_version':1, 'protocol':'vless', 'name':'Synthetic',
            'server':'8.8.8.8', 'server_port':443,
            'uuid':'11111111-1111-1111-1111-111111111111',
            'transport':'tcp', 'encryption':'none',
            'flow':'' if security=='tls' else 'xtls-rprx-vision',
            'tls':{'mode':security,'server_name':'synthetic-upstream.example.test','alpn':[],
                'fingerprint':'' if security=='tls' else 'chrome',
                'public_key':'' if security=='tls' else base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('='),
                'short_id':'' if security=='tls' else '0123456789abcdef'}}
        created = store.save_upstream(profile, store.read_snapshot()['revision'])
        selected = created['upstream_id']
        for local, relay in ((False, False),(True, False),(False, True),(True, True)):
            current = store.read_snapshot()
            settings = current['settings']; settings.pop('local_password_set')
            settings.update(local_proxy_enabled=local, relay_enabled=relay,
                selected_upstream_id=selected, local_username='synthetic')
            store.save_draft(settings,current['revision'],local_password='synthetic-password-only')
            snapshot = store.load_draft()
            endpoint = VerifiedEndpoint(selected,snapshot['revision'],'8.8.8.8',('8.8.8.8',),0)
            compiled = compile_proxy_policy({'log':{'level':'warn'},'inbounds':[],
                'outbounds':[{'type':'direct','tag':'direct'}],'route':{'final':'direct'}},
                [],snapshot,endpoint=endpoint,self_addresses=('127.0.0.1',))
            result = runtime.apply(compiled.config, activate=False)
            assert result['valid'] and result['applied'] is False and not result['running']
            assert store.read_snapshot()['active'] is None
            checks += 1
finally:
    runtime.close()
print(json.dumps({'proxy_policy_config_checks':checks,'proxy_resolution_worker':'bounded numeric-only',
    'proxy_runtime_applied':False,'proxy_double_hop_tested':False}))
'''


def installed_proxy_policy_smoke(python: Path, payload: Path, root: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix='.proxy-policy-smoke-', dir=root) as temporary:
        env = release_tools.child_env(payload, Path(temporary) / 'data')
        result = subprocess.run([str(python), '-B', '-c', PROBE], cwd=payload,
                                env=env, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError('Installed proxy policy check failed: ' + result.stderr[-8192:])
        evidence = json.loads(result.stdout)
        if (evidence.get('proxy_policy_config_checks') != 8
                or evidence.get('proxy_runtime_applied') is not False
                or evidence.get('proxy_double_hop_tested') is not False
                or evidence.get('proxy_resolution_worker') != 'bounded numeric-only'):
            raise AssertionError('Installed proxy policy evidence was incomplete')
        return evidence

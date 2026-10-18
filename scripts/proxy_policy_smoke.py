"""Consume installed M2 modules and fixed core without applying a runtime."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from app import release_tools

EXPECTED = {
    'proxy_policy_config_checks': 8,
    'proxy_policy_mixed_config_checks': 8,
    'proxy_policy_same_config_distinct_identities': 4,
    'proxy_resolution_worker': 'bounded numeric-only',
    'proxy_runtime_applied': False,
    'proxy_double_hop_tested': False,
}

PROBE = r'''
import base64, copy, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from app.services import proxy_resolution as resolution
from app.services.proxy_policy import AddressPolicy, PolicyError, VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from app.services.core_runtime import CoreRuntime
from app.services.core_manager import SingBoxAdapter
from app.services.proxy_policy import NAMESPACE, LOCAL_TAG

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
# Fresh synthetic TLS material exists only in this disposable smoke directory.
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-node.example.test')])
now = datetime.now(timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
    .public_key(key.public_key()).serial_number(1).not_valid_before(now-timedelta(days=1))
    .not_valid_after(now+timedelta(days=1))
    .add_extension(x509.SubjectAlternativeName([x509.DNSName('synthetic-node.example.test')]), False)
    .sign(key, hashes.SHA256()))
certificate, private_key = root/'synthetic-cert.pem', root/'synthetic-key.pem'
certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
private_key.chmod(0o600)
rows = [SimpleNamespace(id=identifier, core='sing-box', protocol='vless',
    port=24440+identifier, routing_mode=mode, enable=True, expiry_time=0,
    remark='Synthetic', tag='synthetic-node-' + str(identifier),
    settings={'users':[{'uuid':'22222222-2222-4222-8222-222222222222'}]},
    stream_settings={'tls':{'enabled':True, 'server_name':'synthetic-node.example.test',
        'certificate_path':str(certificate), 'key_path':str(private_key)}})
    for identifier, mode in ((1,'direct'),(2,'relay'))]
base = SingBoxAdapter().build_config(rows)
checks = mixed_checks = same_config_identities = 0
# Any accidental launch must fail rather than turn a configuration check into
# runtime acceptance. Only CoreRuntime.check can invoke the pinned binary.
def forbidden_launch(*args, **kwargs):
    raise AssertionError('Configuration smoke attempted to start a core')
runtime._launch = forbidden_launch
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
            mixed = compile_proxy_policy(base, rows, snapshot, endpoint=endpoint,
                self_addresses=('127.0.0.1',))
            protected = [NAMESPACE + 'ingress-2'] + ([LOCAL_TAG] if local else [])
            assert mixed.policy_version == 2 and mixed.source_fingerprint
            assert [item['mode'] for item in mixed.plan_manifest] == ['direct', 'relay']
            assert mixed.config['inbounds'][0] == base['inbounds'][0]
            assert all(rule['inbound'] == protected for rule in mixed.config['route']['rules'])
            result = runtime.apply(mixed.config, activate=False)
            assert result['valid'] and result['applied'] is False and not result['running']
            assert store.read_snapshot()['active'] is None
            mixed_checks += 1
            if not relay:
                inherited = copy.deepcopy(rows); inherited[0].routing_mode = 'inherit'
                equivalent = compile_proxy_policy(base, inherited, snapshot, endpoint=endpoint,
                    self_addresses=('127.0.0.1',))
                assert equivalent.config == mixed.config and equivalent.identity != mixed.identity
                same_config_identities += 1
finally:
    runtime.close()
print(json.dumps({'proxy_policy_config_checks':checks,
    'proxy_policy_mixed_config_checks':mixed_checks,
    'proxy_policy_same_config_distinct_identities':same_config_identities,
    'proxy_resolution_worker':'bounded numeric-only',
    'proxy_runtime_applied':False,'proxy_double_hop_tested':False}))
'''


def installed_proxy_policy_smoke(python: Path, payload: Path, root: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix='.proxy-policy-smoke-', dir=root) as temporary:
        env = release_tools.child_env(payload, Path(temporary) / 'data')
        result = subprocess.run([str(python), '-B', '-c', PROBE], cwd=payload,
                                env=env, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError('Installed proxy policy check failed: ' + result.stderr[-8192:])
        try:
            evidence = json.loads(result.stdout)
            if (not isinstance(evidence, dict) or evidence != EXPECTED
                    or any(type(evidence[key]) is not type(value) for key, value in EXPECTED.items())):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise AssertionError('Installed proxy policy evidence was incomplete') from None
        return evidence

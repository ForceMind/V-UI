"""Fixed real core validation; requires explicitly provisioned test binaries."""
import os
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.api import inbounds as inbound_api
from app.certificates.models import CertificateBinding
from app.models import database
from app.services.core_manager import SingBoxAdapter, XrayAdapter, core_manager
from app.services.core_runtime import CoreRuntime, CoreError, atomic_write
import test_auth as auth_tests
import test_certificates as certificate_fixtures


class DiagnosticXrayRuntime(CoreRuntime):
    """Capture only this synthetic fixture's failed launch; production stays quiet."""

    def _listeners_ready(self, config, sockets):
        self._diagnostic_requested = [
            {'address': row.get('listen'), 'port': row.get('port'),
             'protocol': row.get('protocol'), 'network': row.get('streamSettings', {}).get('network')}
            for row in config.get('inbounds', [])]
        self._diagnostic_observed = [
            {'address': item.laddr.ip, 'port': item.laddr.port,
             'type': int(item.type), 'status': item.status}
            for item in sockets if item.laddr]
        return super()._listeners_ready(config, sockets)

    def _launch(self, path):
        log = self.root.parent / 'synthetic-xray-launch.log'
        original_popen = subprocess.Popen
        self._diagnostic_requested = self._diagnostic_observed = []
        with log.open('wb') as output:
            log.chmod(0o600)

            def capture(*args, **kwargs):
                kwargs.update(stdout=output, stderr=subprocess.STDOUT)
                return original_popen(*args, **kwargs)

            try:
                with patch('app.services.core_runtime.subprocess.Popen', side_effect=capture):
                    return super()._launch(path)
            except (CoreError, OSError, ValueError) as exc:
                output.flush()
                # Read a bounded complete diagnostic. Oversized output is
                # suppressed rather than truncating potentially sensitive PEM.
                with log.open('rb') as source:
                    raw = source.read(65537)
                if len(raw) > 65536:
                    text = '[oversized synthetic core log suppressed]'
                else:
                    text = raw.decode(errors='replace')
                    text = re.sub(r'-----BEGIN\b[\s\S]*?(?:-----END [^-]+-----|\Z)', '[PEM redacted]', text)
                    text = text.replace(str(self.root.parent), '[fixture]')
                    text = re.sub(r'(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '[uuid]', text)
                    text = re.sub(r'(?i)\b(?:[0-9a-f]{64}|[0-9a-f]{32})\b', '[identity]', text)
                    text = re.sub(r'(?i)(password|token|secret|private.?key)(["\s:=]+)[^\s,;}]+', r'\1\2[redacted]', text)
                    text = text[-6000:]
                print('Synthetic Xray launch failure: ' + json.dumps({
                    'reason': str(exc), 'requested': self._diagnostic_requested,
                    'observed_owned_sockets': self._diagnostic_observed,
                    'core_output': text}, ensure_ascii=False), flush=True)
                raise


@unittest.skipUnless(os.getenv('VUI_TEST_CORES'), 'real binary path not provided')
class PinnedCoreTests(unittest.TestCase):
    def test_real_xray_and_singbox_check_positive_and_negative(self):
        with tempfile.TemporaryDirectory(prefix='vui-real-check-') as tmp:
            for name, adapter, settings in (
                ('xray', XrayAdapter(), {'clients': [{'id': '11111111-1111-1111-1111-111111111111'}], 'decryption': 'none'}),
                ('sing-box', SingBoxAdapter(), {'users': [{'uuid': '11111111-1111-1111-1111-111111111111'}]}),
            ):
                with self.subTest(core=name):
                    item = SimpleNamespace(enable=True, protocol='vless', port=18443, tag='test',
                                           settings=settings, stream_settings={})
                    config = adapter.build_config([item])
                    runtime = CoreRuntime(name, Path(os.environ['VUI_TEST_CORES']) / name, Path(tmp) / name)
                    self.addCleanup(runtime.close)
                    self.assertFalse(runtime.apply(config, activate=False)['applied'])
                    path = Path(tmp) / 'bad.json'
                    atomic_write(path, b'{this is not json')
                    with self.assertRaisesRegex(CoreError, "^Candidate configuration was rejected by the pinned core$"):
                        runtime.check(path)
                    print('Pinned core positive/negative config check:', name)


@unittest.skipUnless(os.getenv('VUI_TEST_CORES'), 'real binary path not provided')
class XrayManagedTLSRealTests(unittest.TestCase):
    """The existing Actions real-core step executes the full certificate consumer.

    CA issuance is synthetic and the unrelated proxy-policy gate is mocked;
    Xray validation, process ownership/restart/recovery and verified TLS sockets
    consume the actual managed files. No browser or external CA is involved.
    """
    setUp = certificate_fixtures.CertificateTests.setUp
    login = auth_tests.AuthenticationTests.login
    issue = certificate_fixtures.CertificateTests.issue
    due = certificate_fixtures.CertificateTests.due

    def test_xray_managed_tls_renewal_failure_stopped_retry_and_recovery(self):
        certificate = self.issue(domain='xray.example.test')
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        root = Path(self.temp.name)
        adapter = XrayAdapter()
        runtime = DiagnosticXrayRuntime('xray', Path(os.environ['VUI_TEST_CORES']) / 'xray', root / 'xray-runtime')
        self.addCleanup(runtime.close)
        adapter._runtime = runtime

        def apply(core, *, activate=True, **kwargs):
            self.assertEqual(core, 'xray')
            with database.SessionLocal() as db:
                rows = db.query(database.Inbound).filter_by(core='xray').all()
                return runtime.apply(adapter.build_config(rows), activate=activate)

        trusted = ssl.create_default_context(cadata=self.provider.ca.public_bytes(serialization.Encoding.PEM).decode())

        def serial(name='xray.example.test'):
            with socket.create_connection(('127.0.0.1', port), timeout=4) as connection:
                with trusted.wrap_socket(connection, server_hostname=name) as connection:
                    return x509.load_der_x509_certificate(connection.getpeercert(binary_form=True)).serial_number

        with patch.dict(core_manager.adapters, {'xray': adapter}), \
             patch.object(core_manager, 'validate_inbound_change'), \
             patch.object(core_manager, 'apply_database', side_effect=apply), \
             patch.object(inbound_api, 'apply_checked', side_effect=apply):
            created = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
                'core': 'xray', 'protocol': 'vless', 'port': port,
                'profile': {'security': 'tls', 'transport': 'raw'}, 'certificate_id': certificate})
            self.assertEqual(created.status_code, 200, created.text)
            identity = created.json()['inbound']['id']
            target = f'inbound:{identity}'
            first = serial()
            self.assertTrue(runtime.status()['running'])
            with self.assertRaises(ssl.SSLCertVerificationError):
                serial('wrong.example.test')
            with database.SessionLocal() as db:
                settings = json.dumps(db.get(database.Inbound, identity).settings, sort_keys=True)
            state = self.client.get(f'/api/inbounds/{identity}/editor').json()
            edited = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                'remark': 'synthetic-edited', 'profile': state['profile'], 'certificate_id': certificate,
                'expected_instance_revision': state['mutation_revision']})
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(serial(), first)
            self.due(certificate)
            self.manager.process_once()
            renewed = serial()
            self.assertNotEqual(renewed, first)
            revision = self.manager.material(certificate)[2]
            with database.SessionLocal() as db:
                self.assertEqual(db.get(CertificateBinding, target).applied_revision, revision)
            # Failed issuance keeps the serving certificate and committed core.
            self.provider.failure = True
            self.due(certificate)
            failed = self.manager.renew(certificate)
            self.manager.process_once()
            self.assertEqual(self.manager.job(failed['id'])['state'], 'failed')
            self.assertEqual(serial(), renewed)
            self.assertEqual(self.manager.material(certificate)[2], revision)
            self.provider.failure = False
            # A stopped node stays stopped when a new desired revision appears.
            runtime.stop()
            self.due(certificate)
            self.manager.process_once()
            self.assertFalse(runtime.status()['running'])
            with database.SessionLocal() as db:
                binding = db.get(CertificateBinding, target)
                self.assertEqual(binding.error, 'CORE_STOPPED_PENDING_APPLY')
                self.assertEqual(binding.applied_revision, revision)
            self.assertNotEqual(self.manager.material(certificate)[2], revision)
            apply('xray')
            self.assertEqual(self.manager.apply_existing(certificate), [{'target': target, 'applied': True}])
            restarted = serial()
            self.assertNotEqual(restarted, renewed)
            # Recover the committed Xray configuration, including renewed files.
            runtime.close()
            runtime.recover()
            self.assertEqual(serial(), restarted)
            with database.SessionLocal() as db:
                self.assertEqual(json.dumps(db.get(database.Inbound, identity).settings, sort_keys=True), settings)
                self.assertEqual(db.get(CertificateBinding, target).applied_revision,
                                 self.manager.material(certificate)[2])
            print('Pinned Xray VLESS/raw managed TLS: verified SNI, renewal, failed issuance, stopped retry and recovery')


if __name__ == '__main__': unittest.main()

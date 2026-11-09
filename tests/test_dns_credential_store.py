"""Private DNS authority rotation and filesystem failures, using fake tokens."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.certificates import dns_store
from app.certificates.dns_store import DNSCredentialStore, DNSStoreError


class DNSCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / 'certificates'
        self.store = DNSCredentialStore(self.root)
        self.token = 'fake_DNS_token_' + 'x' * 32

    def create(self):
        return self.store.create(label='测试凭据', zone_name='Example.test',
                                 zone_id='1' * 32, api_token=self.token)

    def test_absent_store_does_not_create_state_and_public_values_have_no_secrets(self):
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.root.exists())
        profile = self.create()
        self.assertEqual(profile['zone_name'], 'example.test')
        self.assertNotIn(self.token, json.dumps(self.store.list()))
        self.assertEqual(set(profile), {'id', 'provider', 'label', 'zone_name', 'revision',
                                        'enabled', 'restore_pending', 'status'})
        for path in self.store.root.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_rotation_preserves_frozen_old_revision_and_rejects_stale_cas(self):
        profile = self.create()
        frozen = self.store.snapshot(profile['id'])
        updated = self.store.replace(profile['id'], expected_revision=profile['revision'], api_token='y' * 40)
        self.assertNotEqual(updated['revision'], profile['revision'])
        self.assertEqual(self.store.snapshot(profile['id'])['api_token'], 'y' * 40)
        self.assertEqual(self.store.snapshot(profile['id'], revision=frozen['revision'])['api_token'], self.token)
        with self.assertRaisesRegex(DNSStoreError, 'REVISION_CONFLICT'):
            self.store.replace(profile['id'], expected_revision=profile['revision'], api_token='z' * 40)

    def test_disabled_profile_blocks_issuance_but_allows_only_cleanup_snapshot(self):
        profile = self.create()
        disabled = self.store.set_enabled(profile['id'], expected_revision=profile['revision'], enabled=False)
        with self.assertRaisesRegex(DNSStoreError, 'DISABLED'):
            self.store.snapshot(profile['id'])
        self.assertEqual(self.store.snapshot(profile['id'], for_cleanup=True)['api_token'], self.token)
        replaced = self.store.replace(profile['id'], expected_revision=disabled['revision'], api_token='z' * 40)
        self.assertFalse(replaced['enabled'])
        with self.assertRaisesRegex(DNSStoreError, 'CONFIRMATION_REQUIRED'):
            self.store.set_enabled(profile['id'], expected_revision=replaced['revision'], enabled=True)

    def test_restore_pending_blocks_cleanup_and_rotation_does_not_enable(self):
        profile = self.create()
        self.store.suspend_restored()
        restored = self.store.list()[0]
        self.assertEqual(restored['status'], 'restore_pending')
        for cleanup in (False, True):
            with self.assertRaisesRegex(DNSStoreError, 'RESTORE_PENDING'):
                self.store.snapshot(profile['id'], for_cleanup=cleanup)
        replaced = self.store.replace(profile['id'], expected_revision=restored['revision'], api_token='z' * 40)
        self.assertTrue(replaced['restore_pending'])
        self.assertFalse(replaced['enabled'])
        validation = self.store.verification_snapshot(profile['id'], expected_revision=replaced['revision'])
        self.assertEqual(validation['api_token'], 'z' * 40)
        self.assertEqual(self.store.list()[0]['status'], 'restore_pending')
        enabled = self.store.set_enabled(profile['id'], expected_revision=replaced['revision'], enabled=True, confirmed=True)
        self.assertEqual(enabled['status'], 'configured')

    def test_concurrent_rotation_publishes_one_revision(self):
        profile = self.create()
        def rotate(token):
            try:
                return self.store.replace(profile['id'], expected_revision=profile['revision'], api_token=token)
            except DNSStoreError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(rotate, ('y' * 40, 'z' * 40)))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertTrue(all(isinstance(value, dict) or value in {
            'DNS_CREDENTIAL_BUSY', 'DNS_CREDENTIAL_REVISION_CONFLICT'} for value in results))

    def test_failed_creation_does_not_publish_partial_profile_or_block_other_profiles(self):
        original = dns_store._write
        def interrupted(fd, name, value, **kwargs):
            if name == 'state.json':
                raise OSError('synthetic interruption')
            return original(fd, name, value, **kwargs)
        with patch.object(dns_store, '_write', interrupted):
            with self.assertRaises(DNSStoreError):
                self.create()
        self.assertEqual(self.store.list(), [])
        self.assertEqual(len(list(self.store.root.glob('pending-*'))), 1)
        self.create()
        self.assertEqual(len(self.store.list()), 1)

    def test_atomic_state_write_failure_retains_previous_revision_and_secret(self):
        profile = self.create()
        original = dns_store._write
        def interrupted(fd, name, value, **kwargs):
            if name == 'state.json':
                raise OSError('synthetic interruption')
            return original(fd, name, value, **kwargs)
        with patch.object(dns_store, '_write', interrupted):
            with self.assertRaises(DNSStoreError):
                self.store.replace(profile['id'], expected_revision=profile['revision'], api_token='y' * 40)
        self.assertEqual(self.store.list()[0]['revision'], profile['revision'])
        self.assertEqual(self.store.snapshot(profile['id'])['api_token'], self.token)

    def test_symlinked_secret_and_broad_permissions_are_refused(self):
        profile = self.create()
        frozen = self.store.snapshot(profile['id'])
        path = self.store.root / profile['id'] / (frozen['revision'] + '.json')
        path.chmod(0o644)
        with self.assertRaisesRegex(DNSStoreError, 'UNSAFE'):
            self.store.snapshot(profile['id'])
        path.chmod(0o600)
        foreign = Path(self.temporary.name) / 'foreign.json'
        foreign.write_bytes(path.read_bytes()); foreign.chmod(0o600)
        path.unlink(); path.symlink_to(foreign)
        before = foreign.read_bytes()
        with self.assertRaises(DNSStoreError):
            self.store.snapshot(profile['id'])
        self.assertEqual(foreign.read_bytes(), before)

    def test_hardlinked_secret_and_duplicate_json_keys_are_refused(self):
        profile = self.create()
        frozen = self.store.snapshot(profile['id'])
        path = self.store.root / profile['id'] / (frozen['revision'] + '.json')
        alias = Path(self.temporary.name) / 'alias'
        os.link(path, alias)
        with self.assertRaisesRegex(DNSStoreError, 'UNSAFE'):
            self.store.snapshot(profile['id'])
        alias.unlink()
        path.write_bytes(b'{"schema":1,"schema":1}')
        with self.assertRaisesRegex(DNSStoreError, 'INVALID'):
            self.store.snapshot(profile['id'])

    def test_unsafe_directory_or_identifier_never_escapes_store(self):
        profile = self.create()
        for selected in ('../outside', profile['id'].upper(), 'x' * 33):
            with self.subTest(selected=selected), self.assertRaises(DNSStoreError):
                self.store.snapshot(selected)
        self.store.root.chmod(0o755)
        with self.assertRaisesRegex(DNSStoreError, 'UNSAFE'):
            self.store.list()

    def test_invalid_or_empty_token_does_not_create_state(self):
        for token in ('', 'short', self.token + '\n', 'X-Auth-Key: example', '密钥' * 20):
            with self.subTest(token_length=len(token)), self.assertRaisesRegex(DNSStoreError, 'INVALID_DNS_TOKEN'):
                self.store.create(label='测试', zone_name='example.test', zone_id='1' * 32, api_token=token)
        self.assertFalse(self.root.exists())


    def test_snapshot_scan_is_read_only_and_never_returns_tokens_or_token_hashes(self):
        import hashlib
        profile = self.create(); old = self.store.snapshot(profile['id'])
        self.store.replace(profile['id'], expected_revision=profile['revision'], api_token='y' * 40)
        for lock in self.store.root.rglob('owner.lock'): lock.unlink()
        before = {path.relative_to(self.store.root).as_posix(): path.read_bytes()
                  for path in self.store.root.rglob('*') if path.is_file()}
        result = self.store.validate_snapshot()
        self.assertEqual(set(result), {profile['id']})
        self.assertEqual(len(result[profile['id']]['revisions']), 2)
        self.assertIn(old['revision'], result[profile['id']]['revisions'])
        rendered = json.dumps(result)
        for secret in (self.token, 'y' * 40):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(hashlib.sha256(secret.encode()).hexdigest(), rendered)
        after = {path.relative_to(self.store.root).as_posix(): path.read_bytes()
                 for path in self.store.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(list(self.store.root.rglob('owner.lock')))

    def test_snapshot_validates_every_immutable_revision_not_only_current(self):
        profile = self.create(); old = self.store.snapshot(profile['id'])
        self.store.replace(profile['id'], expected_revision=profile['revision'], api_token='y' * 40)
        path = self.store.root / profile['id'] / (old['revision'] + '.json')
        path.chmod(0o644)
        self.assertEqual(len(self.store.list()), 1)
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()
        path.chmod(0o600)
        value = json.loads(path.read_text()); value['zone_id'] = '2' * 32
        path.write_text(json.dumps(value))
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()

    def test_snapshot_preserves_private_pending_and_incomplete_write_fragments(self):
        original = dns_store._write
        def interrupted(fd, name, value, **kwargs):
            if name == 'state.json': raise OSError('synthetic interruption')
            return original(fd, name, value, **kwargs)
        with patch.object(dns_store, '_write', interrupted):
            with self.assertRaises(DNSStoreError): self.create()
        pending = next(self.store.root.glob('pending-*'))
        fragment = pending / ('.write-' + 'a' * 32)
        fragment.write_bytes(b'{"api_token":'); fragment.chmod(0o600)
        empty = pending / ('.write-' + 'b' * 32)
        empty.write_bytes(b''); empty.chmod(0o600)
        before = {path.name: path.read_bytes() for path in pending.iterdir()}
        self.assertEqual(self.store.validate_snapshot(), {})
        self.assertEqual(before, {path.name: path.read_bytes() for path in pending.iterdir()})
        fragment.chmod(0o644)
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()
        fragment.chmod(0o600); (pending / 'unknown').write_bytes(b'private')
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()

    def test_snapshot_strict_json_links_unknown_shapes_and_bounds(self):
        profile = self.create(); current = self.store.snapshot(profile['id'])
        directory = self.store.root / profile['id']; path = directory / (current['revision'] + '.json')
        raw = path.read_bytes()
        for corruption in (b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'\xff', b'x' * (dns_store.MAX_JSON + 1)):
            with self.subTest(length=len(corruption)):
                path.write_bytes(corruption)
                with self.assertRaises(DNSStoreError): self.store.validate_snapshot()
        path.write_bytes(raw)
        alias = Path(self.temporary.name) / 'snapshot-alias'; os.link(path, alias)
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()
        alias.unlink()
        foreign = directory / 'unexpected.json'; foreign.write_bytes(b'{}'); foreign.chmod(0o600)
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()
        foreign.unlink()
        state = directory / 'state.json'; value = json.loads(state.read_text()); value['label'] = '\ud800'
        state.write_text(json.dumps(value))
        with self.assertRaises(DNSStoreError): self.store.validate_snapshot()



if __name__ == '__main__':
    unittest.main()

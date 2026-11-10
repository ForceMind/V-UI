"""Fake verified observations only; never build/install/execute a bundle."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import release_tools as tools
from app.core_updates.application import bundle_artifacts
from app.core_updates.artifacts import TARGETS

VERSIONS = {'sing-box': '1.14.2', 'xray': '26.3.27'}


def observations(target='x86_64-gnu'):
    files = {'cores/' + target.split('-', 1)[0] + '/' + core:
             {'sha256': ('b' if core == 'xray' else 'c') * 64, 'size': 10, 'mode': 0o700}
             for core in VERSIONS}
    return ({'schema': 1, 'kind': 'release', 'platform': tools.PLATFORM,
             'source_commit': 'a'*40, 'version': '0.4.23', 'targets': [target], 'files': files},
            {'runtime_key': target, 'manifest_sha256': 'd'*64})


class ApplicationCoreTests(unittest.TestCase):
    def wrap(self, manifest=None, ready=None, **kwargs):
        default_manifest, default_ready = observations()
        return bundle_artifacts(Path('/verified/payload'),
            default_manifest if manifest is None else manifest,
            default_ready if ready is None else ready,
            versions=kwargs.pop('versions', VERSIONS), owner=kwargs.pop('owner', os.geteuid()), **kwargs)

    def test_all_four_targets_keep_exact_bundle_identity_and_private_mode(self):
        for target in TARGETS:
            manifest, ready = observations(target)
            values = self.wrap(manifest, ready)
            for core, value in values.items():
                self.assertEqual(value.path, Path('/verified/payload/cores') / target.split('-')[0] / core)
                self.assertEqual(value.reference.target, target)
                self.assertEqual(value.reference.app_source, 'a'*40)
                self.assertEqual(value.reference.payload_manifest_sha256, 'd'*64)
                self.assertEqual(value.version, VERSIONS[core])
                self.assertNotEqual(value.version, manifest['version'])
                self.assertEqual((value.owner, value.mode), (os.geteuid(), 0o700))

    def test_wrong_application_semantics_or_source_rejected(self):
        for field, value in [('schema', True), ('schema', 2), ('kind', 'backup'),
                             ('platform', 'other'), ('source_commit', 'a'*39),
                             ('source_commit', 'A'*40), ('source_commit', None)]:
            manifest, ready = observations();manifest[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.wrap(manifest, ready)

    def test_runtime_target_and_manifest_identity_are_required(self):
        for field, value in [('runtime_key', 'x86_64'), ('runtime_key', None),
                             ('manifest_sha256', None), ('manifest_sha256', 'a'*63)]:
            manifest, ready = observations();ready[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.wrap(manifest, ready)

    def test_supported_full_target_must_be_in_verified_manifest(self):
        for targets in ([], ['aarch64-gnu'], ['x86_64-gnu']*2,
                        ['x86_64-gnu', 'unknown'], ('x86_64-gnu',), [False]):
            manifest, ready = observations();manifest['targets'] = targets
            with self.subTest(targets=targets), self.assertRaises(ValueError):self.wrap(manifest, ready)

    def test_complete_two_core_versions_and_verified_owner_required(self):
        for kwargs in ({'versions': {}}, {'versions': {'xray': '26.3.27'}},
                       {'versions': dict(VERSIONS, xray='latest')},
                       {'owner': True}, {'owner': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):self.wrap(**kwargs)

    def test_binary_record_mode_size_digest_and_member_are_exact(self):
        for update in ({'mode': 0o755}, {'mode': True}, {'size': True},
                       {'size': 0}, {'sha256': 'bad'}, {'extra': 'ignored'}):
            manifest, ready = observations();manifest['files']['cores/x86_64/xray'].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):self.wrap(manifest, ready)
        manifest, ready = observations();del manifest['files']['cores/x86_64/xray']
        with self.assertRaises(ValueError):self.wrap(manifest, ready)

    def test_descriptors_freeze_facts_without_mutating_caller_maps(self):
        manifest, ready = observations();original = deepcopy((manifest, ready, VERSIONS))
        result = self.wrap(manifest, ready)
        self.assertEqual((manifest, ready, VERSIONS), original)
        manifest['files'].clear();ready.clear()
        self.assertEqual(result['xray'].reference.binary_size, 10)

    def test_unsafe_payload_and_anchor_paths_are_rejected(self):
        manifest, ready = observations()
        for payload in (Path('relative'), Path('/verified/../payload')):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                bundle_artifacts(payload, manifest, ready, versions=VERSIONS, owner=os.geteuid())
        with self.assertRaises(ValueError):self.wrap(anchor=Path('/different'))


class ActiveVerifiedReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vui-active-observation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.release = self.root/'releases/fixture-one'
        (self.release/'payload').mkdir(parents=True)
        self.raw = b'{"fake": true}\n'
        (self.release/'payload/MANIFEST.json').write_bytes(self.raw)
        self.current = {'release_id': 'fixture-one'}
        (self.root/'CURRENT.json').write_text(json.dumps(self.current))
        self.manifest = {'release_id': 'fixture-one'}
        self.ready = {'manifest_sha256': hashlib.sha256(self.raw).hexdigest()}

    def test_reuses_same_pass_full_payload_and_runtime_observations(self):
        with patch.object(tools, 'verify_payload', return_value=self.manifest) as payload, \
                patch.object(tools, 'verify_runtime', return_value=self.ready) as runtime:
            result = tools.active_verified(self.root)
        self.assertEqual(result[:2], (self.release, self.current))
        self.assertIs(result[2], self.manifest);self.assertIs(result[3], self.ready)
        payload.assert_called_once_with(self.release/'payload')
        runtime.assert_called_once_with(self.release)

    def test_legacy_active_keeps_two_result_contract_and_one_verification(self):
        with patch.object(tools, 'verify_payload', return_value=self.manifest) as payload, \
                patch.object(tools, 'verify_runtime', return_value=self.ready) as runtime:
            self.assertEqual(tools.active(self.root), (self.release, self.current))
        self.assertEqual((payload.call_count, runtime.call_count), (1, 1))

    def test_manifest_binding_and_release_id_guards_still_fail_closed(self):
        for manifest, ready, error in ((self.manifest, {'manifest_sha256': 'f'*64},
                                        'Release manifest changed after health check'),
                                       ({'release_id': 'different'}, self.ready,
                                        'Release identity mismatch')):
            with patch.object(tools, 'verify_payload', return_value=manifest), \
                    patch.object(tools, 'verify_runtime', return_value=ready), \
                    self.assertRaisesRegex(tools.ReleaseError, error):
                tools.active_verified(self.root)


if __name__ == '__main__':unittest.main()

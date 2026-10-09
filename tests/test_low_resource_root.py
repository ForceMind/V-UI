"""Root fixture safety contracts use synthetic paths; never sudo or systemd."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts import low_resource_root as root

PARENT = 'vuiroot'+'a'*32+'.slice'
COMMIT = 'b'*40


class RootResourceContracts(unittest.TestCase):
    def test_membership_requires_exact_parent_component(self):
        self.assertEqual(root.membership('0::/'+PARENT+'/worker.service', PARENT), '/'+PARENT+'/worker.service')
        for value in ('0::/system.slice/worker.service', '0::/'+PARENT+'evil/worker.service',
                      '0::/../'+PARENT+'/worker.service', '1:cpu:/'+PARENT):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                root.membership(value, PARENT)

    def test_slice_name_rejects_paths_other_units_and_shell_text(self):
        for value in ('system.slice', '../'+PARENT, PARENT+';x', 'vuiroot123.slice', None):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                root.checked_slice(value)

    def test_preflight_rejects_account_before_any_unit_or_cleanup_operation(self):
        with patch.object(root.pwd, 'getpwnam', return_value=object()), patch.object(root, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'account/group'):
                root.preflight(search_paths=[], port=False)
            run.assert_not_called()

    def test_preflight_rejects_group_without_user(self):
        with patch.object(root.pwd, 'getpwnam', side_effect=KeyError), patch.object(root.grp, 'getgrnam', return_value=object()):
            with self.assertRaisesRegex(RuntimeError, 'account/group'):
                root.preflight(search_paths=[], port=False)

    def test_dangling_reserved_or_dropin_or_enablement_links_are_not_empty(self):
        for location in ('reserved', 'unit', 'dropin', 'enablement'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as name:
                base = Path(name); reserved = base/'state'; units = base/'units'; units.mkdir()
                target = {'reserved': reserved, 'unit': units/'v-ui.service',
                          'dropin': units/'v-ui.service.d',
                          'enablement': units/'multi-user.target.wants/v-ui.service'}[location]
                target.parent.mkdir(exist_ok=True); target.symlink_to(base/'missing')
                with self.assertRaisesRegex(RuntimeError, 'Pre-existing reserved'):
                    root.preflight(reserved=[reserved], search_paths=[units], accounts=False, port=False)

    def test_loaded_unit_elsewhere_refuses_even_without_local_files(self):
        with patch.object(root, 'lexists', return_value=False), patch.object(root, 'unit_properties', return_value={'LoadState': 'loaded', 'ActiveState': 'inactive'}):
            with self.assertRaisesRegex(RuntimeError, 'loaded service'):
                root.preflight(reserved=[], search_paths=[], accounts=False, port=False)

    def test_empty_preflight_has_no_mutation(self):
        with patch.object(root, 'lexists', return_value=False), patch.object(root, 'unit_properties', return_value={'LoadState': 'not-found', 'ActiveState': 'inactive'}), patch.object(root, 'run') as run:
            result = root.preflight(reserved=[], search_paths=[], accounts=False, port=False)
            self.assertTrue(result['units_absent']); run.assert_not_called()

    def test_synthetic_bundle_preserves_content_modes_and_other_metadata(self):
        with tempfile.TemporaryDirectory() as name:
            a = Path(name)/'a.zip'; b = Path(name)/'b.zip'
            manifest = {'release_id': '0.4.7-test', 'source_commit': COMMIT,
                        'files': {'code.py': {'sha256': 'synthetic'}}, 'version': '0.4.7'}
            with zipfile.ZipFile(a, 'w') as archive:
                archive.writestr('MANIFEST.json', json.dumps(manifest))
                info = zipfile.ZipInfo('code.py'); info.external_attr = 0o100700 << 16
                archive.writestr(info, b'original-product')
            result = root.synthetic_bundle(a, b, COMMIT)
            self.assertTrue(result['product_members_identical'])
            self.assertNotEqual(result['a_sha256'], result['b_sha256'])
            with zipfile.ZipFile(b) as archive:
                changed = json.loads(archive.read('MANIFEST.json'))
                self.assertEqual({**changed, 'release_id': manifest['release_id']}, manifest)
                self.assertEqual(archive.read('code.py'), b'original-product')
                self.assertEqual(archive.getinfo('code.py').external_attr, 0o100700 << 16)
            with self.assertRaises(FileExistsError):
                root.synthetic_bundle(a, b, COMMIT)

    def test_synthetic_bundle_wrong_source_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as name:
            a = Path(name)/'a.zip'; b = Path(name)/'b.zip'
            with zipfile.ZipFile(a, 'w') as archive:
                archive.writestr('MANIFEST.json', json.dumps({'source_commit': 'wrong', 'release_id': 'a'}))
            with self.assertRaisesRegex(RuntimeError, 'Wrong source'):
                root.synthetic_bundle(a, b, COMMIT)
            self.assertFalse(b.exists())

    def test_stop_timeout_escalates_only_owned_unit_and_verifies_stop(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(command, 12)
            return SimpleNamespace(returncode=0)
        rows = []
        with patch.object(root, 'run', side_effect=run), patch.object(root, 'unit_properties', side_effect=[{'ActiveState': 'deactivating', 'MainPID': '21'}, {'ActiveState': 'inactive', 'MainPID': '0'}]):
            root.bounded_stop('v-ui.service', rows)
        self.assertEqual(calls[1][0], ['systemctl', 'kill', '--kill-whom=all', '--signal=KILL', 'v-ui.service'])
        self.assertEqual(rows[0]['stop_returncode'], 'timeout')
        self.assertTrue(all(call[1]['timeout'] <= 12 for call in calls))

    def test_stop_failure_cannot_pass_cleanup(self):
        with patch.object(root, 'run', return_value=SimpleNamespace(returncode=1)), patch.object(root, 'unit_properties', return_value={'ActiveState': 'active', 'MainPID': '21'}):
            with self.assertRaisesRegex(RuntimeError, 'did not stop'):
                root.bounded_stop('v-ui.service', [])

    def test_root_operation_checks_real_uid_and_membership_before_product_io(self):
        with patch.object(root, 'identity', return_value={'uid': [1000]*4}), patch.object(root.pwd, 'getpwnam') as account:
            with self.assertRaisesRegex(RuntimeError, 'measured root'):
                root.root_operation('seed', PARENT)
            account.assert_not_called()

    def test_service_requires_effective_dropin_and_real_uid(self):
        valid = {'Slice': PARENT, 'ActiveState': 'active', 'MainPID': '5',
                 'ControlGroup': '/'+PARENT+'/v-ui.service',
                 'DropInPaths': '/run/systemd/system/v-ui.service.d/90-vui-resource.conf'}
        process = {'uid': [998]*4, 'cgroup': valid['ControlGroup']}
        for changes in ({'Slice': 'system.slice'}, {'DropInPaths': ''}, {'ActiveState': 'inactive'}):
            with self.subTest(changes=changes), patch.object(root, 'unit_properties', return_value={**valid, **changes}), self.assertRaises(RuntimeError):
                root.service_identity('v-ui.service', PARENT)
        with patch.object(root, 'unit_properties', return_value=valid), patch.object(root, 'identity', return_value=process), patch.object(root.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=998)):
            self.assertEqual(root.service_identity('v-ui.service', PARENT)['process'], process)


if __name__ == '__main__':
    unittest.main()

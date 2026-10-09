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


class RootResourceRegressionTests(unittest.TestCase):
    def test_nested_lookalike_parent_is_rejected(self):
        with self.assertRaises(RuntimeError):
            root.membership('0::/unrelated.slice/'+PARENT+'/worker.service', PARENT)

    def test_other_service_leaf_and_inherited_dropin_are_rejected(self):
        valid = {'Slice': PARENT, 'ActiveState': 'active', 'MainPID': '5',
                 'ControlGroup': '/'+PARENT+'/other.service',
                 'DropInPaths': '/run/systemd/system/v-ui.service.d/90-vui-resource.conf'}
        with patch.object(root, 'unit_properties', return_value=valid), patch.object(root, 'identity', return_value={'uid': [998]*4, 'cgroup': valid['ControlGroup']}), patch.object(root.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=998)):
            with self.assertRaises(RuntimeError):
                root.service_identity('v-ui.service', PARENT)
        valid['DropInPaths'] += ' /usr/lib/systemd/system/v-.service.d/other.conf'
        with patch.object(root, 'unit_properties', return_value=valid), self.assertRaises(RuntimeError):
            root.service_identity('v-ui.service', PARENT)

    def test_all_applicable_global_or_dash_dropins_refused(self):
        for name in ('service.d', 'socket.d', 'v-.service.d', 'v-ui-.service.d', 'v-.socket.d', 'v-ui-.socket.d'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory); (base/name).mkdir()
                with self.assertRaisesRegex(RuntimeError, 'Pre-existing reserved'):
                    root.preflight(reserved=[], search_paths=[base], accounts=False, port=False)

    def test_worker_report_refuses_symlink_and_hardlink(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base/'source'; source.write_text('{"value":1}')
            link = base/'link'; link.symlink_to(source)
            with self.assertRaises(OSError):
                root.worker_report(link, os.getuid())
            self.assertEqual(root.worker_report(source, os.getuid()), {'value': 1})
            link.unlink(); os.link(source, link)
            with self.assertRaises(RuntimeError):
                root.worker_report(source, os.getuid())

    def test_bad_restore_exact_product_error_and_complete_data_preservation(self):
        import os
        import sqlite3
        from app import release_tools
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); data = base/'data'; data.mkdir(mode=0o700)
            marker = data/'resource-fixture-marker.txt'; marker.write_text('after-upgrade\n')
            with sqlite3.connect(data/'v-ui.db') as db:
                db.execute('CREATE TABLE fixture (id INTEGER)')
            (base/'CURRENT.json').write_text('{"release_id":"b"}')
            archive = base/'before-upgrade-123.zip'; archive.write_bytes(b'synthetic archive')
            try:
                with release_tools.verified_snapshot(archive, '0'*64):
                    pass
            except release_tools.ReleaseError as error:
                message = 'Operation refused: '+str(error)
            self.assertEqual(message, 'Operation refused: Archive checksum mismatch')
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(root, 'ROOT', base), patch.object(root, 'identity', return_value={'uid': [0]*4}), patch.object(root.pwd, 'getpwnam', return_value=account), patch.object(root, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr=message)):
                result = root.root_operation('bad-restore', PARENT, archive.name)
                self.assertEqual(result['rejection_reason'], 'Archive checksum mismatch')
                self.assertEqual(marker.read_text(), 'after-upgrade\n')
            with patch.object(root, 'ROOT', base), patch.object(root, 'identity', return_value={'uid': [0]*4}), patch.object(root.pwd, 'getpwnam', return_value=account), patch.object(root, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='permission denied')):
                with self.assertRaisesRegex(RuntimeError, 'unrelated reason'):
                    root.root_operation('bad-restore', PARENT, archive.name)

    def test_no_cleanup_rights_when_slice_was_never_created(self):
        with patch.object(root, 'run') as run:
            report = {'created_paths': []}
            self.assertTrue(root.cleanup_owned(PARENT, 'worker.service', report))
            run.assert_not_called()

    def test_root_receipts_preserve_exact_phase_and_reject_escape(self):
        gate = object.__new__(root.Gate); gate.parent = PARENT
        gate.config = {'source_commit': COMMIT}; gate.report = {'installer_entries': []}
        with tempfile.TemporaryDirectory() as directory:
            gate.output = Path(directory)/'report.json'
            entry = {'source_commit': COMMIT, 'uid': [0]*4,
                     'cgroup': '/'+PARENT+'/'+PARENT.removesuffix('.slice')+'.service',
                     'pid': 10, 'starttime_ticks': 123}
            gate.installer_receipt('initial_install', 'ROOT_ENTRY '+json.dumps(entry)+'\r\n')
            self.assertEqual(gate.report['installer_entries'][0]['pid'], 10)
            for changed in ({'uid': [1000]*4}, {'cgroup': '/system.slice/escape.service'}, {'source_commit': 'c'*40}):
                with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                    gate.installer_receipt('implicit_repeat', 'ROOT_ENTRY '+json.dumps({**entry, **changed}))


def complete_report():
    process = {'pid': 11, 'starttime_ticks': 1, 'uid': [998]*4, 'cgroup': '/'+PARENT+'/v-ui.service'}
    identity = {'device': 1, 'inode': 2}
    limits = {'memory.max': 536870912, 'memory.swap.max': 0, 'cpu.max': '100000 100000'}
    def service(unit):
        return {'properties': {'Slice': PARENT}, 'process': {**process, 'cgroup': '/'+PARENT+'/'+unit}}
    def state(release):
        return {'current': {'release_id': release}, 'releases': ['a'] if release == 'a' else ['a', 'b'],
                'ready_releases': ['a'] if release == 'a' else ['a', 'b'],
                'services': {unit: service(unit) for unit in root.UNITS[:2]}}
    before = state('a'); during = state('a'); during['releases'].append('b')
    after = state('b'); after['services']['v-ui.service']['process']['pid'] = 22
    bad = {'returncode': 1, 'backup_name': 'before-upgrade-1.zip', 'backup_sha256': 'c'*64,
           'marker_sha256': 'd'*64, 'current_unchanged': True, 'database_integrity': 'ok',
           'rejection_reason': 'Archive checksum mismatch'}
    details = {'root_install_and_http01': state('a'), 'restored_new_login_and_membership': after,
               'fresh_directory_upgrade': {'before': before, 'after': after, 'staging_live_samples': [during], 'backup_name': bad['backup_name']},
               'stopped_bad_digest_preserves_data': bad,
               'stopped_verified_restore': {**bad, 'returncode': 0, 'marker_sha256': 'e'*64}}
    stages = [{'name': name, 'monotonic': i+1, 'parent_identity': identity, 'limits': limits,
               'details': details.get(name, {}), 'metrics': {'memory.peak': i+1,
               'memory.events': {'oom': 0, 'oom_kill': 0, 'max': 0}, 'cpu.stat': {'usage_usec': i+1}}}
              for i, name in enumerate(root.STAGES)]
    unit = PARENT.removesuffix('.slice')+'.service'
    return {'source_commit': COMMIT, 'slice': PARENT, 'worker_unit': unit, 'worker_exit': 0,
            'cleanup_confirmed': True, 'parent_populated': 0, 'remaining_processes': [],
            'parent_identity': identity, 'limits': limits,
            'package': {'source_commit': COMMIT, 'a_release_id': 'a', 'b_release_id': 'b',
                        'a_sha256': 'a'*64, 'b_sha256': 'b'*64, 'product_members_identical': True, 'synthetic_release_only': True},
            'worker': {'source_commit': COMMIT, 'slice': PARENT, 'outcome': 'passed', 'complete': True,
                       'worker': {'uid': [1000]*4, 'cgroup': '/'+PARENT+'/'+unit}, 'parent_identity': identity, 'stages': stages,
                       'installer_entries': [{'phase': phase, 'source_commit': COMMIT, 'uid': [0]*4, 'cgroup': '/'+PARENT+'/'+unit}
                           for phase in ('initial_install', 'implicit_repeat', 'same_bundle_upgrade', 'fresh_directory_upgrade')]},
            'final_metrics': copy.deepcopy(stages[-1]['metrics'])}


class RootResultValidationTests(unittest.TestCase):
    def test_complete_report(self):
        root.validate_result(complete_report(), COMMIT)

    def test_missing_stage_receipt_role_or_cleanup_rejected(self):
        mutations = [lambda r: r['worker']['stages'].pop(), lambda r: r['worker']['installer_entries'].pop(),
                     lambda r: r.update(parent_populated=1), lambda r: r.update(cleanup_confirmed=False),
                     lambda r: r['worker']['stages'][1]['details']['services'].pop('v-ui-http01.service')]
        for mutate in mutations:
            report = complete_report(); mutate(report)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError):
                root.validate_result(report, COMMIT)

    def test_regressed_total_or_changed_parent_rejected(self):
        for change in ('peak', 'cpu', 'parent'):
            report = complete_report()
            if change == 'peak':report['final_metrics']['memory.peak'] = 1
            if change == 'cpu':report['final_metrics']['cpu.stat']['usage_usec'] = 1
            if change == 'parent':report['worker']['parent_identity'] = {'device': 1, 'inode': 3}
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                root.validate_result(report, COMMIT)

    def test_prepared_b_or_dead_a_does_not_prove_live_stage(self):
        for change in ('prepared', 'generation', 'current'):
            report = complete_report(); details = report['worker']['stages'][4]['details']
            if change == 'prepared':details['staging_live_samples'][0]['ready_releases'].append('b')
            if change == 'generation':details['staging_live_samples'][0]['services']['v-ui.service']['process']['pid'] = 999
            if change == 'current':details['after']['current']['release_id'] = 'a'
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                root.validate_result(report, COMMIT)

    def test_wrong_backup_or_unchanged_marker_rejected(self):
        for key, value in (('backup_name', 'wrong.zip'), ('marker_sha256', 'd'*64), ('returncode', 1)):
            report = complete_report(); report['worker']['stages'][6]['details'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                root.validate_result(report, COMMIT)

"""Independent launch-evidence adversarial tests: fake procfs, no root/services.

Every executable identity and service account in this module is synthetic.
Nothing starts systemd, executes a product binary, or reads real /proc secrets.
"""
from contextlib import ExitStack
import copy
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from scripts import low_resource_root as root
from scripts import low_resource_root_trace as trace
from test_low_resource_root import COMMIT, PARENT, complete_report


SERVICE_UID = 998
MANAGER_PATH = Path('/usr/lib/systemd/systemd')
EXECUTOR_PATH = Path('/usr/lib/systemd/systemd-executor')
APP_ROOT = Path('/var/lib/v-ui')
RUNTIME_PATH = APP_ROOT / 'releases/a-root-fixture/runtime/python/bin/python3'
SECRET = 'SYNTHETIC_PRIVATE_ARG_MUST_NOT_ESCAPE_20261011'
CONFIGURATION = {'bootstrap_python': str(RUNTIME_PATH),
                 'origin': 'https://fixture.example.invalid:8443',
                 'bind': '127.0.0.1', 'port': 8443,
                 'certificate_mode': 'provided'}


def executable(inode=42, uid=0, mode=stat.S_IFREG | 0o755):
    return {'device': 7, 'inode': inode, 'mode': mode, 'uid': uid,
            'size': 4096, 'mtime_ns': 100_000, 'ctime_ns': 200_000}


def baseline():
    return {'schema_version': 1, 'systemd_version': 255, 'manager_version': '255', 'executor_version': '255',
            'manager': executable(42), 'executor': executable(43)}


def process(unit='v-ui.service', uid=0):
    return {'pid': 3543, 'ppid': 1, 'starttime_ticks': 16557,
            'uid': [uid] * 4, 'cgroup': '/' + PARENT + '/' + unit}


def command(unit='v-ui.service', *, launcher=False):
    mode = 'http01' if unit == 'v-ui-http01.service' else 'panel'
    if launcher:
        return [str(RUNTIME_PATH), '-B', '/usr/local/lib/v-ui/launcher.py', mode]
    if mode == 'http01':
        return [str(RUNTIME_PATH), '-B', '-m', 'app.certificates.http01',
                '--webroot', str(APP_ROOT / 'data/certificates/http-webroot')]
    return [str(RUNTIME_PATH), '-B', '-m', 'app.serve', '--root', str(APP_ROOT),
            '--origin', CONFIGURATION['origin'], '--bind', '127.0.0.1',
            '--port', '8443', '--cert', str(APP_ROOT / 'data/certs/provided-fullchain.pem'),
            '--key', str(APP_ROOT / 'data/certs/provided-privkey.pem')]


def observed(unit='v-ui.service', image='systemd-executor', uid=0,
             template='other'):
    role = 'managed-http01' if unit == 'v-ui-http01.service' else 'managed-panel'
    identity = {'systemd-manager': executable(42),
                'systemd-executor': executable(43),
                'runtime': executable(44, SERVICE_UID),
                'other': executable(99)}[image]
    return {**process(unit, uid), 'role': role, 'execution': {
        'schema_version': 1, 'image': image, 'exe': identity,
        'template': template, 'error': None}}


def service_state(row):
    unit = row['cgroup'].rsplit('/', 1)[1]
    return {'properties': {'Slice': PARENT, 'ActiveState': 'active',
            'MainPID': str(row['pid']), 'ControlGroup': row['cgroup'],
            'DropInPaths': f'/run/systemd/system/{unit}.d/90-vui-resource.conf'},
            'process': {key: row[key] for key in (
                'pid', 'ppid', 'starttime_ticks', 'uid', 'cgroup')},
            'execution': copy.deepcopy(row['execution'])}


def stat_result(identity, *, directory=False):
    return SimpleNamespace(st_dev=identity['device'], st_ino=identity['inode'],
                           st_mode=(stat.S_IFDIR | 0o755) if directory else identity['mode'],
                           st_uid=identity['uid'], st_size=identity['size'],
                           st_mtime_ns=identity['mtime_ns'], st_ctime_ns=identity['ctime_ns'])


class ManagedExecutionObservationTests(unittest.TestCase):
    """Exercise the actual cmdline reader with only metadata calls replaced."""

    def observe(self, row=None, *, argv=None, raw=None, image=None,
                identities=None, configuration=None):
        row = copy.deepcopy(row if row is not None else process())
        image = copy.deepcopy(image if image is not None else executable(43))
        with tempfile.TemporaryDirectory(prefix='vui-fake-proc-') as directory:
            proc = Path(directory)
            pid = proc / str(row['pid']); pid.mkdir()
            if raw is None:
                argv = argv if argv is not None else ['/usr/lib/systemd/systemd-executor']
                raw = ('\0'.join(argv) + '\0').encode()
            (pid / 'cmdline').write_bytes(raw)
            # Poisoned files must never be opened by the observer.
            (pid / 'environ').write_text(SECRET)
            (pid / 'fd').mkdir(); (pid / 'fd' / '3').write_text(SECRET)
            identity = Mock(side_effect=identities) if identities is not None else Mock(return_value=row)
            def metadata(path):
                if Path(path) == RUNTIME_PATH:
                    return executable(44, SERVICE_UID)
                return copy.deepcopy(image)
            original_open = Path.open
            def bounded_read(path, *args, **kwargs):
                self.assertNotIn('environ', path.parts)
                self.assertNotIn('fd', path.parts)
                return original_open(path, *args, **kwargs)
            with patch.object(trace, 'executable_identity', side_effect=metadata), \
                    patch.object(Path, 'open', autospec=True, side_effect=bounded_read):
                result = trace.observed_process(row, PARENT, APP_ROOT, identity, proc,
                    baseline=baseline(), configuration=configuration or CONFIGURATION)
            return result

    def test_manager_and_executor_classification_never_trusts_argv_zero(self):
        for enum, number in (('systemd-manager', 42), ('systemd-executor', 43)):
            with self.subTest(image=enum):
                row = self.observe(argv=['arbitrary-argv-zero', SECRET], image=executable(number))
                self.assertEqual(row['execution']['image'], enum)
                self.assertEqual(row['uid'], [0] * 4)
                self.assertNotIn(SECRET, json.dumps(row))
                self.assertNotIn('arbitrary-argv-zero', json.dumps(row))

    def test_python_with_systemd_argv_and_launcher_or_core_never_becomes_systemd(self):
        cases = ([str(MANAGER_PATH)], [str(EXECUTOR_PATH)], command(launcher=True),
                 [str(APP_ROOT / 'releases/a/payload/cores/xray'), '-c', SECRET],
                 ['/tmp/systemd-executor'], ['/tmp/systemd'])
        for argv in cases:
            with self.subTest(argv=argv):
                row = self.observe(argv=argv, image=executable(99))
                self.assertEqual(row['execution']['image'], 'other')
                self.assertNotIn(SECRET, json.dumps(row))

    def test_exact_runtime_panel_http01_and_launcher_templates(self):
        for unit, template in (('v-ui.service', 'panel'),
                               ('v-ui-http01.service', 'http01')):
            for launcher in (False, True):
                with self.subTest(unit=unit, launcher=launcher):
                    row = self.observe(process(unit, SERVICE_UID), argv=command(unit, launcher=launcher),
                                       image=executable(44, SERVICE_UID))
                    self.assertEqual(row['execution']['image'], 'runtime')
                    self.assertEqual(row['execution']['template'],
                                     'launcher-' + template if launcher else template)
                    self.assertEqual(row['execution']['error'], None)

    def test_runtime_templates_reject_wrong_binary_leaf_arguments_and_suffix(self):
        for unit in ('v-ui.service', 'v-ui-http01.service'):
            good = command(unit)
            variants = [good + ['--injected', SECRET], ['/usr/bin/python3', *good[1:]],
                        [str(RUNTIME_PATH), '-B', '-m', 'app.serve']]
            for argv in variants:
                with self.subTest(unit=unit, argv=argv):
                    row = self.observe(process(unit, SERVICE_UID), argv=argv,
                                       image=executable(44, SERVICE_UID))
                    self.assertEqual(row['execution']['template'], 'other')
                    self.assertNotIn(SECRET, json.dumps(row))

    def test_unknown_empty_invalid_and_oversized_cmdline_keep_root_identity(self):
        for raw in (b'', b'\xff\0', b'x' * (trace.MAX_ROW_BYTES + 1),
                    ('unknown\0' + SECRET + '\0').encode()):
            with self.subTest(size=len(raw), prefix=raw[:8]):
                row = self.observe(raw=raw, image=executable(99))
                self.assertIsNotNone(row)
                self.assertEqual(row['uid'], [0] * 4)
                self.assertNotEqual(row['execution']['image'], 'systemd-executor')
                self.assertNotIn(SECRET, json.dumps(row))

    def test_process_identity_races_are_retained_and_never_qualified(self):
        original = process()
        for mutation in ({'pid': 3544}, {'ppid': 2}, {'starttime_ticks': 16558},
                         {'uid': [SERVICE_UID] * 4},
                         {'cgroup': '/' + PARENT + '/v-ui-http01.service'}):
            with self.subTest(mutation=mutation):
                row = self.observe(original, identities=[{**original, **mutation}] * 8)
                self.assertEqual(row['uid'], [0] * 4)
                self.assertEqual(row['execution']['error'], 'identity_changed')

    def test_late_identity_and_executable_races_are_rejected(self):
        original = process()
        for mutation in ({'pid': 3544}, {'ppid': 2}, {'starttime_ticks': 16558},
                         {'uid': [998] * 4},
                         {'cgroup': '/' + PARENT + '/v-ui-http01.service'}):
            with self.subTest(mutation=mutation):
                row = self.observe(original, identities=[original, {**original, **mutation}])
                self.assertEqual(row['uid'], [0] * 4)
                self.assertEqual(row['execution']['error'], 'identity_changed')
        for mutation in ({'device': 8}, {'inode': 99}, {'mtime_ns': 100001},
                         {'ctime_ns': 200001}, {'size': 4097}, {'uid': 998},
                         {'mode': stat.S_IFREG | 0o775}):
            with self.subTest(exe=mutation), tempfile.TemporaryDirectory() as directory:
                proc = Path(directory); (proc / '3543').mkdir()
                (proc / '3543/cmdline').write_bytes(b'systemd-executor\0')
                with patch.object(trace, 'executable_identity',
                        side_effect=[executable(43), {**executable(43), **mutation}]):
                    row = trace.observed_process(original, PARENT, APP_ROOT,
                        lambda *_: original, proc, baseline=baseline(), configuration=CONFIGURATION)
                self.assertEqual(row['uid'], [0] * 4)
                self.assertEqual(row['execution']['error'], 'exe_changed')

    def test_read_failures_keep_a_bounded_error_without_private_exception_text(self):
        for error, expected in ((FileNotFoundError(SECRET), 'process_vanished'),
                                (PermissionError(SECRET), 'read_denied'),
                                (OSError(SECRET), 'observation_failed')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(trace, 'executable_identity', side_effect=error), \
                    tempfile.TemporaryDirectory() as directory:
                proc = Path(directory); pid = proc / '3543'; pid.mkdir()
                (pid / 'cmdline').write_bytes(b'unknown\0')
                row = trace.observed_process(process(), PARENT, APP_ROOT,
                    lambda *_: process(), proc, baseline=baseline(),
                    configuration=CONFIGURATION)
                self.assertEqual(row['uid'], [0] * 4)
                self.assertEqual(row['execution']['error'], expected)
                self.assertNotIn(SECRET, json.dumps(row))

    def test_cmdline_read_is_bounded_and_never_scans_environment_or_file_descriptors(self):
        original = process()
        source = io.BytesIO((SECRET * 10_000).encode())
        read = Mock(wraps=source.read)
        source.read = read
        def opened(path, mode):
            self.assertEqual(path, Path('/fake-proc/3543/cmdline'))
            self.assertEqual(mode, 'rb')
            return source
        with patch.object(Path, 'open', autospec=True, side_effect=opened), \
                patch.object(trace, 'executable_identity', return_value=executable(43)):
            row = trace.observed_process(original, PARENT, APP_ROOT,
                lambda *_: original, Path('/fake-proc'), baseline=baseline(), configuration=CONFIGURATION)
        read.assert_called_once_with(trace.MAX_ROW_BYTES + 1)
        self.assertEqual(row['execution']['error'], 'invalid_cmdline')
        self.assertEqual(row['uid'], [0] * 4)
        self.assertNotIn(SECRET, json.dumps(row))

    def test_identity_escape_runtime_error_is_not_caught_as_a_race(self):
        with self.assertRaisesRegex(RuntimeError, 'escaped measured'):
            self.observe(identities=RuntimeError('escaped measured parent slice'))


def launch_report(sequence, *, unit='v-ui.service', starts=None):
    """Keep the pre-upgrade live-A proof and insert a separate new generation."""
    report = complete_report()
    old_rows = report['upgrade_accounting']['rows'][1:-1]
    samples = copy.deepcopy(old_rows[:2])
    if starts is None:
        starts = [5 + i * .6 for i in range(len(sequence))]
    for entry, start in zip(sequence, starts):
        sample = copy.deepcopy(old_rows[0]['sample'])
        sample.update(started_monotonic=start, state_monotonic=start + .01,
                      metrics_started_monotonic=start + .02,
                      metrics_finished_monotonic=start + .03,
                      finished_monotonic=start + .04)
        if entry is not None:
            row = copy.deepcopy(entry)
            sample['processes'].append(row)
            if row['uid'] == [SERVICE_UID] * 4 and row['execution']['image'] == 'runtime':
                sample['state'] = {'services': {unit: service_state(row)},
                                  'service_transitions': {}, 'observed_subtree': [row]}
                sample['state_error'] = None
        samples.append({'type': 'sample', 'sample': sample})
    finish = starts[-1] + 1
    for index, stage in enumerate(report['worker']['stages'][4:]):
        stage['monotonic'] = finish + index
    report['worker_finished_monotonic'] = finish + 6
    report['upgrade_accounting']['rows'] = [
        report['upgrade_accounting']['rows'][0], *samples,
        {'type': 'finished', 'sample_count': len(samples), 'returncode': 0,
         'error': None, 'command_started_monotonic': 4.2,
         'command_finished_monotonic': starts[-1] - .1}]
    return report


def rows(report):
    return [item['sample'] for item in report['upgrade_accounting']['rows'][1:-1]]


class LaunchSequenceValidationTests(unittest.TestCase):
    def valid(self, *, unit='v-ui.service', manager=False, twice=False):
        pending = observed(unit, 'systemd-manager' if manager else 'systemd-executor')
        closed = observed(unit, 'runtime', SERVICE_UID,
                          'http01' if unit == 'v-ui-http01.service' else 'panel')
        return launch_report([pending] * (2 if twice else 1) + [closed], unit=unit)

    def test_manager_executor_http01_and_two_pending_samples_close(self):
        for unit in ('v-ui.service', 'v-ui-http01.service'):
            for manager in (False, True):
                for twice in (False, True):
                    with self.subTest(unit=unit, manager=manager, twice=twice):
                        report = self.valid(unit=unit, manager=manager, twice=twice)
                        trace.validate(report)
                        root.validate_result(report, COMMIT)
                        pending = rows(report)[2]['processes'][-1]
                        self.assertEqual(pending['uid'], [0] * 4)

    def test_root_python_launcher_core_unknown_and_missing_exe_fail(self):
        for image, template in (('runtime', 'panel'), ('runtime', 'launcher-panel'),
                                ('other', 'other'), ('other', 'panel')):
            report = self.valid(); pending = rows(report)[2]['processes'][-1]
            pending['execution'].update(image=image, template=template,
                                        exe=executable(44 if image == 'runtime' else 99))
            with self.subTest(image=image, template=template), self.assertRaises(RuntimeError):
                trace.validate(report)
        for omission in ('execution', 'exe'):
            report = self.valid(); pending = rows(report)[2]['processes'][-1]
            if omission == 'execution':
                pending.pop('execution')
            else:
                pending['execution']['exe'] = None
            with self.subTest(omission=omission), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_wrong_pending_pid_parent_leaf_uid_and_executable_are_rejected(self):
        mutations = (
            ('parent', lambda p: p.update(ppid=2)),
            ('mixed-uid', lambda p: p.update(uid=[0, 998, 998, 998])),
            ('nonservice-uid', lambda p: p.update(uid=[123] * 4)),
            ('wrong-leaf', lambda p: p.update(cgroup='/' + PARENT + '/other.service')),
            ('wrong-inode', lambda p: p['execution']['exe'].update(inode=99)),
            ('wrong-owner', lambda p: p['execution']['exe'].update(uid=998)),
            ('group-write', lambda p: p['execution']['exe'].update(mode=stat.S_IFREG | 0o775)),
            ('other-write', lambda p: p['execution']['exe'].update(mode=stat.S_IFREG | 0o757)),
            ('directory', lambda p: p['execution']['exe'].update(mode=stat.S_IFDIR | 0o755)),
            ('negative-pid', lambda p: p.update(pid=-1)),
            ('zero-start', lambda p: p.update(starttime_ticks=0)),
        )
        for label, mutate in mutations:
            report = self.valid(); mutate(rows(report)[2]['processes'][-1])
            with self.subTest(case=label), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_successor_must_be_same_identity_and_actual_application(self):
        mutations = (
            ('pid-reused', lambda p: p.update(starttime_ticks=16558)),
            ('new-pid', lambda p: p.update(pid=3544)),
            ('parent', lambda p: p.update(ppid=2)),
            ('leaf', lambda p: p.update(cgroup='/' + PARENT + '/v-ui-http01.service')),
            ('root', lambda p: p.update(uid=[0] * 4)),
            ('mixed-uid', lambda p: p.update(uid=[998, 0, 998, 998])),
            ('other-uid', lambda p: p.update(uid=[999] * 4)),
            ('launcher-only', lambda p: p['execution'].update(template='launcher-panel')),
            ('unknown-template', lambda p: p['execution'].update(template='other')),
            ('unknown-image', lambda p: p['execution'].update(image='other')),
            ('missing-exe', lambda p: p['execution'].update(exe=None)),
            ('errored-exe', lambda p: p['execution'].update(error='exe_changed')),
        )
        for label, mutate in mutations:
            report = self.valid(); sample = rows(report)[-1]
            mutate(sample['processes'][-1])
            sample['state']['observed_subtree'] = [copy.deepcopy(sample['processes'][-1])]
            sample['state']['services']['v-ui.service'] = service_state(sample['processes'][-1])
            with self.subTest(case=label), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_closure_requires_active_matching_mainpid_and_state_evidence(self):
        changes = (
            ('missing-state', lambda s: s.update(state=None, state_error='ServiceUnavailable')),
            ('inactive', lambda s: s['state']['services']['v-ui.service']['properties'].update(ActiveState='inactive')),
            ('mainpid', lambda s: s['state']['services']['v-ui.service']['properties'].update(MainPID='99')),
            ('missing-mainpid', lambda s: s['state']['services']['v-ui.service']['properties'].pop('MainPID')),
            ('other-group', lambda s: s['state']['services']['v-ui.service']['properties'].update(ControlGroup='/elsewhere')),
            ('wrong-dropin', lambda s: s['state']['services']['v-ui.service']['properties'].update(DropInPaths='')),
        )
        for label, mutate in changes:
            report = self.valid(); mutate(rows(report)[-1])
            with self.subTest(case=label), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_pending_cannot_disappear_restart_persist_or_end_without_closure(self):
        pending = observed(); closed = observed(image='runtime', uid=998, template='panel')
        for name, sequence in (
                ('missing-successor', [pending, None]),
                ('missing-middle', [pending, None, closed]),
                ('three-root-samples', [pending, pending, pending, closed]),
                ('end-pending', [pending, pending]),
                ('generation-replaced', [pending, {**closed, 'starttime_ticks': 16558}]),
                ('root-persists', [pending, pending, pending])):
            with self.subTest(case=name), self.assertRaises(RuntimeError):
                trace.validate(launch_report(sequence))

    def test_five_second_bound_is_independent_of_consecutive_sample_budget(self):
        pending = observed(); closed = observed(image='runtime', uid=998, template='panel')
        trace.validate(launch_report([pending, pending, closed], starts=[5, 7.5, 9.96]))
        with self.assertRaises(RuntimeError):
            trace.validate(launch_report([pending, pending, closed], starts=[5, 7.5, 9.961]))

    def test_application_cannot_regress_to_executor_in_same_generation(self):
        closed = observed(image='runtime', uid=998, template='panel')
        with self.assertRaises(RuntimeError):
            trace.validate(launch_report([closed, observed(), closed]))

    def test_earlier_state_root_is_not_overwritten_by_later_nonroot_or_inactive(self):
        for unknown in (False, True):
            for inactive in (False, True):
                report = self.valid()
                sample = rows(report)[2]
                early = copy.deepcopy(sample['processes'][-1])
                if unknown:
                    early['execution']['image'] = 'other'
                    early['execution']['exe'] = executable(99)
                sample['state'] = {'services': {}, 'observed_subtree': [early],
                                   'service_transitions': {}}
                sample['state_error'] = None
                if inactive:
                    sample['processes'].pop()
                else:
                    later = observed(image='runtime', uid=998, template='panel')
                    sample['processes'][-1] = later
                    sample['state']['services']['v-ui.service'] = service_state(later)
                with self.subTest(unknown=unknown, inactive=inactive):
                    if unknown:
                        with self.assertRaises(RuntimeError):trace.validate(report)
                    else:
                        trace.validate(report)

    def test_unknown_schema_fields_and_changed_baseline_are_rejected(self):
        mutations = (
            ('header-field', lambda r: r['upgrade_accounting']['rows'][0].update(secret=SECRET)),
            ('header-version', lambda r: r['upgrade_accounting']['rows'][0].update(schema_version=1)),
            ('baseline-field', lambda r: rows(r)[2]['launch_baseline'].update(secret=SECRET)),
            ('baseline-version', lambda r: rows(r)[2]['launch_baseline'].update(schema_version=9)),
            ('manager-version', lambda r: rows(r)[2]['launch_baseline'].update(systemd_version=256)),
            ('manager-string', lambda r: rows(r)[2]['launch_baseline'].update(manager_version='256.1')),
            ('executor-version', lambda r: rows(r)[2]['launch_baseline'].update(executor_version='254')),
            ('changed-baseline', lambda r: rows(r)[2]['launch_baseline']['manager'].update(inode=66)),
            ('execution-field', lambda r: rows(r)[2]['processes'][-1]['execution'].update(secret=SECRET)),
            ('exe-field', lambda r: rows(r)[2]['processes'][-1]['execution']['exe'].update(secret=SECRET)),
            ('execution-version', lambda r: rows(r)[2]['processes'][-1]['execution'].update(schema_version=9)),
            ('error-enum', lambda r: rows(r)[2]['processes'][-1]['execution'].update(error=SECRET)),
            ('image-enum', lambda r: rows(r)[2]['processes'][-1]['execution'].update(image=SECRET)),
            ('template-enum', lambda r: rows(r)[2]['processes'][-1]['execution'].update(template=SECRET)),
        )
        for label, mutate in mutations:
            report = self.valid(); mutate(report)
            with self.subTest(case=label), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_runtime_label_cannot_relabel_trusted_manager_or_executor_inode(self):
        for image in ('manager', 'executor'):
            report = self.valid(); sample = rows(report)[-1]
            late = sample['processes'][-1]
            late['execution']['exe'] = baseline()[image]
            sample['state']['observed_subtree'] = [copy.deepcopy(late)]
            sample['state']['services']['v-ui.service'] = service_state(late)
            with self.subTest(image=image), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_nonroot_unknown_mainpid_transition_is_not_a_normal_service(self):
        closed = observed(image='runtime', uid=998, template='panel')
        report = launch_report([closed, closed])
        sample = rows(report)[-1]
        unknown = observed(image='other', uid=998)
        sample['processes'][-1] = copy.deepcopy(unknown)
        sample['state'].update(services={}, observed_subtree=[copy.deepcopy(unknown)],
                              service_transitions={'v-ui.service': service_state(unknown)})
        with self.assertRaises(RuntimeError):trace.validate(report)

    def test_managed_process_cannot_smuggle_unknown_private_fields(self):
        for location in ('processes', 'observed_subtree', 'service-process'):
            report = self.valid(); sample = rows(report)[-1]
            if location == 'processes':target = sample['processes'][-1]
            elif location == 'observed_subtree':target = sample['state']['observed_subtree'][0]
            else:target = sample['state']['services']['v-ui.service']['process']
            target['argv'] = SECRET
            with self.subTest(location=location), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_same_sample_product_to_launcher_or_unknown_regression_is_rejected(self):
        for template, image in (('launcher-panel', 'runtime'), ('other', 'other')):
            report = self.valid()
            rows(report)[-1]['processes'][-1]['execution'].update(template=template, image=image)
            with self.subTest(template=template, image=image), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_pending_errors_and_read_failure_in_partial_state_are_not_forgiven(self):
        for error in ('identity_changed', 'exe_changed', 'read_denied',
                      'process_vanished', 'invalid_cmdline', 'observation_failed'):
            report = self.valid()
            rows(report)[2]['processes'][-1]['execution']['error'] = error
            with self.subTest(error=error), self.assertRaises(RuntimeError):trace.validate(report)
        report = self.valid()
        rows(report)[-1]['state']['observation_error'] = 'service_read_failed'
        with self.assertRaises(RuntimeError):trace.validate(report)

    def test_executable_numeric_schema_rejects_bool_float_missing_and_extra_fields(self):
        mutations = (
            ('bool', lambda value: value.update(inode=True)),
            ('float', lambda value: value.update(device=1.5)),
            ('negative', lambda value: value.update(mtime_ns=-1)),
            ('missing', lambda value: value.pop('size')),
            ('extra-path', lambda value: value.update(path=SECRET)),
        )
        for label, mutate in mutations:
            report = self.valid()
            mutate(rows(report)[2]['processes'][-1]['execution']['exe'])
            with self.subTest(case=label), self.assertRaises(RuntimeError):trace.validate(report)

    def test_new_evidence_still_obeys_existing_row_sample_and_total_byte_budgets(self):
        report = self.valid()
        sample = {'type': 'sample', 'sample': rows(report)[2]}
        self.assertLess(len(json.dumps(sample).encode()), trace.MAX_ROW_BYTES)
        self.assertEqual((trace.MAX_SAMPLES, trace.MAX_BYTES, trace.MAX_ROW_BYTES),
                         (500, 2 * 1024 * 1024, 64 * 1024))
        for cap in ('samples', 'bytes', 'row'):
            with self.subTest(cap=cap), tempfile.TemporaryDirectory() as directory:
                journal = trace.Journal(Path(directory) / 'trace', report['upgrade_accounting']['rows'][0])
                try:
                    journal.append(sample)
                    if cap == 'samples':journal.sample_count = trace.MAX_SAMPLES
                    if cap == 'bytes':journal.byte_count = trace.MAX_BYTES - trace.END_RESERVE_BYTES
                    value = sample if cap != 'row' else {**sample, 'private': SECRET * trace.MAX_ROW_BYTES}
                    with self.assertRaises(RuntimeError):journal.append(value)
                    journal.append({'type': 'finished', 'error': 'RuntimeError'})
                finally:
                    journal.close()
                serialized = (Path(directory) / 'trace').read_text()
                self.assertNotIn(SECRET, serialized)
                self.assertTrue(serialized.endswith('"error":"RuntimeError"}\n'))

    def mainpid_report(self, pending, sequence, *, unit='v-ui.service', starts=None):
        report = launch_report(sequence, unit=unit, starts=starts)
        for entry, sample in zip(sequence, rows(report)[2:]):
            if entry == pending:
                info = service_state(pending)
                if pending['execution']['template'] in ('panel', 'http01'):
                    info['properties']['ActiveState'] = 'activating'
                sample.update(state={'services': {}, 'observed_subtree': [copy.deepcopy(pending)],
                    'service_transitions': {unit: info}}, state_error=None)
        return report

    def test_nonroot_mainpid_transition_closes_without_requiring_an_earlier_root_sample(self):
        for unit, template in (('v-ui.service', 'panel'), ('v-ui-http01.service', 'http01')):
            for image, pattern in (('systemd-manager', 'other'), ('systemd-executor', 'other'),
                                   ('runtime', 'launcher-' + template), ('runtime', template)):
                for count in (1, 2):
                    with self.subTest(unit=unit, image=image, template=pattern, samples=count):
                        pending = observed(unit, image, 998, pattern)
                        closed = observed(unit, 'runtime', 998, template)
                        # An activating complete application is distinguished by its
                        # state, even though its process bytes already equal closure.
                        report = self.mainpid_report(pending, [pending] * count + [closed], unit=unit)
                        last = rows(report)[-1]
                        last['state'] = {'services': {unit: service_state(closed)},
                                         'observed_subtree': [closed], 'service_transitions': {}}
                        trace.validate(report)

    def test_nonroot_mainpid_transition_cannot_disappear_persist_or_end_unclosed(self):
        for unit, template in (('v-ui.service', 'panel'), ('v-ui-http01.service', 'http01')):
            for image, pattern in (('systemd-manager', 'other'), ('systemd-executor', 'other'),
                                   ('runtime', 'launcher-' + template), ('runtime', template)):
                pending = observed(unit, image, 998, pattern)
                for label, sequence in (('disappears', [pending, None]),
                                        ('terminal', [pending]),
                                        ('two-unclosed', [pending, pending]),
                                        ('three-samples', [pending, pending, pending])):
                    with self.subTest(unit=unit, image=image, template=pattern, case=label), \
                            self.assertRaises(RuntimeError):
                        trace.validate(self.mainpid_report(pending, sequence, unit=unit))

    def test_nonroot_mainpid_transition_still_has_a_five_second_deadline(self):
        pending = observed(image='runtime', uid=998, template='launcher-panel')
        closed = observed(image='runtime', uid=998, template='panel')
        with self.assertRaises(RuntimeError):
            trace.validate(self.mainpid_report(pending, [pending, pending, closed],
                                               starts=[5, 7.5, 9.961]))

    def test_service_and_transition_entries_reject_unknown_properties_or_fields(self):
        for collection in ('services', 'service_transitions'):
            for location in ('properties', 'entry'):
                report = self.valid()
                if collection == 'services':
                    entry = rows(report)[-1]['state']['services']['v-ui.service']
                else:
                    sample = rows(report)[2]
                    entry = service_state(sample['processes'][-1])
                    sample.update(state={'services': {}, 'observed_subtree': [],
                        'service_transitions': {'v-ui.service': entry}}, state_error=None)
                target = entry['properties'] if location == 'properties' else entry
                target['argv'] = SECRET
                with self.subTest(collection=collection, location=location), self.assertRaises(RuntimeError):
                    trace.validate(report)

    def test_unclassified_worker_and_observer_reject_extra_private_or_execution_fields(self):
        for index, identity in ((0, 'root-observer'), (1, 'unprivileged-worker')):
            for field, value in (('argv', [SECRET]), ('environment', {'TOKEN': SECRET}),
                                 ('execution', observed()['execution'])):
                report = complete_report()
                process = rows(report)[0]['processes'][index]
                self.assertEqual(process['role'], 'unclassified')
                process[field] = copy.deepcopy(value)
                with self.subTest(identity=identity, field=field), self.assertRaises(RuntimeError):
                    trace.validate(report)

    def test_nonroot_other_image_cannot_regress_to_root_executor_same_generation(self):
        unknown = observed(image='other', uid=998)
        closed = observed(image='runtime', uid=998, template='panel')
        # An ordinary service-owned unknown child is allowed in accounting.
        trace.validate(launch_report([unknown, closed]))
        with self.assertRaises(RuntimeError):
            trace.validate(launch_report([unknown, observed(), closed]))

    def test_old_afce_shape_without_exe_remains_failed_even_with_good_successor(self):
        report = self.valid()
        rows(report)[2]['processes'][-1].pop('execution')
        # This is the exact identity/role shape of afce sample 45, with a
        # synthetic slice and service UID; subsequent identity alone is not exe evidence.
        self.assertEqual(rows(report)[2]['processes'][-1], {
            'pid': 3543, 'ppid': 1, 'starttime_ticks': 16557, 'uid': [0] * 4,
            'cgroup': '/' + PARENT + '/v-ui.service', 'role': 'managed-panel'})
        with self.assertRaises(RuntimeError):trace.validate(report)
        report['upgrade_accounting']['rows'][0].pop('schema_version')
        with self.assertRaises(RuntimeError):trace.validate(report)


class TrustedExecutableBaselineTests(unittest.TestCase):
    def system_stat(self, path, *, changed_path=None, change=None):
        path = Path(path)
        if path in (MANAGER_PATH, EXECUTOR_PATH, Path('/usr/bin/systemctl')):
            value = executable({MANAGER_PATH: 42, EXECUTOR_PATH: 43,
                                Path('/usr/bin/systemctl'): 45}[path])
        else:
            value = executable(100, mode=stat.S_IFDIR | 0o755)
        if path == changed_path:
            value.update(change)
        return stat_result(value)

    def test_trusted_image_accepts_only_safe_real_file_and_ancestor_metadata(self):
        def lstat(path):return self.system_stat(path)
        with patch.object(Path, 'lstat', autospec=True, side_effect=lstat), \
                patch.object(trace, 'executable_identity', return_value=executable(42)):
            self.assertEqual(trace.trusted_system_image(MANAGER_PATH), executable(42))
        unsafe = ({'uid': 998}, {'mode': stat.S_IFREG | 0o775},
                  {'mode': stat.S_IFREG | 0o757}, {'mode': stat.S_IFLNK | 0o777},
                  {'mode': stat.S_IFDIR | 0o755}, {'mode': stat.S_IFIFO | 0o600})
        for change in unsafe:
            with self.subTest(change=change), \
                    patch.object(Path, 'lstat', autospec=True,
                        side_effect=lambda p: self.system_stat(p, changed_path=MANAGER_PATH, change=change)), \
                    patch.object(trace, 'executable_identity', return_value=executable(42)), \
                    self.assertRaises(RuntimeError):
                trace.trusted_system_image(MANAGER_PATH)
        for ancestor in MANAGER_PATH.parents:
            for change in ({'uid': 998}, {'mode': stat.S_IFDIR | 0o775},
                           {'mode': stat.S_IFDIR | 0o757}, {'mode': stat.S_IFLNK | 0o777}):
                with self.subTest(ancestor=ancestor, change=change), \
                        patch.object(Path, 'lstat', autospec=True,
                            side_effect=lambda p: self.system_stat(p, changed_path=ancestor, change=change)), \
                        patch.object(trace, 'executable_identity', return_value=executable(42)), \
                        self.assertRaises(RuntimeError):
                    trace.trusted_system_image(MANAGER_PATH)

    def test_replacement_between_lstat_and_stat_is_rejected(self):
        for mutation in ({'device': 8}, {'inode': 999}, {'uid': 998},
                         {'mode': stat.S_IFREG | 0o775}):
            with self.subTest(mutation=mutation), \
                    patch.object(Path, 'lstat', autospec=True, side_effect=self.system_stat), \
                    patch.object(trace, 'executable_identity', return_value={**executable(42), **mutation}), \
                    self.assertRaises(RuntimeError):
                trace.trusted_system_image(MANAGER_PATH)

    def test_numeric_identity_does_not_serialize_executable_path_or_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / SECRET
            path.write_bytes(b'inert synthetic executable metadata')
            actual = trace.executable_identity(path)
            self.assertEqual(set(actual), {'device', 'inode', 'mode', 'uid', 'size', 'mtime_ns', 'ctime_ns'})
            self.assertTrue(all(type(value) is int for value in actual.values()))
            self.assertNotIn(SECRET, json.dumps(actual))

    def baseline(self, *, version='255.4-1ubuntu8.14', changed=None, executor_output=None):
        calls = []
        seen = {}
        def trusted(path):
            path = Path(path); calls.append(path); seen[path] = seen.get(path, 0) + 1
            item = executable({MANAGER_PATH: 42, EXECUTOR_PATH: 43,
                               Path('/usr/bin/systemctl'): 45}[path])
            if changed == path and seen[path] == 2:
                item['inode'] += 1
            return item
        identities = [executable(42), executable(99) if changed == 'pid1' else executable(42)]
        if executor_output is None:
            executor_output = f'systemd 255 ({version})\n+PAM +AUDIT +SELINUX\n'
        outputs = [SimpleNamespace(stdout=version + '\n'), SimpleNamespace(stdout=executor_output)]
        with patch.object(trace, 'trusted_system_image', side_effect=trusted), \
                patch.object(trace, 'executable_identity', side_effect=identities) as metadata, \
                patch.object(root, 'run', side_effect=outputs) as run:
            result = trace.execution_baseline(proc=Path('/fake-proc'))
            self.assertEqual(run.call_args_list, [
                call(['/usr/bin/systemctl', 'show', '--property=Version', '--value'], timeout=5),
                call(['/usr/lib/systemd/systemd-executor', '--version'], timeout=5)])
            self.assertEqual(metadata.call_count, 2)
            self.assertTrue(all(call.args == (Path('/fake-proc/1/exe'),) for call in metadata.call_args_list))
        self.assertEqual(set(calls), {MANAGER_PATH, EXECUTOR_PATH, Path('/usr/bin/systemctl')})
        self.assertTrue(all(calls.count(path) == 2 for path in set(calls)))
        return result

    def test_running_manager_and_fixed_systemd_images_are_double_read(self):
        actual = self.baseline()
        self.assertEqual(actual, {**baseline(), 'manager_version': '255.4-1ubuntu8.14',
                                     'executor_version': '255.4-1ubuntu8.14'})
        for change in (MANAGER_PATH, EXECUTOR_PATH, Path('/usr/bin/systemctl'), 'pid1'):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.baseline(changed=change)

    def test_unknown_or_unbounded_manager_version_cannot_guess_a_runner_model(self):
        for version in ('', '254', '256.1', 'systemd 255', '255\n' + SECRET,
                        '255-' + 'x' * 129, '255$(id)', '255/../255'):
            with self.subTest(version=version[:30]), self.assertRaises(RuntimeError):
                self.baseline(version=version)

    def test_executor_actual_version_must_match_running_manager_exactly(self):
        for output in ('', 'systemd 256 (256.1)\n+PAM\n',
                       'systemd 255 (255.4-1ubuntu8.13)\n+PAM\n',
                       'systemd 255 (255.4-1ubuntu8.14)\n',
                       'systemd 255 (255.4-1ubuntu8.14)\n+PAM\nextra\n',
                       'systemd 255 (255.4-1ubuntu8.14)\n' + 'x' * 4097):
            with self.subTest(output=output[:80]), self.assertRaises(RuntimeError):
                self.baseline(executor_output=output)

    def test_executor_version_timeout_is_a_hard_failure(self):
        def trusted(path):
            return executable({MANAGER_PATH: 42, EXECUTOR_PATH: 43,
                               Path('/usr/bin/systemctl'): 45}[Path(path)])
        with patch.object(trace, 'trusted_system_image', side_effect=trusted), \
                patch.object(trace, 'executable_identity', return_value=executable(42)), \
                patch.object(root, 'run', side_effect=[SimpleNamespace(stdout='255\n'),
                    subprocess.TimeoutExpired('synthetic-executor-version', 5)]), \
                self.assertRaises(subprocess.TimeoutExpired):
            trace.execution_baseline(proc=Path('/fake-proc'))

    def test_deleted_manager_or_missing_executor_is_not_fallback_identity(self):
        for error in (FileNotFoundError, PermissionError):
            with self.subTest(error=error.__name__), \
                    patch.object(trace, 'trusted_system_image', side_effect=error), \
                    patch.object(root, 'run') as run, self.assertRaises(error):
                trace.execution_baseline(proc=Path('/fake-proc'))
            run.assert_not_called()


class AccountingStateBoundaryTests(unittest.TestCase):
    def state(self, *, early=None, late=None, accounting=True, inactive=False):
        own = {'pid': 500, 'ppid': 20, 'starttime_ticks': 90, 'uid': [0] * 4,
               'cgroup': '/' + PARENT + '/' + PARENT.removesuffix('.slice') + '.service'}
        early = copy.deepcopy(early if early is not None else observed())
        late = copy.deepcopy(late if late is not None else early)
        props = service_state(late)['properties']
        if inactive:props.update(ActiveState='inactive', MainPID='0')
        def properties(unit):
            if unit == 'v-ui.service':return props
            return {'Slice': PARENT, 'ActiveState': 'inactive', 'MainPID': '0',
                    'ControlGroup': '',
                    'DropInPaths': f'/run/systemd/system/{unit}.d/90-vui-resource.conf'}
        def identity(pid, _parent):
            return own if pid == root.os.getpid() else trace.process_identity(late)
        observed_rows = [early, late]
        with patch.object(root, 'identity', side_effect=identity), \
                patch.object(root, 'subtree', return_value=[trace.process_identity(early)]), \
                patch.object(root, 'unit_properties', side_effect=properties), \
                patch.object(root.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=SERVICE_UID)), \
                patch.object(trace, 'observed_process', side_effect=observed_rows), \
                patch.object(Path, 'exists', return_value=False):
            return root.root_state(PARENT, accounting=accounting,
                                   baseline=baseline(), configuration=CONFIGURATION)

    def test_type_simple_active_root_executor_is_retained_only_for_accounting(self):
        state = self.state()
        self.assertNotIn('v-ui.service', state['services'])
        self.assertEqual(state['service_transitions']['v-ui.service']['process']['uid'], [0] * 4)
        self.assertEqual(state['observed_subtree'][0]['uid'], [0] * 4)
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            self.state(accounting=False)

    def test_early_root_evidence_survives_inactive_or_later_nonroot_mainpid(self):
        for early in (observed(), observed(image='other')):
            for inactive in (False, True):
                with self.subTest(image=early['execution']['image'], inactive=inactive):
                    state = self.state(early=early,
                        late=observed(image='runtime', uid=998, template='panel'), inactive=inactive)
                    self.assertEqual(state['observed_subtree'][0], early)
                    self.assertEqual(state['observed_subtree'][0]['uid'], [0] * 4)
                    if inactive:
                        self.assertEqual(state['services'], {})
                    else:
                        self.assertEqual(state['services']['v-ui.service']['process']['uid'], [998] * 4)

    def test_late_service_read_failure_preserves_observed_root_and_fails_closed(self):
        for error in (FileNotFoundError('gone'), PermissionError('denied')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(root, 'accounting_service_identity', side_effect=error):
                state = self.state(early=observed(image='other'))
            self.assertEqual(state['observed_subtree'][0]['uid'], [0] * 4)
            self.assertEqual(state['observed_subtree'][0]['execution']['image'], 'other')
            self.assertEqual(state['observation_error'], 'service_read_failed')

    def test_state_hard_errors_cross_capture_without_broad_catch(self):
        for error in ('wrong effective slice', 'bad drop-in', 'escaped measured parent slice'):
            with self.subTest(error=error), \
                    patch.object(root, 'identity', return_value={'uid': [0] * 4}), \
                    patch.object(trace, 'execution_baseline', return_value=baseline()), \
                    patch.object(trace, 'service_configuration', return_value=CONFIGURATION), \
                    patch.object(root, 'root_state', side_effect=RuntimeError(error)), \
                    self.assertRaisesRegex(RuntimeError, error):
                trace.capture(PARENT)

    def test_late_state_file_race_cannot_discard_earlier_unknown_root(self):
        from scripts import low_resource_acceptance as acceptance
        early = observed(image='other')
        late = observed(image='runtime', uid=998, template='panel')
        own = {'pid': 500, 'ppid': 20, 'starttime_ticks': 90, 'uid': [0] * 4,
               'cgroup': '/' + PARENT + '/' + PARENT.removesuffix('.slice') + '.service'}
        for vanished in (APP_ROOT / 'CURRENT.json', Path('/etc/v-ui/service.json')):
            def service(unit, *_args):
                return (service_state(late), None) if unit == 'v-ui.service' else (None, None)
            def image(path):
                return baseline()['executor' if Path(path) == EXECUTOR_PATH else 'manager']
            with self.subTest(vanished=vanished), ExitStack() as stack:
                for target, name, options in (
                    (root, 'identity', {'return_value': own}),
                    (root, 'subtree', {'side_effect': [[trace.process_identity(early)], [trace.process_identity(late)]]}),
                    (root, 'accounting_service_identity', {'side_effect': service}),
                    (root, 'directory_identity', {'return_value': {'device': 1, 'inode': 2}}),
                    (trace, 'execution_baseline', {'return_value': baseline()}),
                    (trace, 'service_configuration', {'return_value': CONFIGURATION}),
                    (trace, 'observed_process', {'side_effect': [early, late]}),
                    (trace, 'executable_identity', {'side_effect': image}),
                    (trace, 'trusted_system_image', {'side_effect': image}),
                    (acceptance, 'verify_limits', {'return_value': complete_report()['limits']}),
                    (acceptance, 'metrics', {'return_value': complete_report()['final_metrics']}),
                    (Path, 'exists', {'autospec': True, 'side_effect': lambda path: path == vanished}),
                    (Path, 'read_text', {'side_effect': FileNotFoundError('synthetic late state race')})):
                    stack.enter_context(patch.object(target, name, **options))
                try:
                    captured = trace.capture(PARENT)
                except (RuntimeError, FileNotFoundError):
                    continue  # A hard failure is safe; silently losing evidence is not.
                retained = list(captured['processes'])
                if captured['state'] is not None:
                    retained += captured['state']['observed_subtree']
                self.assertIn(early, retained, 'Earlier root identity was discarded')

    def test_real_subtree_partial_process_lookup_failure_is_accounting_hard_error(self):
        from scripts import low_resource_acceptance as acceptance
        original_subtree = root.subtree
        early = process()
        late = observed(image='runtime', uid=998, template='panel')
        own = {'pid': 500, 'ppid': 20, 'starttime_ticks': 90, 'uid': [0] * 4,
               'cgroup': '/' + PARENT + '/' + PARENT.removesuffix('.slice') + '.service'}
        with tempfile.TemporaryDirectory(prefix='vui-fake-cgroup-') as directory:
            group = Path(directory)
            (group / 'cgroup.procs').write_text('3543 3544\n')
            observations = {3543: 0, 3544: 0}
            def identity(pid, parent):
                self.assertEqual(parent, PARENT)
                if pid == root.os.getpid():return own
                observations[pid] += 1
                if pid == 3543:
                    return copy.deepcopy(early if observations[pid] == 1 else trace.process_identity(late))
                if observations[pid] == 1:
                    raise ProcessLookupError('synthetic mid-enumeration race')
                raise FileNotFoundError('synthetic disappeared process')
            def actual_subtree(_directory, parent):
                return original_subtree(group, parent)
            def image(path):
                return baseline()['executor' if Path(path) == EXECUTOR_PATH else 'manager']
            with ExitStack() as stack:
                for target, name, options in (
                    (root, 'identity', {'side_effect': identity}),
                    (root, 'subtree', {'side_effect': actual_subtree}),
                    (root, 'directory_identity', {'return_value': {'device': 1, 'inode': 2}}),
                    (trace, 'execution_baseline', {'return_value': baseline()}),
                    (trace, 'service_configuration', {'return_value': CONFIGURATION}),
                    (trace, 'observed_process', {'return_value': late}),
                    (trace, 'executable_identity', {'side_effect': image}),
                    (trace, 'trusted_system_image', {'side_effect': image}),
                    (acceptance, 'verify_limits', {'return_value': complete_report()['limits']}),
                    (acceptance, 'metrics', {'return_value': complete_report()['final_metrics']}),
                    (Path, 'exists', {'return_value': False})):
                    stack.enter_context(patch.object(target, name, **options))
                with self.assertRaisesRegex(RuntimeError, 'Accounting subtree observation failed'):
                    trace.capture(PARENT)
            self.assertEqual(observations, {3543: 1, 3544: 1})

    def test_nine_stage_gate_does_not_inherit_accounting_root_tolerance(self):
        expected = ('worker_started', 'root_install_and_http01', 'marker_seeded_before_backups',
                    'original_oneclick_controls_passed', 'fresh_directory_upgrade',
                    'stopped_bad_digest_preserves_data', 'stopped_verified_restore',
                    'restored_private_permissions', 'restored_new_login_and_membership')
        self.assertEqual(root.STAGES, expected)
        for stage in (1, 8):
            for unit in ('v-ui.service', 'v-ui-http01.service'):
                report = complete_report()
                info = report['worker']['stages'][stage]['details']['services'][unit]
                info['process']['uid'] = [0] * 4
                info['execution'] = observed(unit)['execution']
                with self.subTest(stage=stage, unit=unit), self.assertRaises(RuntimeError):
                    root.validate_result(report, COMMIT)


if __name__ == '__main__':
    unittest.main()

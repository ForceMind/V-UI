"""Pure fixed-Root projection/receipt tests; no service, process or network."""
from contextlib import nullcontext, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location('_core_read_'+name, ROOT/'deploy'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoreReadStatusTests(unittest.TestCase):
    def setUp(self):
        self.daemon = load('maintenance_daemon')

    def test_closed_read_operation_does_not_create_a_write_action(self):
        self.assertEqual(self.daemon.validate({'protocol': 1, 'operation': 'core_status'}), 'core_status')
        for value in ({'protocol': True, 'operation': 'core_status'},
                      {'protocol': 1, 'operation': 'core_status', 'core': 'xray'},
                      {'protocol': 1, 'operation': 'core_status', 'source': 'a'*40},
                      {'protocol': 1, 'operation': 'submit', 'action': 'core_status'},
                      {'protocol': 1, 'operation': 'submit', 'action': 'update-core'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.daemon.validate(value)
        self.assertEqual(self.daemon.ACTIONS, ('status','start','stop','restart','backup','restore','upgrade'))

    def test_fixed_public_projection_is_detached(self):
        for outcome in ('bundle_default', 'core_pending'):
            self.assertEqual(self.daemon.public_core_selection({'outcome': outcome}), {'outcome': outcome})
        value = {'outcome': 'core_selected', 'cores': {
            'sing-box': {'current': '1.14.3', 'previous': '1.14.2'},
            'xray': {'current': '26.10.21', 'previous': None}}}
        result = self.daemon.public_core_selection(value)
        self.assertEqual(result, value)
        result['cores']['xray']['current'] = '1.2.3'
        self.assertEqual(value['cores']['xray']['current'], '26.10.21')

    def test_unexpected_private_material_and_versions_fail_closed(self):
        valid = {'outcome': 'core_selected', 'cores': {
            'sing-box': {'current': '1.14.3', 'previous': None},
            'xray': {'current': '26.10.21', 'previous': None}}}
        cases = [None, {'outcome':'core_pending','backup_name':'private.zip'},
                 {'outcome':'bundle_default','path':'/private'},
                 {**valid,'backup_sha256':'a'*64}]
        for current in ('/private/core', None, True, '1.2.3\nsecret', '<script>', '1'+'a'*63, '1-private-secret', '01.2.3', '1.2.3.4'):
            changed = deepcopy(valid);changed['cores']['xray']['current'] = current;cases.append(changed)
        changed = deepcopy(valid);changed['cores']['xray']['path']='/private';cases.append(changed)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.daemon.public_core_selection(value)

    def test_core_status_loads_only_receipted_controller_and_returns_read_view(self):
        ops = load('system_ops')
        path = ops.CONTROL/'core_maintenance_control.py'
        code = b"def inspect_core_selection(ops, *, _receipt_sha256):\n    return {'outcome':'bundle_default'}\n"
        receipt = json.dumps({'files':{str(path):hashlib.sha256(code).hexdigest()}}).encode()
        def read(target, *args):
            if target == ops.RECEIPT:return receipt
            if target == path:return code
            raise AssertionError('Unexpected file read')
        with patch.object(ops,'verify_installation',return_value=('systemd',None)), patch.object(ops,'trusted_bytes',side_effect=read):
            self.assertEqual(ops.core_selection_status(ops), {'outcome':'bundle_default'})
        with patch.object(ops,'verify_installation',return_value=('openrc',None)), patch.object(ops,'trusted_bytes',side_effect=read):
            with self.assertRaises(ops.Refused):ops.core_selection_status(ops)
        calls = 0
        def changed(target,*args):
            nonlocal calls
            if target==ops.RECEIPT:
                calls+=1
                return receipt if calls==1 else receipt+b' '
            return read(target,*args)
        with patch.object(ops,'verify_installation',return_value=('systemd',None)), patch.object(ops,'trusted_bytes',side_effect=changed):
            with self.assertRaises(ops.Refused):ops.core_selection_status(ops)


    def test_core_authority_and_reduction_are_closed_operations_without_write_dispatch(self):
        token='vui_cg_'+'b'*32+'_'+'c'*43
        identity={'main_id':7,'main_digest':'a'*64}
        self.assertEqual(self.daemon.validate({'protocol':1,'operation':'core_authority','token':token,**identity}),'core_authority')
        self.assertEqual(self.daemon.validate({'protocol':1,'operation':'core_revoke',**identity}),'core_revoke')
        for extra in ({'scopes':['core_operate']},{'main_id':True},{'token':'bad'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.daemon.validate({'protocol':1,'operation':'core_authority','token':token,**identity,**extra})
        with self.assertRaises(ValueError):self.daemon.validate({'protocol':1,'operation':'core_revoke','token':token,**identity})

    def test_terminal_read_classifies_only_verified_recovery_records(self):
        controller = load('core_maintenance_control')
        class JournalError(Exception):
            pass
        selection = Mock()
        authority = Mock()
        ops = SimpleNamespace(installation_lock=Mock(return_value=nullcontext()),
                              trusted_bytes=Mock(return_value=b'receipt'), RECEIPT=Path('receipt'), MAX_JSON=128,
                              verify_installation=Mock(return_value=('systemd', None)),
                              _load_core_authority=Mock(return_value=SimpleNamespace(CoreAuthority=lambda: authority)))
        files = SimpleNamespace(SelectionFiles=lambda: selection, CoreSelectionFilesError=JournalError)
        selection.read_status.side_effect = lambda: {'outcome':'core_pending'}
        digest = hashlib.sha256(b'receipt').hexdigest()

        with patch.object(controller.os, 'geteuid', return_value=0, create=True), \
                patch.object(controller, '_modules', return_value=nullcontext(SimpleNamespace(selection_files=files))):
            authority.backup_recovery.return_value = {}
            self.assertEqual(controller.inspect_core_selection(ops, _receipt_sha256=digest), {'outcome':'core_pending'})
            self.assertEqual(controller.inspect_core_selection(ops, _receipt_sha256=digest, include_recovery_kind=True),
                             {'outcome':'core_pending','recovery_kind':'backup'})
            authority.backup_recovery.return_value = None
            for operation, error, expected in (
                    ('switch', None, 'switch'), ('restore', 'RECOVERY_OPERATION', 'restore'),
                    ('uncertain', 'INVALID_JOURNAL', None)):
                with self.subTest(operation=operation):
                    selection.read_switch_recovery.side_effect = JournalError(error) if error else None
                    result = controller.inspect_core_selection(ops, _receipt_sha256=digest, include_recovery_kind=True)
                    self.assertEqual(result['outcome'], 'core_pending')
                    self.assertEqual(result.get('recovery_kind'), expected)
            selection.read_switch_recovery.side_effect = JournalError('RECOVERY_OPERATION')
            selection.read_restore_recovery.side_effect = JournalError('INVALID_JOURNAL')
            self.assertEqual(controller.inspect_core_selection(ops, _receipt_sha256=digest, include_recovery_kind=True),
                             {'outcome':'core_pending'})
        self.assertTrue(all(call.kwargs == {'_readonly': True} for call in ops.installation_lock.call_args_list))

    def test_terminal_recovery_hint_does_not_display_unknown_private_values(self):
        ops = load('system_ops')
        for kind, number in [('restore',15),('switch',18),('backup',18)]:
            output=io.StringIO()
            with redirect_stdout(output):
                ops.show_core_selection({'outcome':'core_pending','recovery_kind':kind})
            self.assertIn(f'用 {number} 恢复',output.getvalue())
        output=io.StringIO()
        with redirect_stdout(output):
            ops.show_core_selection({'outcome':'core_pending'})
            with self.assertRaises(ops.Refused):
                ops.show_core_selection({'outcome':'core_pending','recovery_kind':'private-value'})
        self.assertNotIn('private-value',output.getvalue())
        self.assertNotIn('用 15',output.getvalue())
        self.assertNotIn('用 18',output.getvalue())

    def test_terminal_busy_reports_original_task_and_does_not_retry(self):
        ops = load('system_ops')
        output=io.StringIO()
        output.isatty=lambda:True
        tty=SimpleNamespace(isatty=lambda:True)
        flags=SimpleNamespace(dont_write_bytecode=True,isolated=True,no_site=True)
        with (patch.object(ops.os,'geteuid',return_value=0,create=True),
              patch.object(ops.os,'getuid',return_value=0,create=True),
              patch.object(ops.os,'umask'),
              patch.object(ops.sys,'stdin',tty), patch.object(ops.sys,'flags',flags),
              patch.object(ops.sys,'argv',['v-ui']), patch.object(ops,'PYTHON',Path(ops.sys.executable)),
              patch.object(ops,'read_input',side_effect=['4','yes','0']),
              patch.object(ops,'deferred_signals',return_value=nullcontext([])),
              patch.object(ops,'installation_lock',side_effect=ops.Refused('another installer or maintenance operation is running')) as lock,
              patch.object(ops,'operate') as operate, redirect_stdout(output)):
            self.assertEqual(ops.menu(),0)
        lock.assert_called_once_with()
        operate.assert_not_called()
        self.assertIn('请等待原任务完成',output.getvalue())

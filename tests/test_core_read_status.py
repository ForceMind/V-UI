"""Pure fixed-Root projection/receipt tests; no service, process or network."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

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

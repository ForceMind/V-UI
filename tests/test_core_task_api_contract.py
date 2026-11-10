"""Closed core intent and wire contracts; stdlib only, no API/native service.

The production validator body is isolated without importing Pydantic/ORM.
Actual Pydantic and authenticated ASGI coverage lives in test_maintenance_auth.
"""
import ast
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'a' * 64


class CoreIntentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse((ROOT / 'app/api/maintenance.py').read_text())
        model = next(row for row in source.body if isinstance(row, ast.ClassDef) and row.name == 'CoreTaskPayload')
        method = next(row for row in model.body if isinstance(row, ast.FunctionDef) and row.name == 'closed_core_intent')
        method.decorator_list = []
        namespace = {'re': __import__('re')}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), '<production core intent>', 'exec'), namespace)
        cls.validate = staticmethod(namespace['closed_core_intent'])

    def check(self, action, parameters):
        return self.validate(SimpleNamespace(action=action, parameters=parameters))

    def test_all_five_closed_actions_and_nullable_initial_revision(self):
        rows = [('update', dict(core='sing-box', release_id=123, selection_revision=None)),
                ('rollback', dict(core='xray', selection_revision=REVISION)),
                ('backup', dict(selection_revision=REVISION)),
                ('restore', dict(backup_task_id='b' * 32, selection_revision=REVISION)),
                ('recover', dict(recovery_kind='restore', pending_transaction='c' * 32,
                                 pending_sequence=0, selection_revision=None)),
                ('recover', dict(recovery_kind='backup', pending_transaction='c' * 32,
                                 pending_sequence=1, selection_revision=REVISION))]
        for action, parameters in rows:
            with self.subTest(action=action):
                before = deepcopy(parameters)
                self.assertEqual(self.check(action, parameters).parameters, before)
                self.assertEqual(parameters, before)

    def test_no_paths_urls_sha_flags_or_unknown_actions(self):
        base = dict(core='sing-box', release_id=123, selection_revision=REVISION)
        for key in ('path', 'url', 'sha256', 'approved', 'app_source', 'command'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check('update', {**base, key: 'private'})
        for action in ('upgrade', 'apply', 'stage', 'observe'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.check(action, base)

    def test_release_and_sequence_are_exact_bounded_integers(self):
        for value in (True, False, 0, -1, 2**63, 1.0, '123', None):
            with self.subTest(release=value), self.assertRaises(ValueError):
                self.check('update', dict(core='xray', release_id=value, selection_revision=REVISION))
        for value in (True, -1, 2**63, '1', 1.0):
            with self.subTest(sequence=value), self.assertRaises(ValueError):
                self.check('recover', dict(recovery_kind='switch', pending_transaction='c'*32,
                                           pending_sequence=value, selection_revision=None))

    def test_revisions_backup_and_recovery_are_exact(self):
        for revision in (None, True, 'A'*64, 'a'*63):
            for action in ('rollback', 'backup', 'restore'):
                parameters = dict(selection_revision=revision)
                if action == 'rollback': parameters['core'] = 'xray'
                if action == 'restore': parameters['backup_task_id'] = 'b'*32
                with self.subTest(action=action, revision=revision), self.assertRaises(ValueError):
                    self.check(action, parameters)
        for transaction in ('ordinary-backup.zip', '../private', 'A'*32):
            with self.subTest(transaction=transaction), self.assertRaises(ValueError):
                self.check('recover', dict(recovery_kind='switch', pending_transaction=transaction,
                                           pending_sequence=0, selection_revision=None))
        with self.assertRaises(ValueError):
            self.check('recover', dict(recovery_kind='data', pending_transaction='b'*32,
                                       pending_sequence=0, selection_revision=REVISION))

    def test_unknown_core_and_missing_fields_are_rejected(self):
        for core in ('mihomo', '', None, ['xray']):
            with self.subTest(core=core), self.assertRaises(ValueError):
                self.check('update', dict(core=core, release_id=1, selection_revision=None))
        with self.assertRaises(ValueError):
            self.check('recover', dict(recovery_kind='switch', selection_revision=REVISION))


class CoreTaskBridgeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('_core_bridge_fixture', ROOT/'app/services/maintenance_tasks.py')
        self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)
        self.binding = dict(main_id=7, main_digest='b'*64)
        self.token = 'vui_cg_'+'c'*32+'_'+'d'*43

    def test_read_operations_have_no_root_token_or_caller_context(self):
        with patch.object(self.module._client, 'request', return_value={}) as request:
            self.module.core_observe(); self.module.core_list(); self.module.core_get('a'*32)
        self.assertEqual([row.args[0] for row in request.call_args_list], [
            {'protocol':1,'operation':'core_observe'}, {'protocol':1,'operation':'core_list'},
            {'protocol':1,'operation':'core_get','task_id':'a'*32}])

    def test_submit_keeps_original_binding_nonce_and_closed_intent(self):
        parameters = dict(core='xray', release_id=123, selection_revision=REVISION)
        with patch.object(self.module._client, 'request', return_value={'id':'e'*32}) as request:
            self.module.core_submit(self.token, self.binding, 'f'*32, 'update', parameters)
        request.assert_called_once_with(dict(protocol=1, operation='core_submit', token=self.token,
            **self.binding, request_id='f'*32, action='update', parameters=parameters))

    def test_unknown_submission_lookup_is_only_a_read_operation(self):
        with patch.object(self.module._client, 'request', return_value={'not_found':True}) as request:
            result = self.module.core_lookup(self.token, self.binding, 'f'*32)
        self.assertEqual(result, {'not_found':True})
        request.assert_called_once_with(dict(protocol=1, operation='core_lookup', token=self.token,
            **self.binding, request_id='f'*32))

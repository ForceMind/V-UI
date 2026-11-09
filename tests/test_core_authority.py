"""Owner-controlled temp files and inert lock peers; no host/Root operations."""
import fcntl
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('_core_authority_test', Path(__file__).resolve().parents[1]/'deploy/core_authority.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


def hold_lock(path, ready, release):
    with open(path, 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        ready.set()
        release.wait(5)


class CoreAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-authority-')
        self.addCleanup(temporary.cleanup)
        self.anchor = Path(temporary.name)
        self.root = self.anchor/'authority'
        self.store = self.reload()
        self.clock = patch.object(a.time, 'time', return_value=1000000)
        self.now = self.clock.start();self.addCleanup(self.clock.stop)
        self.main_digest = 'a'*64
        self.scope = ['core_operate', 'core_backup', 'core_restore']
        self.parameters = dict(core='sing-box', release_id=11, selection_revision=None)

    def reload(self):
        return a.CoreAuthority(_root=self.root, _anchor=self.anchor, _owner=os.geteuid())

    def issue(self, scopes=None, digest=None, seconds=1800):
        return self.store.issue(main_id=7, main_digest=digest or self.main_digest,
                                scopes=self.scope if scopes is None else scopes, seconds=seconds)

    def reserve(self, token, request='1'*32, action='update', parameters=None, digest=None, store=None):
        return (store or self.store).reserve(token=token, main_id=7,
            main_digest=digest or self.main_digest, request_id=request, action=action,
            parameters=self.parameters if parameters is None else parameters)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(a.CoreAuthorityError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_issue_delivers_once_and_persists_only_digest_with_private_modes(self):
        record, token = self.issue()
        raw = (self.root/'authority.json').read_bytes()
        self.assertNotIn(token.encode(), raw)
        self.assertNotIn('secret_sha256', record)
        self.assertNotIn('main_digest', record)
        self.assertEqual(record['scopes'], sorted(self.scope))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for name in ('.lock', 'authority.json'):
            self.assertEqual(stat.S_IMODE((self.root/name).stat().st_mode), 0o600)
        self.assertEqual(self.reload().grants(), [record])

    def test_request_is_durable_idempotent_but_never_changes_parameters(self):
        _, token = self.issue()
        first, created = self.reserve(token)
        self.assertTrue(created)
        raw = (self.root/'authority.json').read_bytes()
        replay, created = self.reserve(token, store=self.reload())
        self.assertFalse(created);self.assertEqual(first, replay)
        self.assertEqual((self.root/'authority.json').read_bytes(), raw)
        changed = {**self.parameters, 'release_id':12}
        self.assert_code('CONFLICT', self.reserve, token, parameters=changed)
        self.assertEqual((self.root/'authority.json').read_bytes(), raw)

    def test_bearer_id_scope_and_claimed_normal_identity_do_not_grant_access(self):
        record, token = self.issue()
        for wrong in ('vui_mg_'+'x'*43, token[:-1]+('A' if token[-1]!='A' else 'B')):
            self.assert_code('AUTHORITY', self.reserve, wrong)
        self.assert_code('AUTHORITY', self.reserve, token, digest='b'*64)
        self.assert_code('AUTHORITY', self.store.reserve, token=token, main_id=8,
                         main_digest=self.main_digest, request_id='2'*32, action='update', parameters=self.parameters)
        self.assertNotIn(token, str(record))

    def test_backup_restore_and_restore_recovery_have_separate_capabilities(self):
        _, token = self.issue(['core_operate'])
        self.assert_code('SCOPE', self.reserve, token, action='backup', parameters={'selection_revision':'c'*64})
        self.assert_code('SCOPE', self.reserve, token, action='restore',
                         parameters={'selection_revision':'c'*64,'backup_task_id':'d'*32})
        recovery = {'selection_revision':None,'pending_transaction':'d'*32,'pending_sequence':0,'recovery_kind':'restore'}
        self.assert_code('SCOPE', self.reserve, token, action='recover', parameters=recovery)
        accepted, created = self.reserve(token, action='recover', parameters={**recovery,'recovery_kind':'switch'})
        self.assertTrue(created);self.assertEqual(accepted['action'], 'recover')

    def test_revocation_durable_exact_and_idempotent(self):
        _, token = self.issue()
        self.store.revoke_main(main_id=7, main_digest='b'*64)
        self.reserve(token)
        self.store.revoke_main(main_id=7, main_digest=self.main_digest)
        raw = (self.root/'authority.json').read_bytes()
        self.store.revoke_main(main_id=7, main_digest=self.main_digest)
        self.assertEqual((self.root/'authority.json').read_bytes(), raw)
        self.assert_code('AUTHORITY', self.reserve, token, store=self.reload())

    def test_expiration_is_persisted_and_wall_clock_rollback_cannot_revive_it(self):
        _, token = self.issue(seconds=60)
        self.now.return_value = 1000060
        self.assert_code('AUTHORITY', self.reserve, token)
        self.now.return_value = 1000010
        self.assert_code('AUTHORITY', self.reserve, token, store=self.reload())
        self.assertTrue(self.store.grants()[0]['revoked'])

    def test_epoch_change_invalidates_queued_work_and_same_numeric_id_reuse(self):
        _, token = self.issue()
        task, _ = self.reserve(token)
        self.store.advance_epoch()
        self.assert_code('AUTHORITY', self.store.enter_mutation, task_id=task['id'], plan_sha256='e'*64)
        self.assert_code('AUTHORITY', self.reserve, token)
        self.assert_code('AUTHORITY', self.reserve, token, digest='b'*64)
        _, newer = self.issue(digest='b'*64)
        self.assertTrue(self.reserve(newer, digest='b'*64)[1])
        self.assertTrue(next(row for row in self.store.grants() if row['id']==task['grant_id'])['revoked'])

    def test_running_preflight_is_not_permission_and_mutation_is_single_consumption(self):
        _, token = self.issue()
        task, _ = self.reserve(token)
        self.store.revoke_main(main_id=7, main_digest=self.main_digest)
        self.assert_code('AUTHORITY', self.store.enter_mutation, task_id=task['id'], plan_sha256='e'*64)
        self.main_digest = 'b'*64  # A fresh ordinary grant, not a revoked binding.
        _, token = self.issue()
        task, _ = self.reserve(token)
        consumed = self.store.enter_mutation(task_id=task['id'], plan_sha256='e'*64)
        self.assertEqual(consumed['phase'], 'mutating')
        self.assertEqual(self.reserve(token)[0], consumed)
        self.assert_code('CONSUMED', self.reload().enter_mutation, task_id=task['id'], plan_sha256='e'*64)
        self.store.revoke_main(main_id=7, main_digest=self.main_digest)
        value = json.loads((self.root/'authority.json').read_bytes())
        self.assertEqual(value['admissions'][task['id']]['phase'], 'mutating')

    def test_expiry_during_preflight_prevents_entering_mutation(self):
        _, token = self.issue(seconds=60)
        task, _ = self.reserve(token)
        self.now.return_value = 1000060
        self.assert_code('AUTHORITY', self.store.enter_mutation, task_id=task['id'], plan_sha256='e'*64)
        self.assertEqual(json.loads((self.root/'authority.json').read_bytes())['admissions'][task['id']]['phase'], 'preflight')

    def test_unknown_parameters_boolean_ids_and_null_settled_revision_are_rejected(self):
        _, token = self.issue()
        for change in ({'url':'https://elsewhere/private'}, {'release_id':True}, {'selection_revision':False}):
            with self.subTest(change=change), self.assertRaises(a.CoreAuthorityError):
                self.reserve(token, parameters={**self.parameters, **change})
        self.assert_code('REVISION', self.reserve, token, action='backup', parameters={'selection_revision':None})
        self.assert_code('REQUEST', self.reserve, token, request='not-a-request')
        self.assert_code('ACTION', self.reserve, token, action='shell', parameters={})

    def test_private_unsafe_files_links_and_lock_modes_fail_closed(self):
        _, token = self.issue()
        state = self.root/'authority.json';raw = state.read_bytes()
        state.chmod(0o644)
        self.assert_code('FILE', self.reserve, token)
        state.chmod(0o600)
        os.link(state, self.root/'extra')
        self.assert_code('FILE', self.reserve, token)
        (self.root/'extra').unlink()
        saved = self.root/'saved';state.rename(saved);state.symlink_to(saved)
        self.assert_code('IO', self.reserve, token)
        state.unlink();saved.rename(state)
        (self.root/'.lock').chmod(0o644)
        self.assert_code('FILE', self.reserve, token)
        self.assertEqual(state.read_bytes(), raw)

    def test_corrupt_duplicate_or_noncanonical_json_never_bootstraps(self):
        _, token = self.issue()
        state = self.root/'authority.json';original=state.read_bytes()
        for raw in (b'{"schema":1,"schema":1}', original+b'\n', b'{}'):
            state.write_bytes(raw)
            with self.subTest(raw=raw[:30]),self.assertRaises(a.CoreAuthorityError):self.reserve(token)
            self.assertEqual(state.read_bytes(), raw)

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(), 'POSIX lock peer')
    def test_other_process_lock_blocks_without_any_record_change(self):
        _, token = self.issue();raw = (self.root/'authority.json').read_bytes()
        context = multiprocessing.get_context('fork')
        ready, release = context.Event(), context.Event()
        peer = context.Process(target=hold_lock,args=(self.root/'.lock',ready,release))
        peer.start()
        try:
            self.assertTrue(ready.wait(5))
            self.assert_code('BUSY', self.reserve, token)
            self.assertEqual((self.root/'authority.json').read_bytes(), raw)
        finally:
            release.set();peer.join(5)
            if peer.is_alive():peer.kill();peer.join(5)
        self.assertEqual(peer.exitcode, 0)

    def test_parent_replacement_before_publish_is_detected_without_overwriting(self):
        _, token = self.issue();raw = (self.root/'authority.json').read_bytes()
        original = a.os.fsync;renamed = self.anchor/'displaced';changed=False
        def swap(fd):
            nonlocal changed
            original(fd)
            if not changed and stat.S_ISREG(os.fstat(fd).st_mode):
                changed=True;self.root.rename(renamed);self.root.mkdir(mode=0o700)
        with patch.object(a.os,'fsync',side_effect=swap):
            self.assert_code('DIRECTORY_CHANGED', self.reserve, token)
        self.assertEqual((renamed/'authority.json').read_bytes(), raw)
        self.assertFalse((self.root/'authority.json').exists())

    def test_admission_capacity_does_not_remove_history_or_break_replay(self):
        _, token = self.issue()
        with patch.object(a,'MAX_ADMISSIONS',1):
            first, _ = self.reserve(token)
            self.assert_code('CAPACITY', self.reserve, token, request='2'*32)
            self.assertEqual(self.reserve(token)[0], first)

    def test_restrictive_umask_only_initializes_new_inodes_and_never_repairs_unsafe_existing_ones(self):
        old = os.umask(0o777)
        try:
            self.issue()
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        (self.root/'.lock').chmod(0o000)
        with self.assertRaises((a.CoreAuthorityError, PermissionError)):
            self.issue()

    def test_decimal_conversion_is_bounded_before_int_on_all_python_versions(self):
        self.assert_code('JSON_INTEGER', a._decode, b'{"schema":'+b'9'*100000+b'}')
        self.assert_code('JSON_INTEGER', a._decode, b'{"schema":9223372036854775808}')
        self.assertEqual(a._json_integer('9223372036854775807'), a.MAX_INT)

    def test_parent_namespace_sync_failure_never_returns_authority_and_retry_syncs_again(self):
        original = a.os.fsync
        anchor = self.anchor.stat()
        def fail_parent(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino)==(anchor.st_dev,anchor.st_ino):
                raise OSError('inert parent sync failure')
            original(fd)
        with patch.object(a.os,'fsync',side_effect=fail_parent):
            self.assert_code('IO', self.issue)
        self.assertTrue(self.root.is_dir())
        self.assertFalse((self.root/'authority.json').exists())
        calls = []
        def record_parent(fd):
            info = os.fstat(fd)
            calls.append((info.st_dev,info.st_ino))
            original(fd)
        with patch.object(a.os,'fsync',side_effect=record_parent):
            record, token = self.issue()
        self.assertIn((anchor.st_dev,anchor.st_ino), calls)
        self.assertTrue(token);self.assertTrue(record['id'])

    def test_normal_revoke_tombstone_blocks_late_issue_even_before_any_root_grant(self):
        # The ordinary DB observation may have happened before Root revocation.
        observed = dict(main_id=7, main_digest=self.main_digest, scopes=self.scope)
        self.assertEqual(self.store.revoke_main(main_id=7, main_digest=self.main_digest), {'revoked':True})
        raw = (self.root/'authority.json').read_bytes()
        self.assert_code('REVOKED_BINDING', self.reload().issue, **observed)
        self.store.revoke_main(main_id=7, main_digest=self.main_digest)
        self.assertEqual((self.root/'authority.json').read_bytes(), raw)
        self.assertFalse(self.store.grants())
        self.assertTrue(self.issue(digest='b'*64)[1])

    def test_normal_revoke_tombstone_blocks_reissue_after_existing_root_grant(self):
        _, token = self.issue()
        self.store.revoke_main(main_id=7, main_digest=self.main_digest)
        self.assert_code('REVOKED_BINDING', self.issue)
        self.assert_code('AUTHORITY', self.reserve, token)

    def test_full_binding_capacity_preserves_revocation_of_every_issued_binding(self):
        with patch.object(a,'MAX_BINDINGS',1):
            _, token = self.issue()
            self.assert_code('CAPACITY', self.store.revoke_main, main_id=9, main_digest='b'*64)
            self.assert_code('CAPACITY', self.issue, digest='b'*64)
            self.store.revoke_main(main_id=7, main_digest=self.main_digest)
            self.assert_code('AUTHORITY', self.reserve, token)
            self.assert_code('REVOKED_BINDING', self.issue)

    def test_idempotent_revocation_reconfirms_durability_after_unknown_directory_sync(self):
        self.issue()
        original = a.os.fsync;root = self.root.stat()
        def fail_directory(fd):
            info = os.fstat(fd)
            if (info.st_dev,info.st_ino)==(root.st_dev,root.st_ino):
                raise OSError('inert directory sync failure')
            original(fd)
        with patch.object(a.os,'fsync',side_effect=fail_directory):
            self.assert_code('IO', self.store.revoke_main, main_id=7, main_digest=self.main_digest)
            self.assertTrue(json.loads((self.root/'authority.json').read_bytes())['revoked_bindings'])
            self.assert_code('IO', self.store.revoke_main, main_id=7, main_digest=self.main_digest)
        self.assertEqual(self.store.revoke_main(main_id=7, main_digest=self.main_digest), {'revoked':True})

    def test_idempotent_reservation_reconfirms_durability_after_unknown_directory_sync(self):
        _, token = self.issue()
        original = a.os.fsync;root = self.root.stat()
        def fail_directory(fd):
            info = os.fstat(fd)
            if (info.st_dev,info.st_ino)==(root.st_dev,root.st_ino):
                raise OSError('inert directory sync failure')
            original(fd)
        with patch.object(a.os,'fsync',side_effect=fail_directory):
            self.assert_code('IO', self.reserve, token)
            state = json.loads((self.root/'authority.json').read_bytes())
            identity = next(iter(state['admissions']))
            self.assert_code('IO', self.reserve, token)
        replay, created = self.reserve(token)
        self.assertFalse(created);self.assertEqual(replay['id'], identity)

    def test_observed_expiry_is_not_claimed_durable_after_unknown_sync_until_reconfirmed(self):
        _, token = self.issue(seconds=60)
        self.now.return_value = 1000060
        original = a.os.fsync;root = self.root.stat()
        def fail_directory(fd):
            info = os.fstat(fd)
            if (info.st_dev,info.st_ino)==(root.st_dev,root.st_ino):
                raise OSError('inert directory sync failure')
            original(fd)
        with patch.object(a.os,'fsync',side_effect=fail_directory):
            self.assert_code('IO', self.reserve, token)
            self.assert_code('IO', self.reserve, token)
        self.assert_code('AUTHORITY', self.reserve, token)
        self.now.return_value = 1000010
        self.assert_code('AUTHORITY', self.reserve, token)


    def app_record(self):
        return dict(release_id='0.4.23-'+'d'*12,target='x86_64-gnu',app_source='d'*40,
                    manifest_sha256='e'*64,archive_sha256='f'*64)

    def test_application_origin_bootstrap_is_separate_from_credentials_and_returns_detached_record(self):
        record=self.app_record()
        self.assertEqual(self.store.record_application(record),record)
        state=json.loads((self.root/'authority.json').read_bytes())
        self.assertEqual(state['schema'],2);self.assertFalse(state['grants']);self.assertFalse(state['admissions'])
        observed=self.store.application(release_id=record['release_id'],target=record['target'])
        observed['app_source']='a'*40
        self.assertEqual(self.store.application(release_id=record['release_id'],target=record['target']),record)
        self.assertIsNone(self.store.application(release_id='other-version',target=record['target']))
        self.assertIsNone(self.store.application(release_id=record['release_id'],target='x86_64-musl'))

    def test_application_identity_is_immutable_but_multiple_versions_remain_available(self):
        record=self.app_record();self.store.record_application(record)
        raw=(self.root/'authority.json').read_bytes()
        self.assertEqual(self.store.record_application(record),record)
        self.assertEqual((self.root/'authority.json').read_bytes(),raw)
        for key in ('app_source','manifest_sha256','archive_sha256'):
            altered={**record,key:'a'*(40 if key=='app_source' else 64)}
            self.assert_code('APPLICATION_CONFLICT',self.store.record_application,altered)
            self.assertEqual((self.root/'authority.json').read_bytes(),raw)
        other={**record,'release_id':'0.4.24-'+'a'*12,'app_source':'a'*40}
        self.store.record_application(other)
        for row in (record,other):
            self.assertEqual(self.store.application(release_id=row['release_id'],target=row['target']),row)

    def test_schema1_migration_preserves_grants_epoch_tombstones_and_consumption_without_backfilling_origins(self):
        _,token=self.issue();task,_=self.reserve(token)
        self.store.enter_mutation(task_id=task['id'],plan_sha256='e'*64)
        self.store.revoke_main(main_id=7,main_digest=self.main_digest)
        path=self.root/'authority.json'
        legacy=json.loads(path.read_bytes());legacy.pop('applications');legacy.update(schema=1,kind='core-authority-v1')
        raw=a._raw(legacy);path.write_bytes(raw)
        self.assertIsNone(self.reload().application(release_id=self.app_record()['release_id'],target='x86_64-gnu'))
        self.assertEqual(path.read_bytes(),raw)
        self.reload().record_application(self.app_record())
        modern=json.loads(path.read_bytes())
        self.assertEqual(modern['schema'],2)
        for key in ('installation_id','epoch','grants','admissions','revoked_bindings'):
            self.assertEqual(modern[key],legacy[key])
        self.assert_code('AUTHORITY',self.reserve,token)
        self.assert_code('CONSUMED',self.store.enter_mutation,task_id=task['id'],plan_sha256='e'*64)

    def test_only_exact_schema1_is_migrated_and_schema2_missing_origins_is_corrupt(self):
        self.issue();path=self.root/'authority.json';current=json.loads(path.read_bytes())
        malformed=[{**current,'schema':1,'kind':'core-authority-v1'},
                   {key:value for key,value in current.items() if key!='applications'},
                   {**current,'schema':3}, {**current,'schema':True}]
        for value in malformed:
            path.write_bytes(a._raw(value))
            with self.subTest(schema=value.get('schema')):
                self.assert_code('SCHEMA',self.store.application,release_id='test',target='x86_64-gnu')
            self.assertEqual(path.read_bytes(),a._raw(value))

    def test_origin_input_is_closed_and_never_uses_paths_urls_or_implicit_target(self):
        self.issue();path=self.root/'authority.json';raw=path.read_bytes()
        record=self.app_record()
        for key,value in (('release_id','../outside'),('target',[]),('app_source',True),
                          ('manifest_sha256','F'*64),('archive_sha256',None),('url','https://untrusted')):
            with self.subTest(key=key),self.assertRaises(a.CoreAuthorityError):
                self.store.record_application({**record,key:value})
            self.assertEqual(path.read_bytes(),raw)

    def test_epoch_reduction_never_rolls_back_accepted_application_origins(self):
        self.store.record_application(self.app_record())
        _,token=self.issue()
        before=json.loads((self.root/'authority.json').read_bytes())
        self.store.advance_epoch()
        after=json.loads((self.root/'authority.json').read_bytes())
        self.assertEqual(after['applications'],before['applications'])
        self.assertEqual(after['installation_id'],before['installation_id'])
        self.assertNotEqual(after['epoch'],before['epoch'])
        self.assert_code('AUTHORITY',self.reserve,token)
        self.assertEqual(self.store.application(release_id=self.app_record()['release_id'],target='x86_64-gnu'),self.app_record())

    def test_origin_replay_and_reader_reconfirm_unknown_durable_publication(self):
        self.issue();original=a.os.fsync;root=self.root.stat()
        def fail_directory(fd):
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==(root.st_dev,root.st_ino):raise OSError('inert sync failure')
            original(fd)
        record=self.app_record()
        with patch.object(a.os,'fsync',side_effect=fail_directory):
            self.assert_code('IO',self.store.record_application,record)
            self.assert_code('IO',self.store.record_application,record)
            self.assert_code('IO',self.store.application,release_id=record['release_id'],target=record['target'])
        self.assertEqual(self.store.application(release_id=record['release_id'],target=record['target']),record)

    def test_origin_capacity_preserves_old_versions_and_existing_identity_replay(self):
        with patch.object(a,'MAX_APPLICATIONS',1):
            record=self.app_record();self.store.record_application(record)
            other={**record,'release_id':'another-accepted-package'}
            self.assert_code('CAPACITY',self.store.record_application,other)
            self.assertEqual(self.store.record_application(record),record)

    def test_sdk_bootstrap_creates_only_last_two_owned_private_directories(self):
        root=self.anchor/'maintenance'/'authority'
        store=a.CoreAuthority(_root=root,_anchor=self.anchor,_owner=os.geteuid())
        store.record_application(self.app_record())
        self.assertEqual(stat.S_IMODE(root.parent.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o700)
        deeper=a.CoreAuthority(_root=self.anchor/'missing'/'too'/'deep',_anchor=self.anchor,_owner=os.geteuid())
        self.assert_code('IO',deeper.record_application,self.app_record())
        self.assertFalse((self.anchor/'missing').exists())

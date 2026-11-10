"""CORE pending admission: real temp flocks, fake Root metadata and pure ASGI.

No system service, Root control action, live restore, native core or network.
"""
import ast
import asyncio
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch,PropertyMock
import zipfile

from app import data_owner as owner,release_tools as tools
from app.core_updates import managed_startup as startup,backup_archive as archive
import test_core_backup_archive as backup_fixtures

ROOT=Path(__file__).resolve().parents[1]


class PendingAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vui-admission-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        self.data=self.root/'data';self.data.mkdir(mode=0o700)
        self.pending=self.root/'CORE_UPDATE_PENDING.json'
        self.patch=patch.object(owner,'_CORE_UPDATE_PENDING',self.pending);self.patch.start();self.addCleanup(self.patch.stop)
    def set_pending(self):self.pending.write_bytes(b'not authorization');self.pending.chmod(0o600)
    def test_admitted_shared_scope_can_finish_but_new_shared_scope_is_refused(self):
        with owner.data_owner(self.data):
            self.set_pending()
            with self.assertRaises(owner.DataOwnerError):
                with owner.data_owner(self.data):pass
        self.assertEqual(self.pending.read_bytes(),b'not authorization')
        self.pending.unlink()
        with owner.data_owner(self.data):pass
    def test_barrier_is_rechecked_after_actual_shared_flock_acquisition(self):
        flock=owner.fcntl.flock;seen=[]
        def admitted(fd,flags):
            result=flock(fd,flags);seen.append(flags)
            if flags&owner.fcntl.LOCK_SH:self.set_pending()
            return result
        with patch.object(owner.fcntl,'flock',side_effect=admitted):
            with self.assertRaises(owner.DataOwnerError):
                with owner.data_owner(self.data):pass
        self.assertTrue(seen);self.assertTrue(self.pending.exists())
    def test_real_exclusive_borrow_works_but_other_thread_and_faked_native_do_not(self):
        self.set_pending();errors=[]
        with owner.data_owner(self.data,exclusive=True):
            with owner.data_owner(self.data):pass
            def foreign():
                try:
                    with owner.data_owner(self.data):pass
                except BaseException as error:errors.append(error)
            thread=threading.Thread(target=foreign);thread.start();thread.join()
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],owner.DataOwnerError)
        fake=object.__new__(startup.ManagedStartup);fake.connection=types.SimpleNamespace(released=True,native=True)
        old=getattr(startup._LOCAL,'session',None);startup._LOCAL.session=fake
        try:
            with self.assertRaises(owner.DataOwnerError):
                with owner.data_owner(self.data):pass
        finally:startup._LOCAL.session=old
    def test_same_thread_borrow_is_not_available_after_pid_change(self):
        self.set_pending()
        with owner.data_owner(self.data,exclusive=True):
            with patch.object(owner.os,'getpid',return_value=os.getpid()+1):
                with self.assertRaises(owner.DataOwnerError):
                    with owner.data_owner(self.data):pass
    def test_invalid_json_directory_dangling_link_and_check_errors_are_all_barriers(self):
        self.set_pending()
        for mode in ('invalid','directory','link','lookup-error'):
            with self.subTest(mode=mode):
                if self.pending.is_symlink() or self.pending.is_file():self.pending.unlink()
                elif self.pending.exists():self.pending.rmdir()
                if mode=='invalid':self.pending.write_bytes(b'{invalid JSON')
                elif mode=='directory':self.pending.mkdir()
                elif mode=='link':self.pending.symlink_to(self.root/'missing-target')
                if mode=='lookup-error':
                    stat_fn=owner.os.stat
                    def failed(path,*args,**kwargs):
                        if path==self.pending:raise PermissionError('private admission lookup')
                        return stat_fn(path,*args,**kwargs)
                    with patch.object(owner.os,'stat',side_effect=failed):
                        self.assertTrue(owner.core_update_pending())
                        with self.assertRaises(owner.DataOwnerError):
                            with owner.data_owner(self.data):pass
                else:
                    self.assertTrue(owner.core_update_pending())
                    with self.assertRaises(owner.DataOwnerError):
                        with owner.data_owner(self.data):pass
    def test_restore_pending_still_blocks_without_core_marker(self):
        marker=self.root/'RESTORE_PENDING.json';marker.write_bytes(b'{}')
        with self.assertRaises(owner.DataOwnerError):
            with owner.data_owner(self.data):pass
        self.assertFalse(self.pending.exists());self.assertTrue(marker.exists())


class HTTPAdmissionTests(unittest.TestCase):
    def run_gate(self,*,pending=False,session=None,error=False,kind='http',path='/'):
        events=[]
        async def app(scope,receive,send):events.append(('passed',scope['type']))
        async def send(message):events.append(message)
        async def receive():return dict(type='http.request',body=b'')
        with patch.object(startup,'_ACTIVE',session),patch.object(owner,'core_update_pending',
                side_effect=RuntimeError('private failure') if error else None,return_value=pending):
            asyncio.run(startup.ManagedHTTPGate(app)(dict(type=kind,path=path),receive,send))
        return events
    def test_all_http_and_websocket_admission_closes_without_managed_context(self):
        for path in ('/','/login','/api/auth/login','/api/subscription','/sub/share-secret','/api/cores','/ui/index.html'):
            events=self.run_gate(pending=True,path=path)
            self.assertEqual(events[0]['status'],503);self.assertIn('核心维护',events[1]['body'].decode())
            self.assertNotIn('share-secret',events[1]['body'].decode())
        self.assertEqual(self.run_gate(pending=True,kind='websocket')[0]['code'],1013)
    def test_old_released_session_cannot_override_new_core_pending_and_lifespan_passes(self):
        session=object.__new__(startup.ManagedStartup)
        with patch.object(startup.ManagedStartup,'released',new_callable=PropertyMock,return_value=True):
            self.assertEqual(self.run_gate(pending=True,session=session)[0]['status'],503)
            self.assertEqual(self.run_gate(pending=False,session=session),[('passed','http')])
        self.assertEqual(self.run_gate(pending=True,kind='lifespan'),[('passed','lifespan')])
    def test_check_exception_is_fail_closed_and_absence_keeps_normal_pass_through(self):
        self.assertEqual(self.run_gate(error=True)[0]['status'],503)
        self.assertEqual(self.run_gate(),[('passed','http')])
    def test_main_data_owner_exception_handler_is_chinese_503_without_private_error(self):
        tree=ast.parse((ROOT/'main.py').read_text())
        node=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='data_admission_refused')
        node.decorator_list=[]
        def response(value,status_code,headers):return value,status_code,headers
        namespace=dict(Request=object,DataOwnerError=owner.DataOwnerError,JSONResponse=response)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<admission-handler>','exec'),namespace)
        body,status,headers=asyncio.run(namespace['data_admission_refused'](None,owner.DataOwnerError('/private/credential')))
        self.assertEqual(status,503);self.assertIn('维护',body['detail']);self.assertNotIn('private',str(body))
        self.assertEqual(headers,{'Cache-Control':'no-store'})


class ComponentStageLeaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture=backup_fixtures.CompleteBackupTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.pending=self.fixture.root/'CORE_UPDATE_PENDING.json'
        scope=patch.object(owner,'_CORE_UPDATE_PENDING',self.pending);scope.start();self.addCleanup(scope.stop)
    def test_dns_proxy_snapshot_create_and_preflight_hold_real_private_ex_while_pending(self):
        fixture=self.fixture
        state,intents=fixture.state();fixture.active_proxy(state,intents);fixture.dns_fixture()
        original={path.relative_to(fixture.data).as_posix():path.read_bytes()
                  for path in fixture.data.rglob('*') if path.is_file() and path.name not in ('owner.lock','.proxy-state.lock')}
        self.pending.write_bytes(b'opaque Root transaction');self.pending.chmod(0o644)
        with self.assertRaises(owner.DataOwnerError):
            with owner.data_owner(fixture.data):pass
        # The public-looking fake stopped flag alone cannot bypass source SH.
        with self.assertRaises(tools.ReleaseError):fixture.create(state,intents)
        self.assertFalse(fixture.archive.exists())
        with tools.stopped(fixture.root) as guard:
            checksum=archive.create(guard,fixture.archive,core_backup=fixture.fixture.tool,
                selection=state,intents=intents,panel_was_running=True)
        with zipfile.ZipFile(fixture.archive) as outer,zipfile.ZipFile(io.BytesIO(outer.read('data/snapshot.zip'))) as inner:
            self.assertFalse(any('owner.lease' in name for name in inner.namelist()))
            self.assertNotIn('.proxy-state.lock',inner.namelist())
        with fixture.preflight(checksum) as checked:
            self.assertEqual(checked.data_kind,'backup-dns01-v1')
            sibling=checked.data_path.parent/'.data.owner.lease'
            self.assertTrue(sibling.is_file());self.assertEqual(sibling.stat().st_size,0)
            # Preflight does not retain a permit or shared bypass for the caller.
            with self.assertRaises(owner.DataOwnerError):
                with owner.data_owner(checked.data_path):pass
        self.assertEqual({name:(fixture.data/name).read_bytes() for name in original},original)
        self.assertEqual(self.pending.read_bytes(),b'opaque Root transaction')


if __name__=='__main__':unittest.main()

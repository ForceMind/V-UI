"""Existing-install choices and durable recovery use private synthetic state."""
import argparse
from contextlib import ExitStack, nullcontext
import io
import json
import os
import subprocess
import signal
import sqlite3
import zipfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from scripts import install_system as installer
from test_installer_localization import Terminal, opened_terminal


class ExistingInstallTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        previous_umask=os.umask(0o022);self.addCleanup(os.umask,previous_umask)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1])))
        self.config = self.root/'etc/service.json'
        self.config.parent.mkdir()
        self.data = self.root/'data-root'; self.data.mkdir(mode=0o700)
        self.control = self.root/'control'; self.control.mkdir()
        self.units = self.root/'units'; self.units.mkdir()
        self.stack.enter_context(patch.multiple(installer, CONFIG=self.config,
            CONFIG_DIR=self.config.parent, ROOT=self.data, CONTROL=self.control))
        self.stack.enter_context(patch.object(installer.service_support, 'SYSTEMD_DIR', self.units))
        self.stack.enter_context(patch.object(installer, 'own_file',
            side_effect=lambda path: path.is_file() and not path.is_symlink()))
        self.saved = dict(installer=1, domain='w6.example.test', email='old@example.test',
            admin='original-admin', port=3215, bind='0.0.0.0', certificate_mode='managed',
            node_port=10443, ready=False, service_manager='systemd')
        self.save()

    def save(self):
        self.config.write_text(json.dumps(self.saved)); self.config.chmod(0o644)

    def args(self, **values):
        result = dict(domain=None,email=None,admin=None,port=None,bind=None,node_port=None,
            cert=None,key=None,accept_terms=False,upgrade=False,interactive=True,
            dry_run=False,health_ca=None,node_udp_port=[],assume_external_ports_open=True,
            open_firewall='no')
        result.update(values)
        return argparse.Namespace(**result)

    def read(self, answers, args=None):
        args = args or self.args()
        terminal = Terminal(answers)
        with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
            installer.read_configuration(args)
        return args, terminal.output.getvalue()

    def test_incomplete_and_ready_install_reuse_saved_values_without_refill(self):
        for ready in (False, True):
            self.saved['ready'] = ready; self.save()
            args, output = self.read('nonsense\n1\ny\n')
            self.assertTrue(args.upgrade)
            self.assertFalse(args._reconfigure)
            for key in ('domain','email','admin','port','bind','node_port'):
                self.assertEqual(getattr(args,key), self.saved[key])
            self.assertNotIn('联系邮箱:', output)
            self.assertNotIn('管理员用户名 [admin]', output)
            self.assertNotIn('面板 HTTPS 端口 [', output)
            self.assertIn('请输入 1、2 或 0', output)
            self.assertIn('已完成' if ready else '尚未完成，可继续安装', output)
            installer.validate_options(args)

    def test_explicit_upgrade_loads_saved_nondefaults_without_menu_or_fields(self):
        args, output = self.read('y\n', self.args(upgrade=True))
        self.assertNotIn('请选择', output)
        self.assertEqual(args.port,3215)
        self.assertEqual(args.admin,'original-admin')

    def test_edit_prefills_and_validates_before_confirmation_and_ca_consent(self):
        args, output = self.read('2\nhttps://bad.test\nNEW.Example.Test.\ninvalid\nnew@example.test\n80\n3220\nnot-an-ip\n127.0.0.1\n8001\ny\ny\n')
        self.assertTrue(args._reconfigure)
        self.assertEqual((args.domain,args.email,args.port,args.bind,args.node_port),
            ('new.example.test','new@example.test',3220,'127.0.0.1',8001))
        self.assertEqual(args.admin,self.saved['admin'])
        self.assertIn('[old@example.test]',output)
        self.assertIn('不改写已有节点',output)
        self.assertLess(output.index('变更内容'),output.index('CA 条款'))
        self.assertEqual(json.loads(self.config.read_text()),self.saved)

    def test_cancel_at_menu_or_change_confirmation_has_no_writes_or_services(self):
        for answers in ('\n','0\n','2\n\n\n\n\n\nn\n'):
            with self.subTest(answers=answers), \
                 patch.object(installer,'write_root_file',side_effect=AssertionError('write')), \
                 patch.object(installer.service_support,'stop',side_effect=AssertionError('stop')):
                with self.assertRaisesRegex(installer.InstallError,'已取消'):
                    self.read(answers)
            self.assertEqual(json.loads(self.config.read_text()),self.saved)

    def test_explicit_conflict_requires_edit_and_cannot_silently_upgrade(self):
        args, output = self.read('1\n2\n\n\n\n\n\ny\ny\n', self.args(email='new@example.test'))
        self.assertIn('参数与原配置不一致', output)
        self.assertEqual(args.email, 'new@example.test')
        for values in ({'admin':'different'}, {'cert':'a','key':'b'}):
            with self.subTest(values=values), self.assertRaises(installer.InstallError):
                self.read('1\ny\n',self.args(**values))

    def test_noninteractive_upgrade_inherits_and_conflict_still_refused(self):
        args = self.args(interactive=False,upgrade=True,accept_terms=True)
        with patch.object(installer,'controlling_terminal',side_effect=AssertionError('unexpected prompt')):
            installer.read_configuration(args)
        self.assertEqual(args.email,self.saved['email'])
        self.assertEqual(args.bind,'0.0.0.0')
        with self.assertRaisesRegex(installer.InstallError,'参数与已有配置不一致'):
            installer.read_configuration(self.args(interactive=False,upgrade=True,email='other@example.test'))

    def test_untrusted_symlink_or_invalid_metadata_rejected_before_questions(self):
        self.config.unlink(); self.config.symlink_to(self.root/'missing')
        with self.assertRaises(installer.InstallError): self.read('1\ny\n')
        self.config.unlink()
        for invalid in ({}, {**self.saved,'ready':'false'}, {**self.saved,'domain':'https://invalid.test'}):
            self.config.write_text(json.dumps(invalid))
            with self.assertRaises(installer.InstallError): self.read('1\ny\n')

    def test_provided_resume_reuses_installed_material_and_never_asks_ca(self):
        self.saved['certificate_mode']='provided'; self.save()
        folder=self.data/'data/certs'; folder.mkdir(parents=True)
        for name in ('provided-fullchain.pem','provided-privkey.pem'): (folder/name).write_text('fixture')
        args, output=self.read('1\n')
        self.assertEqual(args.cert,str(folder/'provided-fullchain.pem'))
        self.assertNotIn('CA 条款',output)

    def test_provided_resume_missing_material_requests_source_paths_after_choice(self):
        self.saved['certificate_mode']='provided'; self.save()
        cert=self.root/'source.pem'; cert.write_text('fixture')
        key=self.root/'source.key'; key.write_text('fixture')
        args, output=self.read('1\n'+str(cert)+'\n'+str(key)+'\n')
        self.assertEqual((args.cert,args.key),(str(cert),str(key)))
        self.assertIn('尚未保存完成',output)

    def test_durable_journal_restores_only_owned_files_and_retains_on_failure(self):
        old=self.config.read_bytes()
        state='{"version":1}'
        with patch.object(installer,'certificate_state',return_value=state) as helper, \
             patch.object(installer.os,'chown'), \
             patch.object(installer,'https_health'), \
             patch.multiple(installer.service_support,stop=Mock(),start=Mock(),reload=Mock()):
            installer.begin_transaction(self.root,'systemd',True,stopped=True)
            journal=self.config.parent/installer.TRANSACTION_NAME
            self.assertEqual(journal.stat().st_mode & 0o777,0o600)
            self.config.write_text('changed')
            helper.side_effect=installer.InstallError('busy')
            with self.assertRaisesRegex(installer.InstallError,'busy'): installer.recover_transaction(self.root)
            self.assertTrue(journal.exists())
            helper.side_effect=None
            installer.recover_transaction(self.root)
            self.assertEqual(self.config.read_bytes(),old)
            self.assertFalse(journal.exists())
            helper.assert_called_with(self.root,'restore',state)

    def test_pre_stop_intent_recovers_running_state_without_db_mutation(self):
        with patch.object(installer,'certificate_state',side_effect=AssertionError('DB not stopped')), \
             patch.object(installer.os,'chown'), \
             patch.object(installer,'https_health'), \
             patch.multiple(installer.service_support,stop=Mock(),start=Mock(),reload=Mock()) as services:
            installer.begin_transaction(self.root,'systemd',True)
            installer.recover_transaction(self.root)
            installer.service_support.start.assert_called_once_with('systemd',['v-ui-http01.socket','v-ui.service'])

    def test_dry_run_pending_transaction_refuses_before_recovery_or_lock(self):
        (self.config.parent/installer.TRANSACTION_NAME).write_text('{}')
        with patch('sys.argv',['installer','--bundle','dummy.zip','--sha256','0'*64,'--dry-run']), \
             patch.object(installer,'recover_transaction',side_effect=AssertionError('mutation')), \
             patch.object(installer,'_installation_lock',side_effect=AssertionError('write lock')):
            with self.assertRaisesRegex(installer.InstallError,'只读预检不会恢复'): installer.main()

    def test_read_and_apply_are_inside_same_install_lock(self):
        events=[]
        class Lease:
            def __enter__(self): events.append('lock')
            def __exit__(self,*args): events.append('unlock')
        with patch('sys.argv',['installer','--bundle','dummy.zip','--sha256','0'*64]), \
             patch.object(installer.os,'geteuid',return_value=0), \
             patch.object(installer,'_installation_lock',return_value=Lease()), \
             patch.object(installer,'verify_archive',return_value=(io.BytesIO(),nullcontext(),{})), \
             patch.object(installer,'read_configuration',side_effect=lambda args:events.append('read')), \
             patch.object(installer,'validate_options'), \
             patch.object(installer,'install_verified',side_effect=lambda *args:events.append('apply')):
            installer.main()
        self.assertEqual(events,['lock','read','apply','unlock'])

    def test_helper_failure_does_not_echo_snapshot_command_or_service_output(self):
        (self.root/'scripts').mkdir()
        (self.root/'scripts/installer_certificate_state.py').write_text('sensitive-source')
        secret='private-contact@example.test'
        for error in (subprocess.CalledProcessError(1,['code',secret],output=secret,stderr=secret),
                      subprocess.TimeoutExpired(['code',secret],360,output=secret)):
            with patch.object(installer,'as_service',side_effect=error):
                with self.assertRaises(installer.InstallError) as caught:
                    installer.certificate_state(self.root,'restore',secret)
            self.assertNotIn(secret,str(caught.exception))
            self.assertNotIn('sensitive-source',str(caught.exception))

    def test_managed_install_failure_or_signal_restores_binding_contact_pointer_and_service(self):
        from sqlalchemy import create_engine
        from app.certificates.models import Certificate, CertificateBinding
        from scripts.installer_certificate_state import run as panel_state
        folder=self.data/'data'; folder.mkdir(mode=0o700)
        db_path=folder/'v-ui.db'
        engine=create_engine('sqlite:///'+str(db_path))
        Certificate.__table__.create(engine); CertificateBinding.__table__.create(engine)
        engine.dispose()
        old_id='a'*32; new_id='b'*32
        self.saved['ready']=True; self.save()
        original=self.config.read_bytes()
        pointer={'release_id':'old-release','previous_id':None}
        manifest={'release_id':'new-release'}
        account=argparse.Namespace(pw_uid=os.getuid(),pw_gid=os.getgid())
        source=Path(__file__).resolve().parents[1]
        packed=io.BytesIO()
        with zipfile.ZipFile(packed,'w') as archive:
            for name in (*installer.CONTROLLER_MEMBERS,'deploy/system_launcher.py'):
                archive.writestr(name,(source/name).read_bytes())
        def certificate(db,identifier,domain,email):
            db.execute('INSERT INTO managed_certificates '
                '(id,domain,email,environment,auto_renew,renew_at,retry_at,last_attempt,failures,created_at) '
                'VALUES (?,?,?,\'production\',0,0,0,0,0,0)',(identifier,domain,email))
        for failure in ('health','interrupt','terminate','first-stop'):
            with self.subTest(failure=failure):
                self.config.write_bytes(original)
                (self.data/'CURRENT.json').write_text(json.dumps(pointer))
                (self.data/'CURRENT.json').chmod(0o600)
                for name in ('v-ui.service','v-ui-http01.service','v-ui-http01.socket'):
                    (self.units/name).write_text(installer.MARKER+'original')
                with sqlite3.connect(db_path) as db:
                    db.execute('DELETE FROM certificate_bindings'); db.execute('DELETE FROM managed_certificates')
                    db.execute('CREATE TABLE IF NOT EXISTS preservation (value TEXT)')
                    db.execute('DELETE FROM preservation'); db.execute("INSERT INTO preservation VALUES ('session-grant-user-node')")
                    certificate(db,old_id,self.saved['domain'],self.saved['email'])
                    certificate(db,new_id,'changed.example.test','before-change@example.test')
                    db.execute('INSERT INTO certificate_bindings VALUES (?,?,?,?,?)',('panel',old_id,old_id,'a'*64,None))
                args=self.args(domain='changed.example.test',email='after-change@example.test',
                    admin=self.saved['admin'],port=3220,bind='0.0.0.0',node_port=10443,
                    accept_terms=True,upgrade=True,sha256='0'*64)
                args._reconfigure=True;args._existing_config=dict(self.saved)
                active={'value':True,'stops':0,'health':0}
                def stop(manager,names):
                    active['stops']+=1
                    if failure=='first-stop' and active['stops']==1:
                        raise KeyboardInterrupt()
                    if 'v-ui.service' in names: active['value']=False
                def start(manager,names):
                    if 'v-ui.service' in names: active['value']=True
                def health(*unused,**kwargs):
                    active['health']+=1
                    if active['health']==1:
                        if failure=='interrupt':raise KeyboardInterrupt()
                        if failure=='terminate':os.kill(os.getpid(),signal.SIGTERM)
                        raise installer.InstallError('synthetic health failure')
                def service(command,*,capture=False,**kwargs):
                    if '-c' in command:
                        at=command.index('-c')
                        if at+2<len(command) and command[at+2] in ('snapshot','restore','contact'):
                            value=panel_state(command[at+2],self.data,*command[at+4:])
                            return subprocess.CompletedProcess(command,0,json.dumps(value),'')
                        return subprocess.CompletedProcess(command,0,'1\n','')
                    if 'activate' in command:
                        (self.data/'CURRENT.json').write_text(json.dumps({'release_id':'new-release','previous_id':'old-release'}))
                    if 'app.certificates.cli' in command:
                        with sqlite3.connect(db_path) as db:
                            db.execute('UPDATE certificate_bindings SET certificate_id=?,applied_certificate_id=?,applied_revision=? WHERE target=\'panel\'',(new_id,new_id,'b'*64))
                    return subprocess.CompletedProcess(command,0,'','')
                with patch.object(installer,'check_platform',return_value=dict(init='systemd',name='fixture',target='x86_64-gnu')), \
                     patch.object(installer,'check_reserved'),patch.object(installer,'check_port'), \
                     patch.object(installer,'probe_ipv6',return_value=False),patch.object(installer,'firewall_preflight'), \
                     patch.object(installer,'os',argparse.Namespace(**{**vars(os),'geteuid':lambda:0})), \
                     patch.object(installer.os,'chown'),patch.object(installer.pwd,'getpwnam',return_value=account), \
                     patch.object(installer,'as_service',side_effect=service), \
                     patch.object(installer,'https_health',side_effect=health), \
                     patch.multiple(installer.service_support,stop=Mock(side_effect=stop),start=Mock(side_effect=start),
                         is_active=Mock(return_value=True),reload=Mock(),enable_start=Mock()), \
                     installer.installation_signals(),zipfile.ZipFile(packed) as archive:
                    # The first stop failure reaches only the recovery health
                    # call; the saved service itself remains healthy.
                    if failure=='first-stop': active['health']=1
                    with self.assertRaises((installer.InstallError,KeyboardInterrupt)) as caught:
                        installer.install_verified(args,io.BytesIO(packed.getvalue()),archive,manifest)
                self.assertEqual(self.config.read_bytes(),original)
                self.assertEqual(json.loads((self.data/'CURRENT.json').read_text()),pointer)
                self.assertTrue(active['value'])
                self.assertGreaterEqual(active['health'],2,str(caught.exception))
                self.assertFalse((self.config.parent/installer.TRANSACTION_NAME).exists())
                with sqlite3.connect(db_path) as db:
                    self.assertEqual(db.execute("SELECT certificate_id,applied_revision FROM certificate_bindings WHERE target='panel'").fetchone(),(old_id,'a'*64))
                    self.assertEqual(db.execute('SELECT email FROM managed_certificates WHERE id=?',(new_id,)).fetchone()[0],'before-change@example.test')
                    self.assertEqual(db.execute('SELECT value FROM preservation').fetchone()[0],'session-grant-user-node')

    def test_health_uses_specific_bind_and_keeps_tls_default_roots(self):
        context=MagicMock();tls=Mock();response=Mock(status=401)
        context.wrap_socket.return_value.__enter__.return_value=tls
        with patch.object(installer.ssl,'create_default_context',return_value=context), \
             patch.object(installer.socket,'create_connection') as connection, \
             patch.object(installer.http.client,'HTTPResponse',return_value=response):
            installer.https_health({'bind':'192.0.2.2','port':3215,'domain':'panel.example.test'},'private-ca.pem')
            connection.assert_called_once_with(('192.0.2.2',3215),timeout=2)
            context.load_verify_locations.assert_called_once_with(cafile='private-ca.pem')
            context.wrap_socket.assert_called_once()
            self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'],'panel.example.test')

    def test_python39_saved_chain_flag_uses_checked_openssl_capability(self):
        context=MagicMock(verify_flags=0);response=Mock(status=401)
        for version, accepted in (('OpenSSL 1.1.1', True),('LibreSSL 2.8.3',False)):
            with self.subTest(version=version), \
                 patch.object(installer.ssl,'VERIFY_X509_PARTIAL_CHAIN',None,create=True), \
                 patch.object(installer.ssl,'OPENSSL_VERSION',version), \
                 patch.object(installer.ssl,'OPENSSL_VERSION_INFO',(1,1,1,0,0)), \
                 patch.object(installer.ssl,'create_default_context',return_value=context), \
                 patch.object(installer.socket,'create_connection'), \
                 patch.object(installer.http.client,'HTTPResponse',return_value=response):
                if accepted:
                    installer.https_health({'bind':'0.0.0.0','port':3215,'domain':'panel.example.test'},'saved.pem',trust_saved_chain=True)
                    self.assertTrue(context.verify_flags & 0x80000)
                else:
                    with self.assertRaisesRegex(installer.InstallError,'不支持验证原证书链'):
                        installer.https_health({'bind':'0.0.0.0','port':3215,'domain':'panel.example.test'},'saved.pem',trust_saved_chain=True)


if __name__=='__main__': unittest.main()

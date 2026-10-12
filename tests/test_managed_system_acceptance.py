"""Pure contracts for an opt-in qualification harness; never sudo/systemd here."""
import argparse
from contextlib import ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock, mock_open
import zipfile

from scripts import managed_system_acceptance as gate

COMMIT='a'*40


def environment():
    return {'VUI_MANAGED_SYSTEM_ACCEPTANCE':'ROOT-SYSTEMD:'+COMMIT,'GITHUB_ACTIONS':'true',
            'RUNNER_ENVIRONMENT':'github-hosted','ImageOS':'ubuntu24','RUNNER_ARCH':'X64'}


class QualificationContracts(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.base=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.qual=gate.Qualification(self.base,{'source_commit':COMMIT,'manifest_sha256':'b'*64},
                                     {'v-ui':b'wrapper'},self.base/'result.json')

    def test_gate_requires_each_explicit_exact_head_hosted_marker_and_nonroot(self):
        with patch.object(gate.platform,'system',return_value='Linux'),patch.object(gate.platform,'machine',return_value='x86_64'):
            gate.require_gate(COMMIT,environment(),uid=1000)
            for key in environment():
                changed=environment();changed.pop(key)
                with self.subTest(key=key),self.assertRaises(gate.QualificationError):gate.require_gate(COMMIT,changed,uid=1000)
            with self.assertRaises(gate.QualificationError):gate.require_gate('b'*40,environment(),uid=1000)
            with self.assertRaises(gate.QualificationError):gate.require_gate(COMMIT,environment(),uid=0)
            with self.assertRaises(gate.QualificationError):gate.require_gate('main',environment(),uid=1000)
            for key in ('VUI_ROOT_RESOURCE_CONFIG','VUI_SYSTEM_INSTALL_TEST'):
                with self.subTest(key=key),self.assertRaises(gate.QualificationError):
                    gate.require_gate(COMMIT,{**environment(),key:'1'},uid=1000)

    def test_unauthorized_default_entry_has_no_source_preflight_hook_or_cleanup(self):
        args=argparse.Namespace(source_commit=COMMIT,kit=self.base,sha256='b'*64,evidence_dir=self.base/'evidence')
        with patch.dict(os.environ,{},clear=True),patch.object(gate,'source_contract') as source, \
                patch.object(gate,'empty_host') as preflight,patch.object(gate.Qualification,'cleanup') as cleanup:
            with self.assertRaises(gate.QualificationError):gate.run_qualification(args)
            source.assert_not_called();preflight.assert_not_called();cleanup.assert_not_called()
        self.assertFalse(args.evidence_dir.exists())

    def test_source_failure_and_preflight_failure_never_obtain_cleanup_or_sudo_rights(self):
        args=argparse.Namespace(source_commit=COMMIT,kit=self.base,sha256='b'*64,evidence_dir=self.base/'evidence')
        for phase in ('source','preflight'):
            with self.subTest(phase=phase),patch.object(gate,'require_gate'), \
                    patch.object(gate,'source_contract',side_effect=RuntimeError('wrong source') if phase=='source' else None,
                                 return_value=({},{})), \
                    patch.object(gate,'empty_host',side_effect=RuntimeError('occupied')), \
                    patch.object(gate.Qualification,'cleanup') as cleanup,patch.object(gate.Qualification,'run') as run:
                with self.assertRaises(RuntimeError):gate.run_qualification(args)
                cleanup.assert_not_called();run.assert_not_called()
        self.assertFalse(args.evidence_dir.exists())

    def test_source_contract_requires_git_kit_bundle_and_every_harness_blob(self):
        from scripts import install_ops_control
        kit=self.base/'kit';kit.mkdir();source=self.base/'source';source.mkdir()
        record={'source_commit':COMMIT,'targets':['x86_64-gnu'],'version':'0.4.7','release_id':'fixture'}
        for name in gate.DEPENDENCIES:
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# exact accepted dependency\n')
        with zipfile.ZipFile(kit/'vui-source.zip','w') as archive:
            for name in gate.DEPENDENCIES:archive.writestr(name,(source/name).read_bytes())
        with zipfile.ZipFile(kit/'vui-linux-x86_64-gnu.zip','w') as archive:
            archive.writestr('MANIFEST.json',json.dumps({**record,'kind':'release'}))
        with patch.object(gate.subprocess,'check_output',side_effect=lambda args,**kwargs:COMMIT if args[-1]=='HEAD' else 'c'*40), \
                patch.object(gate.subprocess,'run'),patch.object(install_ops_control,'verify_kit',return_value=({},record)):
            value,_=gate.source_contract(kit,'b'*64,COMMIT,source=source)
            self.assertEqual(value['source_commit'],COMMIT)
            self.assertEqual(set(value['harness_blobs']),set(gate.DEPENDENCIES))
            (source/gate.DEPENDENCIES[-1]).write_text('# dirty controller\n')
            with self.assertRaisesRegex(gate.QualificationError,'blob_mismatch'):
                gate.source_contract(kit,'b'*64,COMMIT,source=source)
        with patch.object(gate.subprocess,'check_output',return_value='b'*40),patch.object(install_ops_control,'verify_kit') as verify:
            with self.assertRaises(gate.QualificationError):gate.source_contract(kit,'b'*64,COMMIT,source=source)
            verify.assert_not_called()

    def test_empty_preflight_includes_wrapper_lock_and_existing_group(self):
        from scripts import low_resource_root
        with patch.object(low_resource_root,'preflight',side_effect=RuntimeError('existing account')) as check:
            with self.assertRaises(RuntimeError):gate.empty_host()
            self.assertIn(gate.ENTRY,check.call_args.kwargs['reserved'])
            self.assertIn(gate.LOCK,check.call_args.kwargs['reserved'])
            self.assertFalse(check.call_args.kwargs['port'])

    def test_directory_children_do_not_change_ownership_but_inode_replacement_does(self):
        path=self.base/'owned';path.mkdir(mode=0o700)
        old=gate.fingerprint(path)
        (path/'child').mkdir()
        self.assertEqual(old,gate.fingerprint(path))
        renamed=self.base/'old';path.rename(renamed);path.mkdir(mode=0o700)
        self.assertNotEqual(old['inode'],gate.fingerprint(path)['inode'])

    def test_cleanup_ledger_rejects_unknown_path_changed_inode_link_and_permissions(self):
        path='/usr/local/sbin/v-ui'
        record={'device':1,'inode':2,'uid':0,'gid':0,'type':stat.S_IFREG,'mode':0o755,'links':1}
        gate.validate_cleanup_ledger({path:record},{path:record})
        for mutation in ('path','inode','symlink','hardlink','mode'):
            actual=dict(record);name=path
            if mutation=='path':name='/etc/passwd'
            elif mutation=='inode':actual['inode']=4
            elif mutation=='symlink':actual['type']=stat.S_IFLNK
            elif mutation=='hardlink':actual['links']=2
            else:actual['mode']=0o777
            expected=actual if mutation in ('symlink','hardlink','mode') else record
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):
                gate.validate_cleanup_ledger({name:expected},{name:actual})
        with self.assertRaises(RuntimeError):gate.validate_cleanup_ledger({}, {})

    def test_cleanup_without_creation_has_no_external_action(self):
        with patch.object(self.qual,'capture_owned') as capture,patch.object(self.qual,'root') as root:
            self.qual.cleanup();capture.assert_not_called();root.assert_not_called()

    def test_expired_work_has_separate_bounded_cleanup_budget(self):
        self.qual.creation_started=True;self.qual.deadline=0
        self.qual.account={'uid':123,'gid':123,'home':'/var/lib/v-ui'}
        self.qual.ledger={'/var/lib/v-ui':{}}
        with patch.object(self.qual,'capture_owned'),patch.object(self.qual,'root',return_value={'owned_resources_removed':True}) as root:
            self.qual.cleanup()
            self.assertLessEqual(root.call_args.kwargs['timeout'],120)
            self.assertGreater(self.qual.remaining(10),0)

    def test_unconfirmed_menu_child_cannot_delete_resources_or_claim_cleanup_success(self):
        self.qual.creation_started=True;self.qual.menu_in_flight=True
        self.qual.account={'uid':123,'gid':123,'home':'/var/lib/v-ui'};self.qual.ledger={'/var/lib/v-ui':{}}
        with patch.object(self.qual,'capture_owned'),patch.object(self.qual,'root',return_value={'owned_resources_removed':False}) as root:
            with self.assertRaisesRegex(gate.QualificationError,'cleanup_unconfirmed'):self.qual.cleanup()
            self.assertTrue(root.call_args.args[1]['stop_only'])
            self.assertFalse(self.qual.report['phases'][-1]['owned_resources_removed'])

    def test_child_signal_requires_owned_unreaped_pid_and_exact_process_group(self):
        with patch.object(gate.os,'waitid',side_effect=ChildProcessError),patch.object(gate.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'identity_lost'):gate.owned_child_signal(123,signal.SIGTERM)
            kill.assert_not_called()
        with patch.object(gate.os,'waitid',return_value=None),patch.object(gate.os,'getpgid',return_value=124),patch.object(gate.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'group_changed'):gate.owned_child_signal(123,signal.SIGTERM)
            kill.assert_not_called()
        with patch.object(gate.os,'waitid',return_value=object()),patch.object(gate.os,'getpgid',return_value=123),patch.object(gate.os,'killpg') as kill:
            gate.owned_child_signal(123,signal.SIGKILL);kill.assert_called_once_with(123,signal.SIGKILL)

    def test_stable_identity_rejects_exec_pid_reuse_reparent_and_parent_change(self):
        parent={'pid':10,'starttime':12,'ppid':1,'uid':[0]*4,'gid':[0]*4,'argv':['wrapper']}
        child={'pid':11,'starttime':14,'ppid':10,'uid':[123]*4,'gid':[123]*4,'argv':['worker']}
        self.assertEqual(gate.stable_identity(11,parent,reader=Mock(side_effect=[child,child,parent])),child)
        for key,value in (('starttime',15),('ppid',99),('uid',[0]*4),('argv',['other'])):
            changed={**child,key:value}
            with self.subTest(key=key):self.assertIsNone(gate.stable_identity(11,parent,reader=Mock(side_effect=[child,changed])))
        self.assertIsNone(gate.stable_identity(11,parent,reader=Mock(side_effect=[child,child,{**parent,'starttime':13}])))
        self.assertIsNone(gate.stable_identity(11,parent,reader=Mock(side_effect=FileNotFoundError)))

    def test_missing_worker_observation_is_failure_not_inferred_identity(self):
        self.qual.account={'uid':123,'gid':123}
        with patch.object(self.qual,'root',return_value={'workers':[]}):
            with self.assertRaisesRegex(gate.QualificationError,'unobserved'):self.qual.menu('backup')
            self.assertTrue(self.qual.menu_in_flight)
            self.qual.menu('status')
            self.qual.menu('cancel')

    def test_wrong_worker_identity_keeps_cleanup_unconfirmed(self):
        self.qual.account={'uid':123,'gid':123}
        row={'uid':[0]*4,'gid':[123]*4,'groups':[],'cap_eff':'0'}
        with patch.object(self.qual,'root',return_value={'workers':[row]}):
            with self.assertRaisesRegex(gate.QualificationError,'did_not_demote'):self.qual.menu('backup')
        self.assertTrue(self.qual.menu_in_flight)

    def test_privileged_installer_timeout_never_allows_resource_deletion(self):
        self.qual.creation_started=True
        self.qual.account={'uid':123,'gid':123,'home':'/var/lib/v-ui'};self.qual.ledger={'/var/lib/v-ui':{}}
        with patch.object(gate.subprocess,'run',side_effect=subprocess.TimeoutExpired('sudo',180)):
            with self.assertRaises(subprocess.TimeoutExpired):self.qual.control_install()
        self.assertTrue(self.qual.cleanup_uncertain)
        with patch.object(self.qual,'capture_owned'),patch.object(self.qual,'root',return_value={'owned_resources_removed':False}) as root:
            with self.assertRaisesRegex(gate.QualificationError,'cleanup_unconfirmed'):self.qual.cleanup()
            self.assertTrue(root.call_args.args[1]['stop_only'])

    def test_stop_only_root_helper_reaches_no_delete_even_with_owned_empty_cgroups(self):
        # Every host read/command is fake. No real root, systemd, account, or path
        # mutation is performed by this unit test.
        import pwd,grp,shutil
        row={'device':1,'inode':2,'uid':0,'gid':0,'type':stat.S_IFREG,'mode':0o644,'links':1}
        ledger={str(gate.UNIT_DIR/name):row for name in gate.UNITS}
        account={'uid':123,'gid':123,'home':'/var/lib/v-ui'}
        user=Mock(pw_uid=123,pw_gid=123,pw_dir='/var/lib/v-ui',pw_name='v-ui')
        request={'ledger':ledger,'account':account,'stop_only':True,
                 'unit_hashes':{name:hashlib.sha256(b'unit').hexdigest() for name in gate.UNITS}}
        with patch.object(gate.os,'geteuid',return_value=0),patch.object(gate,'fingerprint',return_value=row), \
                patch.object(pwd,'getpwnam',return_value=user),patch.object(pwd,'getpwall',return_value=[user]), \
                patch.object(grp,'getgrnam',return_value=Mock(gr_gid=123,gr_mem=[])), \
                patch('builtins.open',mock_open(read_data=b'unit')), \
                patch.object(gate.os.path,'isfile',return_value=True),patch.object(gate.os.path,'lexists',return_value=False), \
                patch.object(gate.subprocess,'run',return_value=Mock(returncode=0,stdout='inactive\n0\n')) as run, \
                patch.object(gate.os,'unlink') as unlink,patch.object(shutil,'rmtree') as rmtree:
            result=gate._root_cleanup(request)
            self.assertFalse(result['owned_resources_removed'])
            unlink.assert_not_called();rmtree.assert_not_called()
            self.assertTrue(all(command.args[0][0]=='/usr/bin/systemctl' for command in run.call_args_list))

    def test_final_report_requires_all_phases_confirmed_cleanup_and_no_uncertainty(self):
        rows=[{'phase':name} for name in gate.REQUIRED_PHASES]
        rows[-1]['owned_resources_removed']=True
        for mutation in ('none','missing','cleanup_false','uncertain','prior_failure'):
            self.qual.report.update(outcome='awaiting_cleanup',phases=copy.deepcopy(rows))
            self.qual.cleanup_uncertain=mutation=='uncertain'
            if mutation=='missing':self.qual.report['phases'].pop(0)
            if mutation=='cleanup_false':self.qual.report['phases'][-1]['owned_resources_removed']=False
            if mutation=='prior_failure':self.qual.report['outcome']='failed'
            self.qual.finalize_report()
            self.assertEqual(json.loads(self.qual.output.read_text())['outcome'],
                             'passed' if mutation=='none' else 'failed',mutation)

    def test_root_menu_timeout_leaves_cleanup_unconfirmed(self):
        with patch.object(self.qual,'root',side_effect=subprocess.TimeoutExpired('root helper',600)):
            with self.assertRaises(subprocess.TimeoutExpired):self.qual.menu('backup')
        self.assertTrue(self.qual.menu_in_flight)

    def test_no_action_starts_without_reserved_menu_cleanup_time(self):
        self.qual.deadline=gate.time.monotonic()+100
        with patch.object(self.qual,'root') as root:
            with self.assertRaisesRegex(gate.QualificationError,'cleanup_budget'):self.qual.menu('backup')
            root.assert_not_called()

    def test_baseline_command_is_fixed_accepted_kit_and_explicitly_disables_bytecode(self):
        args=['sudo','bash',str(gate.SOURCE/'install.sh'),'--bundle','synthetic','--sha256','b'*64]
        command=self.qual.command(args)
        self.assertIn(str(self.base/'install.sh'),command)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1',command)
        self.assertTrue(self.qual.creation_started)
        with self.assertRaises(gate.QualificationError):self.qual.command(['sudo','bash','/tmp/unrelated'])

    def test_partial_fixture_removal_requires_receipt_and_exact_owned_fixed_bytes(self):
        from deploy import system_ops
        with patch.object(system_ops,'verify_installation',side_effect=RuntimeError('receipt drift')),patch.object(self.qual,'run') as run:
            with self.assertRaises(RuntimeError):self.qual.partial_refresh()
            run.assert_not_called()
        with patch.object(system_ops,'verify_installation'),patch.object(self.qual,'run') as run:
            with self.assertRaisesRegex(gate.QualificationError,'not_claimed'):self.qual.partial_refresh()
            run.assert_not_called()

    def test_fixed_root_programs_compile_without_running_and_contain_no_payload_import(self):
        for function in (gate._root_menu,gate._root_cleanup):
            code=gate.root_program(function)
            compile(code,'<qualification-root-program>','exec')
            self.assertNotIn('sys.path',code);self.assertNotIn('import app',code)
            self.assertNotIn('/payload/',code);self.assertNotIn('/runtime/python/',code)
            self.assertNotIn('/environ',code)
        cleanup=gate.root_program(gate._root_cleanup)
        self.assertLess(cleanup.index("if request['stop_only']"),cleanup.index('shutil.rmtree(pieces[-1]'))
        self.assertIn('cleanup_cgroup_not_empty',cleanup)
        self.assertIn('os.O_NOFOLLOW',cleanup)
        self.assertNotIn('rm -rf',cleanup)
        for action in ('state','seed','mutate','make-invalid','prepare-recovery'):
            compile(gate.service_program(action),'<nonroot-fixture>','exec')
        with self.assertRaises(gate.QualificationError):gate.service_program('execute shell')

    def test_dependency_shutdown_times_are_not_command_dispatch_order(self):
        from deploy import system_ops as ops
        from scripts import service_support
        import http.client
        # Canonical Requires= pulls the responder into the socket stop job.
        # systemd socket Before=service reverses during shutdown, so that job
        # completes responder then socket before the next panel stop command.
        units=service_support.service_files({'service_manager':'systemd','bootstrap_python':'/fixed/python','ipv6':False})
        self.assertIn('Requires=v-ui-http01.socket\n',units[gate.UNIT_DIR/'v-ui-http01.service'][0])
        self.assertIn('Service=v-ui-http01.service\n',units[gate.UNIT_DIR/'v-ui-http01.socket'][0])
        states={name:True for name in gate.UNITS};times={};commands=[]
        def properties(unit):
            socket=unit.endswith('.socket');active=states[unit]
            fields={'LoadState':'loaded','ActiveState':'active' if active else 'inactive',
                'SubState':('listening' if socket else 'running') if active else 'dead',
                'NeedDaemonReload':'no','FragmentPath':str(gate.UNIT_DIR/unit),'DropInPaths':'',
                'ControlPID':'0','ControlGroup':'/system.slice/'+unit if active else '',
                'User':'' if socket else 'v-ui','Group':'' if socket else 'v-ui','KillMode':'control-group'}
            if not socket:fields['MainPID']='123' if active else '0'
            return 0,''.join(key+'='+value+'\n' for key,value in fields.items()).encode()
        def command(argv,*args,**kwargs):
            commands.append(argv)
            if argv[1]=='show':return properties(argv[-1])
            self.assertEqual(argv[1],'stop')
            if argv[-1]==gate.UNITS[2]:
                states[gate.UNITS[1]]=False;times[gate.UNITS[1]]=10
                states[gate.UNITS[2]]=False;times[gate.UNITS[2]]=20
            elif states[argv[-1]]:
                states[argv[-1]]=False;times[argv[-1]]=30
            return 0,b''
        class ReachedStart(Exception):pass
        def menu(action):
            if action=='stop':ops.Services('systemd').stop_all()
            elif action=='start':raise ReachedStart()
        self.qual.account={'uid':123,'gid':123};(self.base/'ops-control.json').write_text('{}')
        connection=Mock();connection.getresponse.return_value.status=404
        with patch.object(self.qual,'capture_owned'),patch.object(self.qual,'states',return_value={name:'active' for name in gate.UNITS}), \
                patch.object(self.qual,'control_install',return_value=0),patch.object(gate,'CONTROL',self.base), \
                patch.object(ops,'verify_installation',return_value=('systemd',Mock(pw_uid=123))), \
                patch.object(ops,'trusted_executable'),patch.object(ops,'run_command',side_effect=command), \
                patch.object(ops,'cgroup_empty',return_value=True) as empty, \
                patch.object(http.client,'HTTPConnection',return_value=connection), \
                patch.object(self.qual,'menu',side_effect=menu), \
                patch.object(self.qual,'run',side_effect=lambda argv:Mock(stdout=str(times[argv[-1]])+'\n')):
            with self.assertRaises(ReachedStart):self.qual.finish(self,[],Mock(),Mock())
        self.assertEqual([argv for argv in commands if argv[1]=='stop'],
                         [['/usr/bin/systemctl','stop',name] for name in (gate.UNITS[2],gate.UNITS[0],gate.UNITS[1])])
        self.assertEqual([call.args[0] for call in empty.call_args_list],list(gate.UNITS)*2)
        self.assertFalse(times[gate.UNITS[2]]<=times[gate.UNITS[0]]<=times[gate.UNITS[1]])
        row=next(row for row in self.qual.report['phases'] if row['phase']=='stop_timestamp_observation')
        self.assertEqual(row['inactive_monotonic'],times)
        self.assertNotIn('stop_start',[row['phase'] for row in self.qual.report['phases']])

    def test_timestamp_observations_require_positive_bounded_ascii_uint64(self):
        for value in (1,2**64-1):
            self.assertEqual(gate.stop_timestamp(str(value)),value)
            self.assertEqual(gate.stop_timestamp(str(value)+'\n'),value)
        for raw in ('','0','0\n','-1','+1','01',' 1','1 ','1\n2','1\r\n','١',
                    str(2**64),'9'*1000,None):
            with self.subTest(raw=raw):
                self.qual.report['checkpoints']=[];self.qual.report['phases']=[]
                with patch.object(self.qual,'menu') as menu,patch.object(self.qual,'stopped'), \
                        patch.object(self.qual,'run',return_value=Mock(stdout=raw)), \
                        patch.object(self.qual,'api_ready') as api:
                    with self.assertRaisesRegex(gate.QualificationError,'invalid_stop_timestamp'):
                        self.qual.stop_start(Mock())
                    self.assertEqual([call.args[0] for call in menu.call_args_list],['stop'])
                    api.assert_not_called()
                self.assertEqual(self.qual.report['checkpoints'][-1],
                    {'name':'stop_timestamps','status':'failed','error_type':'QualificationError',
                     'error_code':'invalid_stop_timestamp'})
                self.assertNotIn('stop_start',[row['phase'] for row in self.qual.report['phases']])

    def test_stop_proof_rejects_each_unknown_active_or_residual_unit_before_start(self):
        from deploy import system_ops as ops
        for bad_unit in gate.UNITS:
            for problem in ('unknown','active','residual','missing'):
                with self.subTest(unit=bad_unit,problem=problem):
                    self.qual.report['checkpoints']=[];self.qual.report['phases']=[]
                    def command(argv,*args,**kwargs):
                        unit=argv[-1];socket=unit.endswith('.socket')
                        if problem=='missing' and unit==bad_unit:return 4,b''
                        active=problem=='active' and unit==bad_unit
                        fields={'LoadState':'loaded','ActiveState':'active' if active else 'inactive',
                            'SubState':('listening' if socket else 'running') if active else 'dead',
                            'NeedDaemonReload':'no','FragmentPath':str(gate.UNIT_DIR/unit),'DropInPaths':'',
                            'ControlPID':'0','ControlGroup':'/system.slice/'+unit if active else '',
                            'User':'' if socket else 'v-ui','Group':'' if socket else 'v-ui','KillMode':'control-group'}
                        if not socket:fields['MainPID']='123' if active else '0'
                        if problem=='unknown' and unit==bad_unit:fields['ActiveState']='unknown'
                        return 0,''.join(key+'='+value+'\n' for key,value in fields.items()).encode()
                    with patch.object(ops,'trusted_executable'),patch.object(ops,'run_command',side_effect=command), \
                            patch.object(ops,'cgroup_empty',side_effect=lambda unit:not (problem=='residual' and unit==bad_unit)), \
                            patch.object(self.qual,'menu') as menu,patch.object(self.qual,'run') as timestamps:
                        with self.assertRaises(ops.Refused):self.qual.stop_start(Mock())
                        self.assertEqual([call.args[0] for call in menu.call_args_list],['stop'])
                        timestamps.assert_not_called()
                    self.assertEqual(self.qual.report['checkpoints'][-1]['name'],'stop_proof')
                    self.assertEqual(self.qual.report['checkpoints'][-1]['status'],'failed')
                    self.assertNotIn('stop_start',[row['phase'] for row in self.qual.report['phases']])

    def test_stop_start_success_records_observations_and_bounded_separate_checkpoints(self):
        with patch.object(self.qual,'menu') as menu,patch.object(self.qual,'stopped') as proof, \
                patch.object(self.qual,'run',side_effect=[Mock(stdout=str(value)) for value in (30,10,20)]) as run, \
                patch.object(self.qual,'api_ready') as api,patch.object(self.qual,'states',return_value={}):
            self.qual.stop_start(Mock())
        self.assertEqual([call.args[0] for call in menu.call_args_list],['stop','start'])
        proof.assert_called_once_with();api.assert_called_once()
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],list(gate.UNITS))
        self.assertEqual(self.qual.report['checkpoints'],
            [{'name':name,'status':status} for name in gate.CHECKPOINT_NAMES for status in ('begin','passed')])
        self.assertEqual(len(self.qual.report['checkpoints']),gate.CHECKPOINT_LIMIT)
        phases=[row['phase'] for row in self.qual.report['phases']]
        self.assertEqual(phases,['stop_timestamp_observation','stop_start'])
        for name in ('unknown/private-path','stop_menu'):
            with self.assertRaisesRegex(gate.QualificationError,'checkpoint_contract_violation'):
                with self.qual.checkpoint(name):pass
        self.assertEqual(len(self.qual.report['checkpoints']),gate.CHECKPOINT_LIMIT)

    def test_checkpoints_and_prefixed_records_cannot_complete_required_phases(self):
        for name in gate.CHECKPOINT_NAMES:
            with self.qual.checkpoint(name):pass
        self.qual.report['outcome']='awaiting_cleanup'
        self.qual.report['phases']=[{'phase':name,'owned_resources_removed':True}
            for name in gate.REQUIRED_PHASES if name!='stop_start']
        for suffix in ('.begin','.passed','_observation'):
            self.qual.report['phases'].append({'phase':'stop_start'+suffix})
        self.qual.finalize_report()
        self.assertEqual(self.qual.report['outcome'],'failed')

    def test_error_codes_suppress_unsafe_text_in_artifact_and_cli(self):
        secret='token=super-secret /private/customer/path raw-transcript'
        cases=[gate.QualificationError(secret),gate.QualificationError('invalid_stop_timestamp '+secret),
               RuntimeError(secret),AssertionError(secret),ValueError(secret),
               subprocess.TimeoutExpired(secret,3,output=secret,stderr=secret)]
        self.assertEqual(gate.safe_error(gate.QualificationError('invalid_stop_timestamp'))['error_code'],
                         'invalid_stop_timestamp')
        for exception in cases:
            self.qual.report['checkpoints']=[]
            with self.assertRaises(type(exception)):
                with self.qual.checkpoint('stop_menu'):raise exception
            diagnostic=gate.safe_error(exception)
            self.qual.report.update(outcome='failed',**diagnostic);self.qual.finalize_report()
            self.assertNotIn(secret,self.qual.output.read_text())
            self.assertNotIn('/private/',json.dumps(self.qual.report))
            output=io.StringIO()
            with patch.object(gate,'run_qualification',side_effect=exception),patch.object(gate.sys,'stderr',output):
                self.assertEqual(gate.main(['--kit',str(self.base),'--sha256','b'*64,
                    '--source-commit',COMMIT,'--evidence-dir',str(self.base/'evidence')]),1)
            self.assertNotIn(secret,output.getvalue())
            self.assertIn(diagnostic['error_code'],output.getvalue())

    def test_clean_direct_cli_default_refuses_without_creating_evidence(self):
        output=self.base/'not-created'
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(self.base)}
        command=[sys.executable,'-B',str(Path(gate.__file__).resolve()),'--kit',str(self.base),'--sha256','b'*64,
                 '--source-commit',COMMIT,'--evidence-dir',str(output)]
        result=subprocess.run(command,env=env,cwd=self.base,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertIn('QualificationError',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertFalse(output.exists())


@unittest.skipUnless(os.environ.get('VUI_MANAGED_SYSTEM_ACCEPTANCE','').startswith(gate.AUTH_PREFIX),
                     'real privileged qualification requires separate explicit approval and exact-head opt-in')
class ExplicitManagedSystemQualification(unittest.TestCase):
    def test_approved_exact_head_disposable_runner(self):
        commit=os.environ['VUI_MANAGED_SYSTEM_ACCEPTANCE'][len(gate.AUTH_PREFIX):]
        args=argparse.Namespace(source_commit=commit,kit=Path(os.environ['VUI_MANAGED_KIT']),
            sha256=os.environ['VUI_MANAGED_KIT_SHA256'],evidence_dir=Path(os.environ['VUI_MANAGED_EVIDENCE']))
        self.assertEqual(gate.run_qualification(args)['outcome'],'passed')


if __name__=='__main__':unittest.main()

"""Release checks bind all evidence and target assets to one exact source commit."""
import hashlib
import io
import json
from pathlib import Path
import stat
import shutil
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_release_kit, install_ops_control
from scripts.publish_release import (
    ASSETS,
    COMMON_ASSETS,
    KIT_ASSETS,
    REQUIRED,
    REQUIRED_ACCEPTANCE_STEPS,
    TARGETS,
    ReleaseGateError,
    finalize_assets,
    installation_command,
    select_runs,
    validate_run_jobs,
    validate_installer_only_scope,
    INSTALLER_ONLY_BASELINE,
    validate_kit,
    validate_target_artifact,
)

SHA='a'*40


class InstallerOnlyReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tscripts/install_system.py\n',
              mode='100644', application_extra=b''):
        old=b"app = FastAPI(version='0.4.9')\n"
        def git(args,**kwargs):
            if args[1:3]==['diff','--name-status']:return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                if args[2]==INSTALLER_ONLY_BASELINE+':main.py':return old
                if args[2]==SHA+':main.py':return old.replace(b'0.4.9',b'0.4.10')+application_extra
                if args[2]==SHA+':VERSION':return b'0.4.10\n'
            raise AssertionError(args)
        with patch('scripts.publish_release.subprocess.run') as ancestry, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_installer_only_scope(SHA,'0.4.10')
            self.assertEqual(ancestry.call_args.args[0],['git','merge-base','--is-ancestor',INSTALLER_ONLY_BASELINE,SHA])

    def test_regular_installer_source_and_version_only_app_change_allowed(self):
        self.scope()

    def test_exception_cannot_be_reused_for_another_release(self):
        for version in ('0.4.9','0.4.11','0.5.0'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_installer_only_scope(SHA,version)
            git.assert_not_called()

    def test_web_app_dependency_and_core_changes_still_require_full_acceptance(self):
        for path in ('web/js/app.js','app/api/auth.py','requirements-runtime.txt','scripts/fetch_test_cores.py','deploy/system_ops.py'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_additional_application_logic_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'Application changes exceed'):
            self.scope(application_extra=b'new_application_logic()\n')

    def test_deleted_renamed_or_linked_files_are_rejected(self):
        for change,mode in (('D\tsetup.sh\n','100644'),('R100\tsetup.sh\tother.sh\n','100644'),('M\tsetup.sh\n','120000')):
            with self.subTest(change=change,mode=mode),self.assertRaises(ReleaseGateError):self.scope(changes=change,mode=mode)

    def test_browser_exception_never_waives_other_skips_failure_or_missing_steps(self):
        run,base=ExactJobQualificationTests().fixtures()
        for state in ('only_browser_skipped','nonbrowser_skipped','browser_failed','browser_missing'):
            job={**base,'steps':[dict(s,conclusion='skipped') for s in base['steps']]}
            if state=='nonbrowser_skipped':job['steps'].append({'name':'Run tests','status':'completed','conclusion':'skipped'})
            if state=='browser_failed':job['steps'][0]['conclusion']='failure'
            if state=='browser_missing':job['steps'].pop()
            with self.subTest(state=state),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                if state=='only_browser_skipped':validate_run_jobs({'test.yml':run},SHA,installer_only_no_browser=True)
                else:
                    with self.assertRaises(ReleaseGateError):validate_run_jobs({'test.yml':run},SHA,installer_only_no_browser=True)
        with patch('scripts.publish_release.request',return_value={'jobs':[job]}),self.assertRaises(ReleaseGateError):
            validate_run_jobs({'test.yml':run},SHA)


class ExactJobQualificationTests(unittest.TestCase):
    def fixtures(self):
        run={'id':42,'run_attempt':2,'head_sha':SHA,'status':'completed','conclusion':'success'}
        job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
             'steps':[{'name':name,'status':'completed','conclusion':'success'}
                      for name in sorted(REQUIRED_ACCEPTANCE_STEPS['test.yml'])]}
        return run,job

    def verify(self,run,job,current=None):
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},current or run]) as request:
            validate_run_jobs({'test.yml':run},SHA)
        self.assertIn('/attempts/2/jobs?',request.call_args_list[0].args[0])

    def test_exact_latest_attempt_success_is_accepted(self):
        self.verify(*self.fixtures())

    def test_green_workflow_cannot_hide_skipped_missing_or_failed_steps(self):
        for state in ('skipped','failure','cancelled','in_progress','missing','missing_one'):
            run,job=self.fixtures()
            if state=='missing':job['steps']=[]
            elif state=='missing_one':job['steps'].pop()
            elif state=='in_progress':job['steps'][0]['status']=state
            else:job['steps'][0]['conclusion']=state
            with self.subTest(state=state),self.assertRaises(ReleaseGateError):self.verify(run,job)

    def test_foreign_revision_or_job_run_is_rejected(self):
        for key,value in (('head_sha','b'*40),('run_id',43),('conclusion','skipped')):
            run,job=self.fixtures();job[key]=value
            with self.subTest(key=key),self.assertRaises(ReleaseGateError):self.verify(run,job)

    def test_new_attempt_started_during_read_is_rejected(self):
        run,job=self.fixtures()
        with self.assertRaisesRegex(ReleaseGateError,'changed during'):
            self.verify(run,job,{**run,'run_attempt':3,'status':'in_progress','conclusion':None})

    def test_missing_jobs_and_duplicate_jobs_are_rejected(self):
        run,job=self.fixtures()
        for jobs in ([],[job,job]):
            with self.subTest(count=len(jobs)),patch('scripts.publish_release.request',return_value={'jobs':jobs}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA)


def bundle(target,commit=SHA,version='0.3.1'):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('MANIFEST.json',json.dumps({
            'schema':1,
            'kind':'release',
            'source_commit':commit,
            'version':version,
            'platform':'linux-multi-cpython312',
            'targets':[target],
        }))
    return raw.getvalue()


class ReleasePromotionTests(unittest.TestCase):
    def test_one_line_command_pins_bootstrap_and_kit_before_managed_installation(self):
        files={'install.sh':b'accepted installer','SHA256SUMS':b'accepted complete checksums'}
        command=installation_command(files,'0.4.9')
        self.assertEqual(len(command.splitlines()),1)
        arguments=shlex.split(command)
        self.assertEqual(arguments[:2],['/bin/sh','-c'])
        script=arguments[-1]
        self.assertIn('https://github.com/ForceMind/V-UI/releases/download/v0.4.9/install.sh',script)
        self.assertIn(hashlib.sha256(files['install.sh']).hexdigest(),script)
        self.assertIn(hashlib.sha256(files['SHA256SUMS']).hexdigest(),script)
        self.assertLess(script.index('sha256sum -c -'),script.index('exec /bin/bash'))
        self.assertIn('--with-global-cli',script)
        self.assertIn('--interactive',script)
        self.assertIn('mktemp -d /root/vui-bootstrap.',script)
        for fragment in (arguments[2],script):
            subprocess.run(['/bin/sh','-n','-c',fragment],check=True)

    def test_one_line_command_quotes_existing_optional_arguments_and_rejects_bad_version(self):
        files={'install.sh':b'installer','SHA256SUMS':b'checksums'}
        value='/tmp/certificate with spaces;$(false).pem'
        command=installation_command(files,'0.4.9',installer_args=('--cert',value))
        body=shlex.split(command)[-1]
        final=shlex.split(body.rsplit('exec /bin/bash ',1)[1])
        self.assertEqual(final[-2:],['--cert',value])
        for version in ('latest','0.4.9;false','v0.4.9','0.4.9-rc.1'):
            with self.subTest(version=version),self.assertRaises(ReleaseGateError):
                installation_command(files,version)
        with self.assertRaises(ReleaseGateError):
            installation_command(files,'0.4.9',installer_args=('--dry-run',))

    def runs(self):
        return [
            {'id':i,'path':'.github/workflows/'+path,'head_sha':SHA,
             'status':'completed','conclusion':'success'}
            for i,path in enumerate(sorted(REQUIRED),1)
        ]

    def test_requires_all_latest_exact_commit_checks(self):
        runs=self.runs()
        self.assertEqual(set(select_runs(runs,SHA)),REQUIRED)
        for mutation in ('missing','wrong_sha','failure','queued','newer_failed'):
            value=self.runs()
            if mutation=='missing':value.pop()
            if mutation=='wrong_sha':value[0]['head_sha']='b'*40
            if mutation=='failure':value[0]['conclusion']='failure'
            if mutation=='queued':value[0]['status']='queued'
            if mutation=='newer_failed':
                value.append({**value[0],'id':999,'conclusion':'failure'})
            with self.subTest(mutation=mutation),self.assertRaises(ReleaseGateError):
                select_runs(value,SHA)

    def kit(self,commit=SHA,version='0.3.1',corrupt=False):
        files={name:b'fixture' for name in KIT_ASSETS-{'SHA256SUMS'}}
        files['vui-linux-x86_64-gnu.zip']=bundle('x86_64-gnu',commit,version)
        files['RELEASE.json']=json.dumps({
            'source_commit':commit,'version':version,'targets':['x86_64-gnu']
        }).encode()
        files['SHA256SUMS']=(''.join(
            hashlib.sha256(data).hexdigest()+'  '+name+'\n'
            for name,data in sorted(files.items())
        )).encode()
        if corrupt:
            files['install.sh']=b'modified'
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in files.items():
                z.writestr(name,data)
        return archive.getvalue()

    def portable(self,target,commit=SHA,version='0.3.1',corrupt=False):
        name='vui-linux-'+target+'.zip'
        data=bundle(target,commit,version)
        checksum=hashlib.sha256(data).hexdigest()
        if corrupt:
            checksum='0'*64
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr(name,data)
            z.writestr(name+'.sha256',checksum+'  '+name+'\n')
        return archive.getvalue()

    def test_kit_integrity_commit_version_and_target(self):
        files=validate_kit(self.kit(),SHA,'0.3.1')
        self.assertEqual(set(files),KIT_ASSETS)
        for raw in (
            self.kit(commit='b'*40),
            self.kit(version='0.3.2'),
            self.kit(corrupt=True),
        ):
            with self.assertRaises(ReleaseGateError):
                validate_kit(raw,SHA,'0.3.1')

    def test_each_portable_target_is_bound_to_commit_version_and_checksum(self):
        for target in TARGETS[1:]:
            with self.subTest(target=target):
                raw=self.portable(target)
                returned=validate_target_artifact(raw,SHA,'0.3.1',target)
                with zipfile.ZipFile(io.BytesIO(returned)) as archive:
                    manifest=json.loads(archive.read('MANIFEST.json'))
                self.assertEqual(manifest['targets'],[target])
                self.assertEqual(manifest['source_commit'],SHA)
                self.assertEqual(manifest['version'],'0.3.1')
                with self.assertRaises(ReleaseGateError):
                    validate_target_artifact(self.portable(target,corrupt=True),SHA,'0.3.1',target)
                with self.assertRaises(ReleaseGateError):
                    validate_target_artifact(self.portable(target,commit='b'*40),SHA,'0.3.1',target)

    def test_final_assets_recompute_global_checksums_for_all_targets(self):
        files=validate_kit(self.kit(),SHA,'0.3.1')
        for target in TARGETS[1:]:
            files['vui-linux-'+target+'.zip']=bundle(target)
        final=finalize_assets(files,SHA,'0.3.1')
        self.assertEqual(set(final),ASSETS)
        checks={}
        for line in final['SHA256SUMS'].decode().splitlines():
            digest,name=line.split('  ',1)
            checks[name]=digest
        self.assertEqual(set(checks),ASSETS-{'SHA256SUMS'})
        for name,digest in checks.items():
            self.assertEqual(hashlib.sha256(final[name]).hexdigest(),digest)
        record=json.loads(final['RELEASE.json'])
        self.assertEqual(record['targets'],list(TARGETS))

    def test_control_distribution_sets_are_closed_and_consistent(self):
        source_names={name for _,name in build_release_kit.SOURCE_ASSETS}
        self.assertEqual(COMMON_ASSETS,install_ops_control.COMMON_ASSETS)
        self.assertEqual(source_names|{'vui-source.zip','RELEASE.json','RELEASE_NOTES.md'},COMMON_ASSETS)
        self.assertEqual({name: source for source,name in build_release_kit.SOURCE_ASSETS}
                         | {'RELEASE_NOTES.md':'docs/RELEASE_NOTES.md'},install_ops_control.SOURCE_MEMBERS)
        self.assertTrue({'install_ops_control.py','system_launcher.py','system_ops.py',
                         'ops_worker.py','release_tools.py','v-ui'} <= source_names)

    def test_actual_builder_packages_control_sources_and_installer_verifies_full_kit(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            source=work/'bundle.zip'
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as archive:
                archive.writestr('MANIFEST.json',json.dumps({
                    'schema':1,'kind':'release','source_commit':SHA,'version':'0.3.1',
                    'release_id':'fixture','platform':'linux-multi-cpython312','targets':['x86_64-gnu']}))
            source.write_bytes(raw.getvalue())
            destination=work/'kit'
            def archive_source(command, **kwargs):
                self.assertEqual(command[:3],['git','archive','--format=zip'])
                output=Path(next(value[9:] for value in command if value.startswith('--output=')))
                with zipfile.ZipFile(output,'w') as archive:
                    archive.comment=SHA.encode()
                    archive.writestr('VERSION','0.3.1')
                    for source,_ in (*build_release_kit.SOURCE_ASSETS,('docs/RELEASE_NOTES.md','RELEASE_NOTES.md')):
                        archive.writestr(source,(root/source).read_bytes())
                    for member in install_ops_control.SOURCE_DEPENDENCIES.values():
                        archive.writestr(member, (root/member).read_bytes())
            with patch.object(build_release_kit.subprocess,'run',side_effect=archive_source):
                build_release_kit.build(source,destination,SHA)
            self.assertEqual({path.name for path in destination.iterdir()},KIT_ASSETS)
            for path,name in build_release_kit.SOURCE_ASSETS:
                self.assertEqual((destination/name).read_bytes(),(root/path).read_bytes())
            checksum=hashlib.sha256((destination/'SHA256SUMS').read_bytes()).hexdigest()
            code,record=install_ops_control.verify_kit(destination,checksum)
            self.assertEqual(record['source_commit'],SHA)
            self.assertIn(b'def operate(',code['system_ops.py'])
            self.assertIn(b'class _MaintenanceSession(',code['release_tools.py'])
            self.assertIn(b'/usr/bin/python3 -B -I -S',code['v-ui'])
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as archive:
                for path in destination.iterdir():archive.writestr(path.name,path.read_bytes())
            self.assertEqual(set(validate_kit(raw.getvalue(),SHA,'0.3.1')),KIT_ASSETS)
            with self.assertRaisesRegex(ValueError,'empty'):
                build_release_kit.build(source,destination,SHA)

    def test_missing_extra_or_linked_control_asset_fails_promotion(self):
        with zipfile.ZipFile(io.BytesIO(self.kit())) as archive:
            files={name:archive.read(name) for name in archive.namelist()}
        for change in ('missing','extra','duplicate','symlink','corrupt'):
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as archive:
                for name,content in files.items():
                    if name=='ops_worker.py' and change=='missing':continue
                    if name=='ops_worker.py' and change=='symlink':
                        link=zipfile.ZipInfo(name)
                        link.create_system=3
                        link.external_attr=(stat.S_IFLNK|0o777)<<16
                        archive.writestr(link,content)
                    elif name=='ops_worker.py' and change=='corrupt':archive.writestr(name,b'modified')
                    else:archive.writestr(name,content)
                if change=='extra':archive.writestr('unrelated.py',b'extra')
                if change=='duplicate':
                    with self.assertWarns(UserWarning):archive.writestr('ops_worker.py',files['ops_worker.py'])
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):
                validate_kit(output.getvalue(),SHA,'0.3.1')

    def test_builder_never_mixes_dirty_working_tree_with_exact_commit_bundle(self):
        root=Path(__file__).resolve().parents[1]
        snapshots={source:(root/source).read_bytes() for source,_ in
                   (*build_release_kit.SOURCE_ASSETS,('docs/RELEASE_NOTES.md','RELEASE_NOTES.md'))}
        copy=shutil.copyfile
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            source=work/'bundle.zip'
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('MANIFEST.json',json.dumps({
                    'source_commit':SHA,'version':'0.3.1','release_id':'fixture',
                    'platform':'linux-multi-cpython312','targets':['x86_64-gnu']}))
            def dirty_copy(input_path, output_path):
                if Path(input_path) in {root/name for name in snapshots}:
                    Path(output_path).write_bytes(b'dirty uncommitted root code')
                    return output_path
                return copy(input_path,output_path)
            def exact_archive(command, **kwargs):
                output=Path(next(value[9:] for value in command if value.startswith('--output=')))
                with zipfile.ZipFile(output,'w') as archive:
                    archive.comment=SHA.encode()
                    for name,content in snapshots.items():archive.writestr(name,content)
            with patch.object(build_release_kit.shutil,'copyfile',side_effect=dirty_copy), \
                    patch.object(build_release_kit.subprocess,'run',side_effect=exact_archive):
                build_release_kit.build(source,work/'kit',SHA)
            for path,name in (*build_release_kit.SOURCE_ASSETS,('docs/RELEASE_NOTES.md','RELEASE_NOTES.md')):
                self.assertEqual((work/'kit'/name).read_bytes(),snapshots[path])

            def wrong_commit_archive(command, **kwargs):
                exact_archive(command,**kwargs)
                output=Path(next(value[9:] for value in command if value.startswith('--output=')))
                with zipfile.ZipFile(output,'a') as archive:archive.comment=b'b'*40
            with patch.object(build_release_kit.subprocess,'run',side_effect=wrong_commit_archive):
                with self.assertRaisesRegex(ValueError,'exact commit'):
                    build_release_kit.build(source,work/'wrong-source',SHA)
            self.assertFalse((work/'wrong-source/SHA256SUMS').exists())

            def old_commit_archive(command, **kwargs):
                output=Path(next(value[9:] for value in command if value.startswith('--output=')))
                with zipfile.ZipFile(output,'w') as archive:
                    archive.comment=SHA.encode()
                    for name,content in snapshots.items():
                        if name!='scripts/install_ops_control.py':archive.writestr(name,content)
            with patch.object(build_release_kit.subprocess,'run',side_effect=old_commit_archive):
                with self.assertRaises(KeyError):
                    build_release_kit.build(source,work/'old-source',SHA)
            self.assertFalse((work/'old-source/SHA256SUMS').exists())


if __name__=='__main__':
    unittest.main()

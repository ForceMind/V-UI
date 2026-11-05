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
    validate_ca_trust_scope,
    CA_TRUST_BASELINE,
    SIMPLE_NODE_CI_FIX_HASHES,
    SIMPLE_NODE_BASELINE,
    SIMPLE_NODE_FILES,
    SIMPLE_NODE_UPGRADE_STEP,
    SIMPLE_NODE_TEMPLATE_STEP,
    SIMPLE_NODE_LOOPBACK_STEPS,
    validate_simple_node_scope,
    REMOTE_MAINTENANCE_BASELINE,
    REMOTE_MAINTENANCE_FILES,
    REMOTE_MAINTENANCE_UPGRADE_STEP,
    REMOTE_MAINTENANCE_LOOPBACK_STEPS,
    validate_remote_maintenance_scope,
    TLS_CERTIFICATE_BASELINE,
    TLS_CERTIFICATE_FILES,
    TLS_CERTIFICATE_UPGRADE_STEP,
    TLS_CERTIFICATE_CORE_STEP,
    validate_tls_certificate_scope,
    UI_REDESIGN_BASELINE,
    UI_REDESIGN_FILES,
    UI_REDESIGN_UPGRADE_STEP,
    BROWSER_SKIP_STEPS,
    validate_ui_redesign_scope,
    STARTUP_BASELINE,
    validate_startup_scope,
    validate_install_interaction_scope,
    INSTALL_INTERACTION_BASELINE,
    validate_kit,
    validate_target_artifact,
)

SHA='a'*40


class InstallerOnlyReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tscripts/install_system.py\n',
              mode='100644', application_extra=b'', builder_extra=b''):
        old=b"app = FastAPI(version='0.4.10')\n"
        builder=b"allowed_files={'scripts/deploy.py','scripts/install_system.py'}\n"
        def git(args,**kwargs):
            if args[1:3]==['diff','--name-status']:return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                if args[2]==INSTALLER_ONLY_BASELINE+':main.py':return old
                if args[2]==SHA+':main.py':return old.replace(b'0.4.10',b'0.4.11')+application_extra
                if args[2]==SHA+':VERSION':return b'0.4.11\n'
                if args[2]==INSTALLER_ONLY_BASELINE+':scripts/build_bundle.py':return builder
                if args[2]==SHA+':scripts/build_bundle.py':return builder.replace(
                    b"'scripts/install_system.py'}",b"'scripts/install_system.py',\n            'scripts/installer_certificate_state.py'}")+builder_extra
            raise AssertionError(args)
        with patch('scripts.publish_release.subprocess.run') as ancestry, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_installer_only_scope(SHA,'0.4.11')
            self.assertEqual(ancestry.call_args.args[0],['git','merge-base','--is-ancestor',INSTALLER_ONLY_BASELINE,SHA])

    def test_regular_installer_source_and_version_only_app_change_allowed(self):
        self.scope()

    def test_exception_cannot_be_reused_for_another_release(self):
        for version in ('0.4.9','0.4.10','0.4.12','0.5.0'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_installer_only_scope(SHA,version)
            git.assert_not_called()

    def test_web_app_dependency_and_core_changes_still_require_full_acceptance(self):
        for path in ('web/js/app.js','app/api/auth.py','requirements-runtime.txt','scripts/fetch_test_cores.py','deploy/system_ops.py'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_additional_application_logic_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'Application changes exceed'):
            self.scope(application_extra=b'new_application_logic()\n')

    def test_builder_cannot_rewrite_payload_or_core_selection(self):
        self.scope(changes='M\tscripts/build_bundle.py\nA\tscripts/installer_certificate_state.py\n')
        for extra in (b'fetch_cores(payload, "other-core")\n', b'payload.write_text("modified app")\n'):
            with self.subTest(extra=extra),self.assertRaisesRegex(ReleaseGateError,'Builder changes exceed'):
                self.scope(builder_extra=extra)

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


class CATrustReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\nM\tapp/certificates/material.py\n',
              mode='100644', application_extra=b'', installer_extra=b'', builder_extra=b''):
        app=b"app = FastAPI(version='0.4.11')\n"
        installer=b'# install.sh --version v0.4.11\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                ref=args[2]
                if ref==CA_TRUST_BASELINE+':main.py':return app
                if ref==SHA+':main.py':return app.replace(b'0.4.11',b'0.4.12')+application_extra
                if ref==SHA+':VERSION':return b'0.4.12\n'
                if ref==CA_TRUST_BASELINE+':install.sh':return installer
                if ref==SHA+':install.sh':return installer.replace(b'0.4.11',b'0.4.12')+installer_extra
                if ref==CA_TRUST_BASELINE+':scripts/build_bundle.py':return b'unchanged builder\n'
                if ref==SHA+':scripts/build_bundle.py':return b'unchanged builder\n'+builder_extra
            raise AssertionError(args)
        with patch('scripts.publish_release.subprocess.run') as ancestry, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_ca_trust_scope(SHA,'0.4.12')
            self.assertEqual(ancestry.call_args.args[0],['git','merge-base','--is-ancestor',CA_TRUST_BASELINE,SHA])

    def test_only_current_approved_ca_patch_allowed(self):self.scope()

    def test_exception_is_not_reusable_for_old_or_future_versions(self):
        for version in ('0.4.10','0.4.11','0.4.13','0.5.0'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_ca_trust_scope(SHA,version)
            git.assert_not_called()

    def test_other_app_web_runtime_and_deployment_changes_rejected(self):
        for path in ('app/certificates/provider.py','app/api/auth.py','web/js/certificates.js',
                     'requirements-runtime.txt','deploy/system_ops.py','scripts/build_bundle.py',
                     '.github/workflows/test.yml','tests/unrelated_new_acceptance.py'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_renames_deletions_and_symlinks_rejected(self):
        for change,mode in (('D\tsetup.sh\n','100644'),('R100\tmain.py\tother.py\n','100644'),
                            ('M\tapp/certificates/material.py\n','120000')):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change,mode=mode)

    def test_main_installer_and_builder_cannot_hide_logic_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'builder_extra':b'rewrite_payload()\n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_ca_exception_accepts_only_explicit_browser_skips(self):
        run,base=ExactJobQualificationTests().fixtures()
        job={**base,'steps':[dict(s,conclusion='skipped') for s in base['steps']]}
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
            validate_run_jobs({'test.yml':run},SHA,ca_trust_no_browser=True)
        for conclusion in ('skipped','failure','cancelled'):
            bad={**job,'steps':job['steps']+[{'name':'Run tests','status':'completed','conclusion':conclusion}]}
            with self.subTest(conclusion=conclusion),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA,ca_trust_no_browser=True)


class InstallInteractionReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\nM\tapp/admin.py\n',
              mode='100644', application_extra=b'', installer_extra=b'', builder_extra=b'', controller_extra=b''):
        app=b"app = FastAPI(version='0.4.12')\n"
        installer=b'# install.sh --version v0.4.12\nactual_installer()\n'
        controller=b"print('Activated verified release; start explicitly after checking configuration')\nprint('Backup SHA-256: ')\n"
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                ref=args[2]
                if ref==INSTALL_INTERACTION_BASELINE+':main.py':return app
                if ref==SHA+':main.py':return app.replace(b'0.4.12',b'0.4.13')+application_extra
                if ref==SHA+':VERSION':return b'0.4.13\n'
                if ref==INSTALL_INTERACTION_BASELINE+':install.sh':return installer
                if ref==SHA+':install.sh':return installer.replace(b'0.4.12',b'0.4.13')+installer_extra
                if ref==INSTALL_INTERACTION_BASELINE+':scripts/build_bundle.py':return b'unchanged builder\n'
                if ref==SHA+':scripts/build_bundle.py':return b'unchanged builder\n'+builder_extra
                if ref==INSTALL_INTERACTION_BASELINE+':scripts/deploy.py':return controller
                if ref==SHA+':scripts/deploy.py':return controller.replace(b'Activated verified release; start explicitly after checking configuration','已激活通过校验的版本；核对配置后再启动'.encode()).replace(b'Backup SHA-256: ','备份 SHA-256：'.encode())+controller_extra
            raise AssertionError(args)
        with patch('scripts.publish_release.subprocess.run') as ancestry, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_install_interaction_scope(SHA,'0.4.13')
            self.assertEqual(ancestry.call_args.args[0],['git','merge-base','--is-ancestor',INSTALL_INTERACTION_BASELINE,SHA])

    def test_only_current_approved_interaction_patch_allowed(self):self.scope()

    def test_exception_is_not_reusable_for_old_or_future_versions(self):
        for version in ('0.4.10','0.4.12','0.4.14','0.5.0'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_install_interaction_scope(SHA,version)
            git.assert_not_called()

    def test_other_app_web_runtime_and_deployment_changes_rejected(self):
        for path in ('app/certificates/material.py','app/services/auth_service.py','app/api/auth.py','web/js/certificates.js',
                     'requirements-runtime.txt','deploy/system_ops.py','scripts/build_bundle.py',
                     '.github/workflows/test.yml','tests/unrelated_new_acceptance.py'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_renames_deletions_and_symlinks_rejected(self):
        for change,mode in (('D\tsetup.sh\n','100644'),('R100\tmain.py\tother.py\n','100644'),
                            ('M\tapp/admin.py\n','120000')):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change,mode=mode)

    def test_main_installer_and_builder_cannot_hide_logic_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'builder_extra':b'rewrite_payload()\n'}, {'controller_extra':b'new_logic()\n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_interaction_exception_accepts_only_explicit_browser_skips(self):
        run,base=ExactJobQualificationTests().fixtures()
        job={**base,'steps':[dict(s,conclusion='skipped') for s in base['steps']]}
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
            validate_run_jobs({'test.yml':run},SHA,install_interaction_no_browser=True)
        for conclusion in ('skipped','failure','cancelled'):
            bad={**job,'steps':job['steps']+[{'name':'Run tests','status':'completed','conclusion':conclusion}]}
            with self.subTest(conclusion=conclusion),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA,install_interaction_no_browser=True)


class StartupReadinessReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\nM\tscripts/install_system.py\n',
              mode='100644', application_extra=b'', installer_extra=b'', builder_extra=b''):
        app=b"app = FastAPI(version='0.4.13')\n"
        installer=b'# install.sh --version v0.4.13\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                ref=args[2]
                if ref==STARTUP_BASELINE+':main.py':return app
                if ref==SHA+':main.py':return app.replace(b'0.4.13',b'0.4.14')+application_extra
                if ref==SHA+':VERSION':return b'0.4.14\n'
                if ref==STARTUP_BASELINE+':install.sh':return installer
                if ref==SHA+':install.sh':return installer.replace(b'0.4.13',b'0.4.14')+installer_extra
                if ref==STARTUP_BASELINE+':scripts/build_bundle.py':return b'unchanged builder\n'
                if ref==SHA+':scripts/build_bundle.py':return b'unchanged builder\n'+builder_extra
            raise AssertionError(args)
        with patch('scripts.publish_release.subprocess.run') as ancestry, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_startup_scope(SHA,'0.4.14')
            self.assertEqual(ancestry.call_args.args[0],['git','merge-base','--is-ancestor',STARTUP_BASELINE,SHA])

    def test_only_current_approved_startup_patch_allowed(self):self.scope()

    def test_exception_is_not_reusable_for_old_or_future_versions(self):
        for version in ('0.4.10','0.4.13','0.4.15','0.5.0'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_startup_scope(SHA,version)
            git.assert_not_called()

    def test_other_app_web_runtime_and_deployment_changes_rejected(self):
        for path in ('app/admin.py','app/certificates/material.py','app/certificates/provider.py','app/api/auth.py','web/js/certificates.js',
                     'requirements-runtime.txt','deploy/system_ops.py','scripts/build_bundle.py',
                     '.github/workflows/test.yml','tests/unrelated_new_acceptance.py'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_renames_deletions_and_symlinks_rejected(self):
        for change,mode in (('D\tsetup.sh\n','100644'),('R100\tmain.py\tother.py\n','100644'),
                            ('M\tscripts/install_system.py\n','120000')):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change,mode=mode)

    def test_main_installer_and_builder_cannot_hide_logic_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'builder_extra':b'rewrite_payload()\n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_startup_exception_accepts_only_explicit_browser_skips(self):
        run,base=ExactJobQualificationTests().fixtures()
        job={**base,'steps':[dict(s,conclusion='skipped') for s in base['steps']]}
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
            validate_run_jobs({'test.yml':run},SHA,startup_no_browser=True)
        for conclusion in ('skipped','failure','cancelled'):
            bad={**job,'steps':job['steps']+[{'name':'Run tests','status':'completed','conclusion':conclusion}]}
            with self.subTest(conclusion=conclusion),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA,startup_no_browser=True)


class UIRedesignReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\n', mode=None,
              application_extra=b'', installer_extra=b'', version=b'0.4.15\n', ancestry=True):
        app=b"app = FastAPI(version='0.4.14')\n"
        installer=b'# install.sh --version v0.4.14\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':
                file_mode=mode or ('100755' if args[-1]=='install.sh' else '100644')
                return file_mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                return {
                    UI_REDESIGN_BASELINE+':main.py': app,
                    SHA+':main.py': app.replace(b'0.4.14',b'0.4.15')+application_extra,
                    SHA+':VERSION': version,
                    UI_REDESIGN_BASELINE+':install.sh': installer,
                    SHA+':install.sh': installer.replace(b'0.4.14',b'0.4.15')+installer_extra,
                }[args[2]]
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_ui_redesign_scope(SHA,'0.4.15')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',UI_REDESIGN_BASELINE,SHA])

    def test_approved_version_and_exact_release_paths_allowed(self):
        self.scope(changes=''.join('M\t'+path+'\n' for path in sorted(UI_REDESIGN_FILES)))
        self.scope(changes='A\ttests/published_upgrade_helpers.py\n')

    def test_exception_rejects_old_future_or_nonstable_versions_before_git(self):
        for version in ('0.4.14','0.4.16','0.5.0','0.4.15-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git, self.assertRaises(ReleaseGateError):
                validate_ui_redesign_scope(SHA,version)
            git.assert_not_called()

    def test_missing_approved_ancestor_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)

    def test_other_production_and_unlisted_test_or_doc_paths_rejected(self):
        for path in ('app/api/auth.py','app/admin.py','web/index.html','web/css/style.css',
                     'requirements-runtime.txt','scripts/build_bundle.py','scripts/install_system.py',
                     'setup.sh','.github/workflows/short-install.yml','.github/workflows/test.yml',
                     'tests/unrelated.py','docs/unrelated.md'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_rename_delete_type_change_and_nonregular_or_executable_modes_rejected(self):
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n',
                       'T\tREADME.md\n','C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_installer_executable_bit_cannot_be_removed(self):
        with self.assertRaises(ReleaseGateError):self.scope(changes='M\tinstall.sh\n',mode='100644')

    def test_application_installer_and_version_cannot_hide_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'version':b'0.4.14\n'}, {'version':b'0.4.15 \n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_only_twelve_explicit_browser_skips_are_accepted(self):
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        for path, names in BROWSER_SKIP_STEPS.items():
            run={'id':42,'run_attempt':1,'head_sha':SHA,'status':'completed','conclusion':'success'}
            job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
                 'steps':[{'name':name,'status':'completed','conclusion':'skipped'} for name in names]}
            with self.subTest(path=path),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                validate_run_jobs({path:run},SHA,ui_redesign_no_browser=True)
            for invalid in ('missing','failure','cancelled','in_progress'):
                bad={**job,'steps':[dict(step) for step in job['steps']]}
                if invalid=='missing':bad['steps'].pop()
                elif invalid=='in_progress':bad['steps'][0]['status']=invalid
                else:bad['steps'][0]['conclusion']=invalid
                with self.subTest(path=path,invalid=invalid),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                    validate_run_jobs({path:run},SHA,ui_redesign_no_browser=True)

    def test_ui_exception_rejects_every_other_skipped_step(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps']=[{'name':name,'status':'completed','conclusion':'skipped'} for name in BROWSER_SKIP_STEPS['test.yml']]
        job['steps'].append({'name':'Run tests','status':'completed','conclusion':'skipped'})
        with patch('scripts.publish_release.request',return_value={'jobs':[job]}),self.assertRaises(ReleaseGateError):
            validate_run_jobs({'test.yml':run},SHA,ui_redesign_no_browser=True)

    def test_real_published_upgrade_step_is_mandatory_and_must_succeed(self):
        run,job=ExactJobQualificationTests().fixtures()
        for state in ('success','missing','skipped','failure','cancelled','in_progress'):
            steps=[{'name':UI_REDESIGN_UPGRADE_STEP,'status':'completed','conclusion':'success'}]
            if state=='missing':steps=[{'name':'Unrelated test','status':'completed','conclusion':'success'}]
            elif state=='in_progress':steps[0]['status']=state
            else:steps[0]['conclusion']=state
            with self.subTest(state=state),patch('scripts.publish_release.request',side_effect=[{'jobs':[{**job,'steps':steps}]},run]):
                if state=='success':validate_run_jobs({'oneclick.yml':run},SHA,ui_redesign_no_browser=True)
                else:
                    with self.assertRaises(ReleaseGateError):validate_run_jobs({'oneclick.yml':run},SHA,ui_redesign_no_browser=True)
        # Old release scope does not acquire a retroactive step requirement.
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
            validate_run_jobs({'oneclick.yml':run},SHA,startup_no_browser=True)

    def test_ui_exception_cannot_mix_with_any_previous_exception(self):
        for name in ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser','startup_no_browser'):
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,ui_redesign_no_browser=True,**{name:True})
            request.assert_not_called()


class TLSCertificateReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\n', mode=None,
              application_extra=b'', installer_extra=b'', version=b'0.4.16\n', ancestry=True):
        app=b"app = FastAPI(version='0.4.15')\n"
        installer=b'# install.sh --version v0.4.15\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':
                file_mode=mode or ('100755' if args[-1]=='install.sh' else '100644')
                return file_mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                return {
                    TLS_CERTIFICATE_BASELINE+':main.py': app,
                    SHA+':main.py': app.replace(b'0.4.15',b'0.4.16')+application_extra,
                    SHA+':VERSION': version,
                    TLS_CERTIFICATE_BASELINE+':install.sh': installer,
                    SHA+':install.sh': installer.replace(b'0.4.15',b'0.4.16')+installer_extra,
                }[args[2]]
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_tls_certificate_scope(SHA,'0.4.16')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',TLS_CERTIFICATE_BASELINE,SHA])

    def test_approved_version_and_exact_release_paths_allowed(self):
        self.scope(changes=''.join('M\t'+path+'\n' for path in sorted(TLS_CERTIFICATE_FILES)))
        self.scope(changes='A\ttests/published_upgrade_helpers.py\n')

    def test_exception_rejects_old_future_or_nonstable_versions_before_git(self):
        for version in ('0.4.15','0.4.17','0.5.0','0.4.16-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git, self.assertRaises(ReleaseGateError):
                validate_tls_certificate_scope(SHA,version)
            git.assert_not_called()

    def test_missing_approved_ancestor_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)

    def test_other_production_and_unlisted_test_or_doc_paths_rejected(self):
        for path in ('app/api/auth.py','app/admin.py','app/core_runtime.py','app/certificates/material.py','web/index.html','web/css/style.css',
                     'requirements-runtime.txt','scripts/build_bundle.py','scripts/install_system.py',
                     'setup.sh','.github/workflows/short-install.yml','.github/workflows/test.yml',
                     'tests/unrelated.py','docs/unrelated.md'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_rename_delete_type_change_and_nonregular_or_executable_modes_rejected(self):
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n',
                       'T\tREADME.md\n','C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_installer_executable_bit_cannot_be_removed(self):
        with self.assertRaises(ReleaseGateError):self.scope(changes='M\tinstall.sh\n',mode='100644')

    def test_application_installer_and_version_cannot_hide_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'version':b'0.4.15\n'}, {'version':b'0.4.16 \n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_only_twelve_explicit_browser_skips_are_accepted(self):
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        for path, names in BROWSER_SKIP_STEPS.items():
            run={'id':42,'run_attempt':1,'head_sha':SHA,'status':'completed','conclusion':'success'}
            job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
                 'steps':[{'name':name,'status':'completed','conclusion':'skipped'} for name in names]}
            if path=='test.yml':
                job['steps'].append({'name':TLS_CERTIFICATE_CORE_STEP,'status':'completed','conclusion':'success'})
            with self.subTest(path=path),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                validate_run_jobs({path:run},SHA,tls_certificate_no_browser=True)
            for invalid in ('missing','failure','cancelled','in_progress'):
                bad={**job,'steps':[dict(step) for step in job['steps']]}
                if invalid=='missing':bad['steps'].pop(0)
                elif invalid=='in_progress':bad['steps'][0]['status']=invalid
                else:bad['steps'][0]['conclusion']=invalid
                with self.subTest(path=path,invalid=invalid),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                    validate_run_jobs({path:run},SHA,tls_certificate_no_browser=True)

    def test_tls_exception_rejects_every_other_skipped_step(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps']=[{'name':name,'status':'completed','conclusion':'skipped'} for name in BROWSER_SKIP_STEPS['test.yml']]
        job['steps'].append({'name':TLS_CERTIFICATE_CORE_STEP,'status':'completed','conclusion':'success'})
        job['steps'].append({'name':'Run tests','status':'completed','conclusion':'skipped'})
        with patch('scripts.publish_release.request',return_value={'jobs':[job]}),self.assertRaises(ReleaseGateError):
            validate_run_jobs({'test.yml':run},SHA,tls_certificate_no_browser=True)

    def test_both_published_upgrades_and_pinned_real_core_step_must_succeed(self):
        run,job=ExactJobQualificationTests().fixtures()
        mandatory={'oneclick.yml':{UI_REDESIGN_UPGRADE_STEP,TLS_CERTIFICATE_UPGRADE_STEP},
                   'test.yml':{TLS_CERTIFICATE_CORE_STEP}}
        for path, required in mandatory.items():
            names=required | BROWSER_SKIP_STEPS.get(path,set())
            for name in required:
                for state in ('success','missing','skipped','failure','cancelled','in_progress'):
                    steps=[{'name':item,'status':'completed','conclusion':'success'} for item in names]
                    target=next(step for step in steps if step['name']==name)
                    if state=='missing':steps.remove(target)
                    elif state=='in_progress':target['status']=state
                    else:target['conclusion']=state
                    with self.subTest(path=path,name=name,state=state),patch(
                            'scripts.publish_release.request',side_effect=[{'jobs':[{**job,'steps':steps}]},run]):
                        if state=='success':validate_run_jobs({path:run},SHA,tls_certificate_no_browser=True)
                        else:
                            with self.assertRaises(ReleaseGateError):
                                validate_run_jobs({path:run},SHA,tls_certificate_no_browser=True)

    def test_tls_exception_cannot_mix_with_any_previous_exception(self):
        for name in ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser','startup_no_browser','ui_redesign_no_browser'):
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,tls_certificate_no_browser=True,**{name:True})
            request.assert_not_called()


class RemoteMaintenanceReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\n', mode=None,
              application_extra=b'', installer_extra=b'', version=b'0.4.17\n', ancestry=True):
        app=b"app = FastAPI(version='0.4.16')\n"
        installer=b'# install.sh --version v0.4.16\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':
                file_mode=mode or ('100755' if args[-1] in ('install.sh','setup.sh') else '100644')
                return file_mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                return {
                    REMOTE_MAINTENANCE_BASELINE+':main.py': app,
                    SHA+':main.py': app.replace(b'0.4.16',b'0.4.17')+application_extra,
                    SHA+':VERSION': version,
                    REMOTE_MAINTENANCE_BASELINE+':install.sh': installer,
                    SHA+':install.sh': installer.replace(b'0.4.16',b'0.4.17')+installer_extra,
                }[args[2]]
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_remote_maintenance_scope(SHA,'0.4.17')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',REMOTE_MAINTENANCE_BASELINE,SHA])

    def test_approved_version_and_exact_release_paths_allowed(self):
        self.scope(changes=''.join('M\t'+path+'\n' for path in sorted(REMOTE_MAINTENANCE_FILES)))
        self.scope(changes='A\ttests/published_upgrade_helpers.py\n')

    def test_exception_rejects_old_future_or_nonstable_versions_before_git(self):
        for version in ('0.4.16','0.4.18','0.5.0','0.4.17-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git, self.assertRaises(ReleaseGateError):
                validate_remote_maintenance_scope(SHA,version)
            git.assert_not_called()

    def test_missing_approved_ancestor_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)

    def test_other_production_and_unlisted_test_or_doc_paths_rejected(self):
        for path in ('app/api/auth.py','app/admin.py','app/core_runtime.py','app/certificates/material.py','web/index.html','web/css/style.css',
                     'requirements-runtime.txt','scripts/build_bundle.py','scripts/install_system.py',
                     '.github/workflows/test.yml',
                     'tests/test_oneclick_system.py','tests/unrelated.py','docs/unrelated.md',
                     'app/services/system_ops.py','scripts/system_ops.py','web/maintenance.html'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_rename_delete_type_change_and_nonregular_or_executable_modes_rejected(self):
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n',
                       'T\tREADME.md\n','C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_installer_and_bootstrap_executable_bits_cannot_be_removed(self):
        for name in ('install.sh','setup.sh'):
            with self.subTest(name=name),self.assertRaises(ReleaseGateError):
                self.scope(changes='M\t'+name+'\n',mode='100644')

    def test_application_installer_and_version_cannot_hide_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'version':b'0.4.16\n'}, {'version':b'0.4.17 \n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_only_twelve_explicit_browser_skips_are_accepted(self):
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        for path, names in BROWSER_SKIP_STEPS.items():
            run={'id':42,'run_attempt':1,'head_sha':SHA,'status':'completed','conclusion':'success'}
            job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
                 'steps':[{'name':name,'status':'completed','conclusion':'skipped'} for name in names]}
            if path=='test.yml':
                job['steps'].append({'name':TLS_CERTIFICATE_CORE_STEP,'status':'completed','conclusion':'success'})
            with self.subTest(path=path),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                validate_run_jobs({path:run},SHA,remote_maintenance_no_browser=True)
            for invalid in ('missing','failure','cancelled','in_progress'):
                bad={**job,'steps':[dict(step) for step in job['steps']]}
                if invalid=='missing':bad['steps'].pop(0)
                elif invalid=='in_progress':bad['steps'][0]['status']=invalid
                else:bad['steps'][0]['conclusion']=invalid
                with self.subTest(path=path,invalid=invalid),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                    validate_run_jobs({path:run},SHA,remote_maintenance_no_browser=True)

    def test_remote_maintenance_exception_rejects_every_other_skipped_step(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps']=[{'name':name,'status':'completed','conclusion':'skipped'} for name in BROWSER_SKIP_STEPS['test.yml']]
        job['steps'].append({'name':TLS_CERTIFICATE_CORE_STEP,'status':'completed','conclusion':'success'})
        job['steps'].append({'name':'Run tests','status':'completed','conclusion':'skipped'})
        with patch('scripts.publish_release.request',return_value={'jobs':[job]}),self.assertRaises(ReleaseGateError):
            validate_run_jobs({'test.yml':run},SHA,remote_maintenance_no_browser=True)

    def test_all_published_upgrades_real_core_and_full_loopback_must_succeed(self):
        run,job=ExactJobQualificationTests().fixtures()
        mandatory={'oneclick.yml':{UI_REDESIGN_UPGRADE_STEP,TLS_CERTIFICATE_UPGRADE_STEP,REMOTE_MAINTENANCE_UPGRADE_STEP},
                   'test.yml':{TLS_CERTIFICATE_CORE_STEP},
                   'loopback.yml':REMOTE_MAINTENANCE_LOOPBACK_STEPS}
        for path, required in mandatory.items():
            names=required | BROWSER_SKIP_STEPS.get(path,set())
            for name in required:
                for state in ('success','missing','skipped','failure','cancelled','in_progress'):
                    steps=[{'name':item,'status':'completed','conclusion':'success'} for item in names]
                    target=next(step for step in steps if step['name']==name)
                    if state=='missing':steps.remove(target)
                    elif state=='in_progress':target['status']=state
                    else:target['conclusion']=state
                    with self.subTest(path=path,name=name,state=state),patch(
                            'scripts.publish_release.request',side_effect=[{'jobs':[{**job,'steps':steps}]},run]):
                        if state=='success':validate_run_jobs({path:run},SHA,remote_maintenance_no_browser=True)
                        else:
                            with self.assertRaises(ReleaseGateError):
                                validate_run_jobs({path:run},SHA,remote_maintenance_no_browser=True)

    def test_remote_maintenance_exception_cannot_mix_with_any_previous_exception(self):
        for name in ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser','startup_no_browser','ui_redesign_no_browser','tls_certificate_no_browser'):
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,remote_maintenance_no_browser=True,**{name:True})
            request.assert_not_called()


class SimpleNodeReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tVERSION\nM\tmain.py\nM\tinstall.sh\n', mode=None,
              application_extra=b'', installer_extra=b'', version=b'0.4.18\n', ancestry=True, ci_extra=b''):
        app=b"app = FastAPI(version='0.4.17')\n"
        installer=b'# install.sh --version v0.4.17\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':
                file_mode=mode or ('100755' if args[-1] in ('install.sh','setup.sh') else '100644')
                return file_mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                if args[2] in (SHA+':'+name for name in SIMPLE_NODE_CI_FIX_HASHES):
                    return b'approved CI fixture\n'+ci_extra
                return {
                    SIMPLE_NODE_BASELINE+':main.py': app,
                    SHA+':main.py': app.replace(b'0.4.17',b'0.4.18')+application_extra,
                    SHA+':VERSION': version,
                    SIMPLE_NODE_BASELINE+':install.sh': installer,
                    SHA+':install.sh': installer.replace(b'0.4.17',b'0.4.18')+installer_extra,
                }[args[2]]
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.SIMPLE_NODE_CI_FIX_HASHES', {name: hashlib.sha256(b'approved CI fixture\n').hexdigest() for name in SIMPLE_NODE_CI_FIX_HASHES}), \
                patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_simple_node_scope(SHA,'0.4.18')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',SIMPLE_NODE_BASELINE,SHA])

    def test_approved_version_and_exact_release_paths_allowed(self):
        self.scope(changes=''.join('M\t'+path+'\n' for path in sorted(SIMPLE_NODE_FILES)))
        self.scope(changes='A\ttests/published_upgrade_helpers.py\n')

    def test_ci_helpers_allow_only_the_reviewed_fixed_bytes(self):
        for name in SIMPLE_NODE_CI_FIX_HASHES:
            with self.subTest(name=name):
                self.scope(changes='M\t'+name+'\n')
                with self.assertRaisesRegex(ReleaseGateError, 'bytes differ'):
                    self.scope(changes='M\t'+name+'\n', ci_extra=b'weakened assertion\n')

    def test_exception_rejects_old_future_or_nonstable_versions_before_git(self):
        for version in ('0.4.17','0.4.19','0.5.0','0.4.18-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git, self.assertRaises(ReleaseGateError):
                validate_simple_node_scope(SHA,version)
            git.assert_not_called()

    def test_missing_approved_ancestor_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)

    def test_other_production_and_unlisted_test_or_doc_paths_rejected(self):
        for path in ('app/api/auth.py','app/admin.py','app/core_runtime.py','app/certificates/material.py','web/index.html','web/css/style.css',
                     'requirements-runtime.txt','scripts/build_bundle.py','scripts/install_system.py',
                     '.github/workflows/test.yml',
                     'tests/test_oneclick_system.py','tests/unrelated.py','docs/unrelated.md',
                     'app/services/system_ops.py','scripts/system_ops.py','web/maintenance.html'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_rename_delete_type_change_and_nonregular_or_executable_modes_rejected(self):
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n',
                       'T\tREADME.md\n','C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_installer_and_bootstrap_executable_bits_cannot_be_removed(self):
        for name in ('install.sh','setup.sh'):
            with self.subTest(name=name),self.assertRaises(ReleaseGateError):
                self.scope(changes='M\t'+name+'\n',mode='100644')

    def test_application_installer_and_version_cannot_hide_changes(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'version':b'0.4.17\n'}, {'version':b'0.4.18 \n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_only_twelve_explicit_browser_skips_are_accepted(self):
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        for path, names in BROWSER_SKIP_STEPS.items():
            run={'id':42,'run_attempt':1,'head_sha':SHA,'status':'completed','conclusion':'success'}
            job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
                 'steps':[{'name':name,'status':'completed','conclusion':'skipped'} for name in names]}
            if path=='test.yml':
                job['steps'].extend({'name':name,'status':'completed','conclusion':'success'} for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP))
            with self.subTest(path=path),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                validate_run_jobs({path:run},SHA,simple_node_no_browser=True)
            for invalid in ('missing','failure','cancelled','in_progress'):
                bad={**job,'steps':[dict(step) for step in job['steps']]}
                if invalid=='missing':bad['steps'].pop(0)
                elif invalid=='in_progress':bad['steps'][0]['status']=invalid
                else:bad['steps'][0]['conclusion']=invalid
                with self.subTest(path=path,invalid=invalid),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                    validate_run_jobs({path:run},SHA,simple_node_no_browser=True)

    def test_simple_node_exception_rejects_every_other_skipped_step(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps']=[{'name':name,'status':'completed','conclusion':'skipped'} for name in BROWSER_SKIP_STEPS['test.yml']]
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'} for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP))
        job['steps'].append({'name':'Run tests','status':'completed','conclusion':'skipped'})
        with patch('scripts.publish_release.request',return_value={'jobs':[job]}),self.assertRaises(ReleaseGateError):
            validate_run_jobs({'test.yml':run},SHA,simple_node_no_browser=True)

    def test_all_published_upgrades_real_core_and_full_loopback_must_succeed(self):
        run,job=ExactJobQualificationTests().fixtures()
        mandatory={'oneclick.yml':{UI_REDESIGN_UPGRADE_STEP,TLS_CERTIFICATE_UPGRADE_STEP,REMOTE_MAINTENANCE_UPGRADE_STEP,SIMPLE_NODE_UPGRADE_STEP},
                   'test.yml':{TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP},
                   'loopback.yml':SIMPLE_NODE_LOOPBACK_STEPS}
        for path, required in mandatory.items():
            names=required | BROWSER_SKIP_STEPS.get(path,set())
            for name in required:
                for state in ('success','missing','skipped','failure','cancelled','in_progress'):
                    steps=[{'name':item,'status':'completed','conclusion':'success'} for item in names]
                    target=next(step for step in steps if step['name']==name)
                    if state=='missing':steps.remove(target)
                    elif state=='in_progress':target['status']=state
                    else:target['conclusion']=state
                    with self.subTest(path=path,name=name,state=state),patch(
                            'scripts.publish_release.request',side_effect=[{'jobs':[{**job,'steps':steps}]},run]):
                        if state=='success':validate_run_jobs({path:run},SHA,simple_node_no_browser=True)
                        else:
                            with self.assertRaises(ReleaseGateError):
                                validate_run_jobs({path:run},SHA,simple_node_no_browser=True)

    def test_simple_node_exception_cannot_mix_with_any_previous_exception(self):
        for name in ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser','startup_no_browser','ui_redesign_no_browser','tls_certificate_no_browser','remote_maintenance_no_browser'):
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,simple_node_no_browser=True,**{name:True})
            request.assert_not_called()


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
                    members = [source for source,_ in (*build_release_kit.SOURCE_ASSETS,
                        ('docs/RELEASE_NOTES.md','RELEASE_NOTES.md'))]
                    members.extend(install_ops_control.SOURCE_DEPENDENCIES.values())
                    # A root helper may intentionally reuse the same source as
                    # a public kit asset. git archive writes that path once.
                    for member in dict.fromkeys(members):
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

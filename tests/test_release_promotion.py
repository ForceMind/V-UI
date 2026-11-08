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
import urllib.error
import urllib.parse
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
    MAINTENANCE_SHARE_BASELINE,
    MAINTENANCE_SHARE_FILES,
    MAINTENANCE_SHARE_REQUIRED,
    MAINTENANCE_SHARE_JOB_COUNTS,
    MAINTENANCE_SHARE_UPGRADE_STEP,
    MAINTENANCE_SHARE_EXTRA_STEPS,
    validate_maintenance_share_scope,
    SIMPLE_OPERATIONS_BASELINE,
    SIMPLE_OPERATIONS_FILES,
    SIMPLE_OPERATIONS_REQUIRED,
    SIMPLE_OPERATIONS_JOB_COUNTS,
    SIMPLE_OPERATIONS_UPGRADE_STEP,
    SIMPLE_OPERATIONS_INSTALL_STEP,
    validate_simple_operations_scope,
    NETLINK_SERVICE_BASELINE,
    NETLINK_SERVICE_FILES,
    NETLINK_SERVICE_CI_HASHES,
    NETLINK_SERVICE_DIAGNOSTIC_STEP,
    NETLINK_SERVICE_UPGRADE_STEP,
    NETLINK_SERVICE_MANUAL_IF,
    validate_netlink_service_scope,
    MAINTENANCE_180_BASELINE,
    MAINTENANCE_180_FILES,
    validate_maintenance_180_scope,
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


class Maintenance180ReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tscripts/publish_release.py\n', mode='100644',
              application_extra=b'', installer_extra=b'', version=b'0.4.19\n', ancestry=True):
        app=b"app = FastAPI(version='0.4.19')\n"
        installer=b'# install.sh --version v0.4.19\nactual_installer()\n'
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                return {
                    MAINTENANCE_180_BASELINE+':main.py': app,
                    SHA+':main.py': app+application_extra,
                    SHA+':VERSION': version,
                    MAINTENANCE_180_BASELINE+':install.sh': installer,
                    SHA+':install.sh': installer+installer_extra,
                }[args[2]]
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_maintenance_180_scope(SHA,'0.4.19')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',MAINTENANCE_180_BASELINE,SHA])

    def test_frozen_candidate_and_exact_gate_and_document_paths_allowed(self):
        self.scope(changes='')
        self.scope(changes=''.join('M\t'+path+'\n' for path in sorted(MAINTENANCE_180_FILES)))
        self.scope(changes='A\tdocs/LONG_MAINTENANCE_0419.md\n')

    def test_exception_rejects_old_future_or_nonstable_versions_before_git(self):
        for version in ('0.4.18','0.4.20','0.5.0','0.4.19-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_maintenance_180_scope(SHA,version)
            git.assert_not_called()

    def test_missing_approved_ancestor_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)

    def test_frozen_product_other_paths_and_post_release_pointers_rejected(self):
        for path in ('VERSION','main.py','install.sh','setup.sh','.github/workflows/short-install.yml',
                     'app/api/maintenance.py','app/services/maintenance.py','scripts/system_launcher.py',
                     'scripts/system_ops.py','web/maintenance.html','web/js/maintenance.js',
                     'requirements-runtime.txt','scripts/build_bundle.py','scripts/install_system.py',
                     '.github/workflows/test.yml','.github/workflows/oneclick.yml',
                     'scripts/system_maintenance_smoke.py','tests/test_system_maintenance.py',
                     'tests/test_oneclick_system.py','tests/unrelated.py','docs/unrelated.md'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_rename_delete_type_change_and_nonregular_or_executable_modes_rejected(self):
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n',
                       'T\tREADME.md\n','C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_frozen_app_installer_and_version_are_checked_without_version_rewrite(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'version':b'0.4.18\n'}, {'version':b'0.4.19 \n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_only_twelve_explicit_browser_skips_are_accepted(self):
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        for path, names in BROWSER_SKIP_STEPS.items():
            run={'id':42,'run_attempt':1,'head_sha':SHA,'status':'completed','conclusion':'success'}
            job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
                 'steps':[{'name':name,'status':'completed','conclusion':'skipped'} for name in names]}
            if path=='test.yml':
                job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                                    for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP))
            with self.subTest(path=path),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                validate_run_jobs({path:run},SHA,maintenance_180_no_browser=True)
            for invalid in ('missing','failure','cancelled','in_progress'):
                bad={**job,'steps':[dict(step) for step in job['steps']]}
                if invalid=='missing':bad['steps'].pop(0)
                elif invalid=='in_progress':bad['steps'][0]['status']=invalid
                else:bad['steps'][0]['conclusion']=invalid
                with self.subTest(path=path,invalid=invalid),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                    validate_run_jobs({path:run},SHA,maintenance_180_no_browser=True)

    def test_nonbrowser_skips_remain_rejected(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps']=[{'name':name,'status':'completed','conclusion':'skipped'} for name in BROWSER_SKIP_STEPS['test.yml']]
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                            for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP))
        job['steps'].append({'name':'Run tests','status':'completed','conclusion':'skipped'})
        with patch('scripts.publish_release.request',return_value={'jobs':[job]}),self.assertRaises(ReleaseGateError):
            validate_run_jobs({'test.yml':run},SHA,maintenance_180_no_browser=True)

    def test_all_published_upgrades_real_core_template_and_full_loopback_must_succeed(self):
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
                        if state=='success':validate_run_jobs({path:run},SHA,maintenance_180_no_browser=True)
                        else:
                            with self.assertRaises(ReleaseGateError):
                                validate_run_jobs({path:run},SHA,maintenance_180_no_browser=True)

    def test_maintenance_exception_cannot_mix_with_any_previous_exception(self):
        for name in ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser',
                     'startup_no_browser','ui_redesign_no_browser','tls_certificate_no_browser',
                     'remote_maintenance_no_browser','simple_node_no_browser'):
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,maintenance_180_no_browser=True,**{name:True})
            request.assert_not_called()

    def test_exception_retains_exact_attempt_job_and_reread_checks(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                            for name in BROWSER_SKIP_STEPS['test.yml']-{step['name'] for step in job['steps']})
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                            for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP))
        for bad in ({**job,'run_id':43},{**job,'head_sha':'b'*40}):
            with self.subTest(bad=bad),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA,maintenance_180_no_browser=True)
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},{**run,'run_attempt':3}]),self.assertRaisesRegex(ReleaseGateError,'changed during'):
            validate_run_jobs({'test.yml':run},SHA,maintenance_180_no_browser=True)


class NetlinkServiceReleaseTests(unittest.TestCase):
    def scope(self, changes='M\tscripts/publish_release.py\n', mode=None,
              application_extra=b'', installer_extra=b'', version=b'0.4.20\n', ancestry=True,
              workflow_extra=b'', workflow_condition=None, ci_tamper=None, baseline_app=None,
              baseline_installer=None):
        app=b"app = FastAPI(version='0.4.19')\n" if baseline_app is None else baseline_app
        installer=b'# install.sh --version v0.4.19\nactual_installer()\n' if baseline_installer is None else baseline_installer
        diagnostic=('      - name: '+NETLINK_SERVICE_DIAGNOSTIC_STEP+'\n').encode()
        workflow=b'name: tests\n'+diagnostic+NETLINK_SERVICE_MANUAL_IF+b'        run: exact_probe\n'
        current_workflow=workflow.replace(diagnostic+NETLINK_SERVICE_MANUAL_IF,diagnostic,1)
        if workflow_condition is not None:current_workflow=workflow_condition
        ci={name:('reviewed '+name+'\n').encode() for name in NETLINK_SERVICE_CI_HASHES}
        hashes={name:hashlib.sha256(content).hexdigest() for name,content in ci.items()}
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':
                actual_mode=mode or ('100755' if args[-1]=='install.sh' else '100644')
                return actual_mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                name=args[2].split(':',1)[1]
                if args[2].startswith(SHA+':') and name in ci:
                    return ci[name]+(b'weakened assertion\n' if name==ci_tamper else b'')
                return {
                    NETLINK_SERVICE_BASELINE+':main.py': app,
                    SHA+':main.py': app.replace(b"version='0.4.19'",b"version='0.4.20'",1)+application_extra,
                    SHA+':VERSION': version,
                    NETLINK_SERVICE_BASELINE+':install.sh': installer,
                    SHA+':install.sh': installer.replace(b'--version v0.4.19',b'--version v0.4.20',1)+installer_extra,
                    NETLINK_SERVICE_BASELINE+':.github/workflows/test.yml':workflow,
                    SHA+':.github/workflows/test.yml':current_workflow+workflow_extra,
                }[args[2]]
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git), \
                patch.dict('scripts.publish_release.NETLINK_SERVICE_CI_HASHES',hashes,clear=True):
            validate_netlink_service_scope(SHA,'0.4.20')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',NETLINK_SERVICE_BASELINE,SHA])

    def test_frozen_candidate_and_exact_gate_and_document_paths_allowed(self):
        self.scope(changes='')
        self.scope(changes=''.join('M\t'+path+'\n' for path in sorted(NETLINK_SERVICE_FILES)))
        self.scope(changes='A\tdocs/SING_BOX_SYSTEMD_0420.md\n')

    def test_exception_rejects_old_future_or_nonstable_versions_before_git(self):
        for version in ('0.4.18','0.4.19','0.4.21','0.5.0','0.4.20-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_netlink_service_scope(SHA,version)
            git.assert_not_called()

    def test_missing_approved_ancestor_is_rejected(self):
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)

    def test_frozen_product_other_paths_and_post_release_pointers_rejected(self):
        for path in ('setup.sh','.github/workflows/short-install.yml',
                     'app/api/maintenance.py','app/services/maintenance.py','scripts/system_launcher.py',
                     'scripts/system_ops.py','web/maintenance.html','web/js/maintenance.js',
                     'requirements-runtime.txt','scripts/build_bundle.py','scripts/install_system.py',
                     'scripts/service_support.py','scripts/sing_box_systemd_probe.py',
                     'tests/unrelated.py','docs/unrelated.md',
                     'docs/LONG_MAINTENANCE_0419.md','tests/published_upgrade_helpers_extra.py'):
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+path+'\n')

    def test_rename_delete_type_change_and_nonregular_or_executable_modes_rejected(self):
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n',
                       'T\tREADME.md\n','C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_frozen_app_installer_accept_only_single_version_literal_rewrite(self):
        for kwargs in ({'application_extra':b'new_logic()\n'}, {'installer_extra':b'new_logic()\n'},
                       {'version':b'0.4.19\n'}, {'version':b'0.4.20 \n'},
                       {'baseline_app':b"version='0.4.19' version='0.4.19'\n"},
                       {'baseline_installer':b'--version v0.4.19 --version v0.4.19\n'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReleaseGateError):self.scope(**kwargs)

    def test_only_twelve_explicit_browser_skips_are_accepted(self):
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        for path, names in BROWSER_SKIP_STEPS.items():
            run={'id':42,'run_attempt':1,'head_sha':SHA,'status':'completed','conclusion':'success'}
            job={'id':7,'run_id':42,'head_sha':SHA,'status':'completed','conclusion':'success',
                 'steps':[{'name':name,'status':'completed','conclusion':'skipped'} for name in names]}
            if path=='test.yml':
                job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                                    for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP,NETLINK_SERVICE_DIAGNOSTIC_STEP))
            with self.subTest(path=path),patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},run]):
                validate_run_jobs({path:run},SHA,netlink_service_no_browser=True)
            for invalid in ('missing','failure','cancelled','in_progress'):
                bad={**job,'steps':[dict(step) for step in job['steps']]}
                if invalid=='missing':bad['steps'].pop(0)
                elif invalid=='in_progress':bad['steps'][0]['status']=invalid
                else:bad['steps'][0]['conclusion']=invalid
                with self.subTest(path=path,invalid=invalid),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                    validate_run_jobs({path:run},SHA,netlink_service_no_browser=True)

    def test_nonbrowser_skips_remain_rejected(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps']=[{'name':name,'status':'completed','conclusion':'skipped'} for name in BROWSER_SKIP_STEPS['test.yml']]
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                            for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP,NETLINK_SERVICE_DIAGNOSTIC_STEP))
        for name in ('Run tests','Browser unapproved additional workflow'):
            invalid={**job,'steps':job['steps']+[{'name':name,'status':'completed','conclusion':'skipped'}]}
            with self.subTest(name=name),patch('scripts.publish_release.request',return_value={'jobs':[invalid]}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA,netlink_service_no_browser=True)

    def test_all_published_upgrades_real_core_template_and_full_loopback_must_succeed(self):
        run,job=ExactJobQualificationTests().fixtures()
        mandatory={'oneclick.yml':{UI_REDESIGN_UPGRADE_STEP,TLS_CERTIFICATE_UPGRADE_STEP,REMOTE_MAINTENANCE_UPGRADE_STEP,SIMPLE_NODE_UPGRADE_STEP,NETLINK_SERVICE_UPGRADE_STEP},
                   'test.yml':{TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP,NETLINK_SERVICE_DIAGNOSTIC_STEP},
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
                        if state=='success':validate_run_jobs({path:run},SHA,netlink_service_no_browser=True)
                        else:
                            with self.assertRaises(ReleaseGateError):
                                validate_run_jobs({path:run},SHA,netlink_service_no_browser=True)

    def test_netlink_exception_cannot_mix_with_any_previous_exception(self):
        for name in ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser',
                     'startup_no_browser','ui_redesign_no_browser','tls_certificate_no_browser',
                     'remote_maintenance_no_browser','simple_node_no_browser','maintenance_180_no_browser'):
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,netlink_service_no_browser=True,**{name:True})
            request.assert_not_called()

    def test_exception_retains_exact_attempt_job_and_reread_checks(self):
        run,job=ExactJobQualificationTests().fixtures()
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                            for name in BROWSER_SKIP_STEPS['test.yml']-{step['name'] for step in job['steps']})
        job['steps'].extend({'name':name,'status':'completed','conclusion':'success'}
                            for name in (TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP,NETLINK_SERVICE_DIAGNOSTIC_STEP))
        for bad in ({**job,'run_id':43},{**job,'head_sha':'b'*40}):
            with self.subTest(bad=bad),patch('scripts.publish_release.request',return_value={'jobs':[bad]}),self.assertRaises(ReleaseGateError):
                validate_run_jobs({'test.yml':run},SHA,netlink_service_no_browser=True)
        with patch('scripts.publish_release.request',side_effect=[{'jobs':[job]},{**run,'run_attempt':3}]),self.assertRaisesRegex(ReleaseGateError,'changed during'):
            validate_run_jobs({'test.yml':run},SHA,netlink_service_no_browser=True)

    def test_acceptance_helpers_have_fixed_full_sha256_and_reject_unreviewed_bytes(self):
        self.assertEqual(set(NETLINK_SERVICE_CI_HASHES),{
            '.github/workflows/oneclick.yml','scripts/system_maintenance_smoke.py',
            'tests/test_system_maintenance.py','tests/test_short_install_contract.py',
            'tests/test_oneclick_system.py','tests/published_upgrade_helpers.py',
            'tests/test_published_upgrade_helpers.py'})
        for digest in NETLINK_SERVICE_CI_HASHES.values():self.assertRegex(digest,r'^[a-f0-9]{64}$')
        for name in NETLINK_SERVICE_CI_HASHES:
            with self.subTest(name=name),self.assertRaisesRegex(ReleaseGateError,'reviewed CI bytes differ: '+name):
                self.scope(changes='',ci_tamper=name)

    def test_diagnostic_only_loses_its_one_manual_condition(self):
        for kwargs in ({'workflow_extra':b'new_step\n'},
                       {'workflow_condition':b'manual condition retained\n'}):
            with self.subTest(kwargs=kwargs),self.assertRaisesRegex(ReleaseGateError,'manual-only condition'):
                self.scope(**kwargs)

    def test_installer_keeps_executable_mode(self):
        with self.assertRaises(ReleaseGateError):self.scope(changes='M\tinstall.sh\n',mode='100644')

    def test_workflow_dispatch_binds_new_boolean_and_cli(self):
        workflow=Path('.github/workflows/publish.yml').read_text()
        self.assertIn('      netlink_service_no_browser:\n',workflow)
        self.assertIn('NETLINK_SERVICE_NO_BROWSER: ${{ inputs.netlink_service_no_browser }}',workflow)
        self.assertIn('args+=(--netlink-service-no-browser)',workflow)


class SimpleOperationsReleaseTests(unittest.TestCase):
    FLAGS = ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser',
             'startup_no_browser','ui_redesign_no_browser','tls_certificate_no_browser',
             'remote_maintenance_no_browser','simple_node_no_browser','maintenance_180_no_browser',
             'netlink_service_no_browser')
    MANDATORY = {
        'test.yml': {TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP,NETLINK_SERVICE_DIAGNOSTIC_STEP},
        'oneclick.yml': {UI_REDESIGN_UPGRADE_STEP,TLS_CERTIFICATE_UPGRADE_STEP,
                         REMOTE_MAINTENANCE_UPGRADE_STEP,SIMPLE_NODE_UPGRADE_STEP,
                         NETLINK_SERVICE_UPGRADE_STEP,SIMPLE_OPERATIONS_UPGRADE_STEP,
                         SIMPLE_OPERATIONS_INSTALL_STEP},
        'loopback.yml': SIMPLE_NODE_LOOPBACK_STEPS,
    }

    def scope(self, changes='M\tscripts/publish_release.py\n', mode='100644',
              ancestry=True, version=b'0.4.21\n', tamper=None):
        content={'VERSION':b'0.4.21\n','main.py':b"app = FastAPI(version='0.4.21')\n",
                 'install.sh':b'# install.sh --version v0.4.21\nactual_installer()\n'}
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                revision,name=args[2].split(':',1)
                if revision==SHA and name=='VERSION':return version
                return content[name]+(b'changed\n' if revision==SHA and name==tamper else b'')
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_simple_operations_scope(SHA,'0.4.21')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',SIMPLE_OPERATIONS_BASELINE,SHA])

    def fixtures(self):
        runs={};jobs={}
        for index,path in enumerate(sorted(SIMPLE_OPERATIONS_REQUIRED),1):
            run={'id':index,'run_attempt':2,'head_sha':SHA,'status':'completed',
                 'conclusion':'success','path':'.github/workflows/'+path,'html_url':'https://example.test/'+path}
            runs[path]=run
            names=self.MANDATORY.get(path,set()) | BROWSER_SKIP_STEPS.get(path,set()) | {'Required nonbrowser work'}
            jobs[index]=[{'id':index*10+number,'run_id':index,'head_sha':SHA,
                          'status':'completed','conclusion':'success',
                          'steps':[{'name':name,'status':'completed',
                                    'conclusion':'skipped' if name in BROWSER_SKIP_STEPS.get(path,set()) else 'success'}
                                   for name in sorted(names)]}
                         for number in range(SIMPLE_OPERATIONS_JOB_COUNTS[path])]
        return runs,jobs

    def qualify(self, runs, jobs, reread=None):
        def request(path):
            run_id=int(path.split('/')[2])
            if '/jobs?' in path:return {'jobs':jobs[run_id]}
            return (reread or {}).get(run_id,next(run for run in runs.values() if run['id']==run_id))
        with patch('scripts.publish_release.request',side_effect=request):
            validate_run_jobs(runs,SHA,simple_operations_no_browser=True)

    def test_only_frozen_product_and_exact_release_documents_are_allowed(self):
        self.assertEqual(SIMPLE_OPERATIONS_BASELINE,'0a133205bb7a1f1d0deabd28eb4fffe77625fd44')
        self.assertEqual(SIMPLE_OPERATIONS_FILES,{
            'scripts/publish_release.py','tests/test_release_promotion.py','.github/workflows/publish.yml',
            'AGENTS.md','README.md','CHANGELOG.md','docs/SIMPLE_OPERATIONS_PROGRESS.md',
            'docs/DEVELOPMENT_HANDOFF.md','docs/README.md','docs/RELEASING.md',
            'docs/RELEASE_NOTES.md','docs/INSTALLATION.md','docs/API.md'})
        self.scope(changes='')
        self.scope(changes=''.join('M\t'+name+'\n' for name in sorted(SIMPLE_OPERATIONS_FILES)))
        self.scope(changes='A\tdocs/SIMPLE_OPERATIONS_PROGRESS.md\n')

    def test_scope_cannot_change_app_installer_ci_helpers_or_bootstrap(self):
        for name in ('VERSION','main.py','install.sh','setup.sh','.github/workflows/short-install.yml',
                     'app/api/maintenance.py','app/services/simple_operations.py','web/nodes.html',
                     'scripts/system_ops.py','scripts/install_system.py','scripts/build_bundle.py',
                     'requirements-runtime.txt','scripts/core_runtime.py','scripts/fetch_test_cores.py',
                     '.github/workflows/oneclick.yml','.github/workflows/test.yml',
                     '.github/workflows/loopback.yml','tests/test_oneclick_system.py',
                     'tests/published_upgrade_helpers.py','scripts/system_maintenance_smoke.py',
                     'tests/unrelated.py','docs/unrelated.md'):
            with self.subTest(name=name),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+name+'\n')
        for name in ('main.py','install.sh'):
            with self.subTest(name=name),self.assertRaisesRegex(ReleaseGateError,'frozen product bytes'):
                self.scope(changes='',tamper=name)

    def test_wrong_version_ancestry_and_file_shapes_are_rejected(self):
        for version in ('0.4.19','0.4.20','0.4.22','0.5.0','0.4.21-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_simple_operations_scope(SHA,version)
            git.assert_not_called()
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)
        for version in (b'0.4.20\n',b'0.4.21 \n',b'0.4.21'):
            with self.subTest(version=version),self.assertRaisesRegex(ReleaseGateError,'version identity'):self.scope(version=version)
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n','T\tREADME.md\n',
                       'C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_nine_workflows_and_all_twelve_jobs_with_only_twelve_browser_skips(self):
        runs,jobs=self.fixtures()
        self.assertEqual(len(runs),9)
        self.assertEqual(sum(map(len,jobs.values())),12)
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        self.qualify(runs,jobs)
        self.assertEqual(set(select_runs(list(runs.values()),SHA,simple_operations_no_browser=True)),SIMPLE_OPERATIONS_REQUIRED)
        for path in SIMPLE_OPERATIONS_REQUIRED:
            reduced={name:run for name,run in runs.items() if name!=path}
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.qualify(reduced,jobs)
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):
                select_runs(list(reduced.values()),SHA,simple_operations_no_browser=True)
        with self.assertRaises(ReleaseGateError):self.qualify({**runs,'unexpected.yml':runs['test.yml']},jobs)
        for path in runs:
            for change in ('missing','extra','duplicate'):
                altered={key:list(value) for key,value in jobs.items()}
                target=altered[runs[path]['id']]
                if change=='missing':target.pop()
                elif change=='duplicate':target.append(target[0])
                else:target.append({**target[0],'id':9999})
                with self.subTest(path=path,change=change),self.assertRaises(ReleaseGateError):self.qualify(runs,altered)

    def test_latest_ninth_workflow_cannot_use_stale_wrong_revision_or_unsuccessful_evidence(self):
        runs,_=self.fixtures()
        for mutation in ('wrong_sha','failure','queued','newer_failed'):
            candidate=[dict(run) for run in runs.values()]
            short=next(run for run in candidate if run['path'].endswith('/short-install.yml'))
            if mutation=='wrong_sha':short['head_sha']='b'*40
            elif mutation=='failure':short['conclusion']='failure'
            elif mutation=='queued':short['status']='queued'
            else:candidate.append({**short,'id':999,'conclusion':'failure'})
            with self.subTest(mutation=mutation),self.assertRaises(ReleaseGateError):
                select_runs(candidate,SHA,simple_operations_no_browser=True)

    def test_required_installer_six_upgrades_core_template_diagnostic_and_loopback_cannot_skip(self):
        for path in set(self.MANDATORY) | set(BROWSER_SKIP_STEPS):
            for name in self.MANDATORY.get(path,set()) | BROWSER_SKIP_STEPS.get(path,set()):
                for state in ('missing','failure','cancelled','in_progress','skipped'):
                    if state=='skipped' and name in BROWSER_SKIP_STEPS.get(path,set()):continue
                    runs,jobs=self.fixtures();steps=jobs[runs[path]['id']][0]['steps']
                    target=next(step for step in steps if step['name']==name)
                    if state=='missing':steps.remove(target)
                    elif state=='in_progress':target['status']=state
                    else:target['conclusion']=state
                    with self.subTest(path=path,name=name,state=state),self.assertRaises(ReleaseGateError):self.qualify(runs,jobs)
        for path in SIMPLE_OPERATIONS_REQUIRED:
            for name in ('Required nonbrowser work','Browser unapproved extra step'):
                runs,jobs=self.fixtures()
                jobs[runs[path]['id']][0]['steps'].append({'name':name,'status':'completed','conclusion':'skipped'})
                with self.subTest(path=path,name=name),self.assertRaises(ReleaseGateError):self.qualify(runs,jobs)

    def test_all_jobs_and_latest_attempt_are_bound_to_exact_commit(self):
        for path in SIMPLE_OPERATIONS_REQUIRED:
            for key,value in (('head_sha','b'*40),('run_id',999),('conclusion','failure'),('status','in_progress')):
                runs,jobs=self.fixtures();jobs[runs[path]['id']][-1][key]=value
                with self.subTest(path=path,key=key),self.assertRaises(ReleaseGateError):self.qualify(runs,jobs)
            runs,jobs=self.fixtures();run=runs[path]
            with self.subTest(path=path),self.assertRaisesRegex(ReleaseGateError,'changed during'):
                self.qualify(runs,jobs,reread={run['id']:{**run,'run_attempt':3}})

    def test_exception_and_cli_cannot_mix_with_previous_flags(self):
        from scripts import publish_release
        argv=['publish_release.py','--commit',SHA,'--version','0.4.21',
              '--confirm','RELEASE 0.4.21 '+SHA,'--simple-operations-no-browser']
        for name in self.FLAGS:
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,simple_operations_no_browser=True,**{name:True})
            request.assert_not_called()
            with self.subTest(cli=name),patch('sys.argv',argv+['--'+name.replace('_','-')]), \
                    patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                publish_release.main()
            request.assert_not_called()

    def promotion(self, tamper_target=None):
        from scripts import publish_release
        runs,jobs=self.fixtures();writes=[];downloads=[]
        fixtures=ReleasePromotionTests()
        kit_name='vui-release-kit-'+SHA
        portable_names={'vui-linux-'+target+'-'+SHA:target for target in TARGETS[1:]}
        release={'id':900,'upload_url':'https://uploads.github.com/repos/ForceMind/V-UI/releases/900/assets{?name,label}',
                 'html_url':'https://example.test/release/900','draft':True}
        def artifact(name):return {'id':name,'name':name,'expired':False}
        def request(path,method='GET',data=None,binary=False):
            if method!='GET':
                writes.append((path,method,data))
                if path=='releases':return release
                if path.startswith('https://uploads.github.com/'):
                    return {'digest':'sha256:'+hashlib.sha256(data).hexdigest()}
                raise AssertionError((path,method))
            if path=='':return {'default_branch':'main'}
            if path=='git/ref/heads/main':return {'object':{'sha':SHA}}
            if path.startswith('actions/runs?'):return {'workflow_runs':list(runs.values())}
            if path.startswith(('git/ref/tags/','releases/tags/')):
                raise urllib.error.HTTPError(path,404,'absent',{},None)
            run_id=int(path.split('/')[2])
            if '/jobs?' in path:return {'jobs':jobs[run_id]}
            if '/artifacts?' in path:
                names=[kit_name] if run_id==runs['oneclick.yml']['id'] else list(portable_names)
                self.assertIn(run_id,(runs['oneclick.yml']['id'],runs['portable.yml']['id']))
                return {'artifacts':[artifact(name) for name in names]}
            return next(run for run in runs.values() if run['id']==run_id)
        def download(item):
            downloads.append(item['name'])
            if item['name']==kit_name:
                return fixtures.kit(commit='b'*40 if tamper_target==TARGETS[0] else SHA,version='0.4.21')
            target=portable_names[item['name']]
            return fixtures.portable(target,commit='b'*40 if target==tamper_target else SHA,version='0.4.21')
        argv=['publish_release.py','--commit',SHA,'--version','0.4.21',
              '--confirm','RELEASE 0.4.21 '+SHA,'--simple-operations-no-browser']
        with patch('sys.argv',argv),patch.dict('os.environ',{'GITHUB_REPOSITORY':'ForceMind/V-UI','GITHUB_EVENT_NAME':'workflow_dispatch'}), \
                patch('scripts.publish_release.Path.read_text',return_value='0.4.21\n'), \
                patch('scripts.publish_release.subprocess.check_output',return_value=SHA+'\n'), \
                patch('scripts.publish_release.subprocess.run',side_effect=AssertionError('Publication must never build')), \
                patch('scripts.publish_release.validate_simple_operations_scope') as scope, \
                patch('scripts.publish_release.request',side_effect=request), \
                patch('scripts.publish_release.download_artifact',side_effect=download),patch('builtins.print'):
            if tamper_target:
                with self.assertRaises(ReleaseGateError):publish_release.main()
                self.assertEqual(writes,[])
            else:
                publish_release.main()
            scope.assert_called_once_with(SHA,'0.4.21')
        return writes,downloads,runs

    def test_cli_qualifies_nine_workflows_and_promotes_same_commit_four_targets_without_building(self):
        writes,downloads,runs=self.promotion()
        self.assertEqual(len(downloads),4)
        self.assertEqual(set(downloads),{'vui-release-kit-'+SHA} | {'vui-linux-'+target+'-'+SHA for target in TARGETS[1:]})
        path,method,release=writes[0]
        self.assertEqual((path,method),('releases','POST'))
        self.assertEqual(release['target_commitish'],SHA)
        self.assertTrue(release['draft'])
        for text in ('0.4.21','浏览器未验证',SIMPLE_OPERATIONS_BASELINE,'14/15/16/17/19/20','发布时不重建'):
            self.assertIn(text,release['body'])
        for run in runs.values():self.assertIn(run['html_url'],release['body'])
        uploaded={urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['name'][0]:data
                  for path,method,data in writes[1:]}
        self.assertEqual(set(uploaded),ASSETS)
        for target in TARGETS:
            with zipfile.ZipFile(io.BytesIO(uploaded['vui-linux-'+target+'.zip'])) as archive:
                manifest=json.loads(archive.read('MANIFEST.json'))
            self.assertEqual((manifest['source_commit'],manifest['version'],manifest['targets']),(SHA,'0.4.21',[target]))

    def test_new_flag_cannot_promote_any_target_from_another_commit(self):
        for target in TARGETS:
            with self.subTest(target=target):self.promotion(tamper_target=target)

    def test_workflow_dispatch_binds_new_boolean_and_cli(self):
        workflow=Path('.github/workflows/publish.yml').read_text()
        self.assertIn('      simple_operations_no_browser:\n',workflow)
        self.assertIn('SIMPLE_OPERATIONS_NO_BROWSER: ${{ inputs.simple_operations_no_browser }}',workflow)
        self.assertIn("if [[ \"$SIMPLE_OPERATIONS_NO_BROWSER\" == 'true' ]]; then args+=(--simple-operations-no-browser); fi",workflow)


class MaintenanceShareReleaseTests(unittest.TestCase):
    FLAGS = ('installer_only_no_browser','ca_trust_no_browser','install_interaction_no_browser',
             'startup_no_browser','ui_redesign_no_browser','tls_certificate_no_browser',
             'remote_maintenance_no_browser','simple_node_no_browser','maintenance_180_no_browser',
             'netlink_service_no_browser','simple_operations_no_browser')
    MANDATORY = {
        'test.yml': {TLS_CERTIFICATE_CORE_STEP,SIMPLE_NODE_TEMPLATE_STEP,NETLINK_SERVICE_DIAGNOSTIC_STEP,'Run tests'},
        'oneclick.yml': {UI_REDESIGN_UPGRADE_STEP,TLS_CERTIFICATE_UPGRADE_STEP,
                         REMOTE_MAINTENANCE_UPGRADE_STEP,SIMPLE_NODE_UPGRADE_STEP,
                         NETLINK_SERVICE_UPGRADE_STEP,SIMPLE_OPERATIONS_UPGRADE_STEP,MAINTENANCE_SHARE_UPGRADE_STEP,
                         SIMPLE_OPERATIONS_INSTALL_STEP},
        'loopback.yml': SIMPLE_NODE_LOOPBACK_STEPS,
        'acme.yml': MAINTENANCE_SHARE_EXTRA_STEPS['acme.yml'],
        'short-install.yml': MAINTENANCE_SHARE_EXTRA_STEPS['short-install.yml'],
    }

    def scope(self, changes='M\tscripts/publish_release.py\n', mode='100644',
              ancestry=True, version=b'0.4.22\n', tamper=None):
        content={'VERSION':b'0.4.22\n','main.py':b"app = FastAPI(version='0.4.22')\n",
                 'install.sh':b'# install.sh --version v0.4.22\nactual_installer()\n'}
        def git(args, **kwargs):
            if args[1:3]==['diff','--name-status']:
                self.assertIn('--no-renames',args)
                return changes
            if args[1]=='ls-tree':return mode+' blob '+'f'*40+'\t'+args[-1]+'\n'
            if args[1]=='show':
                revision,name=args[2].split(':',1)
                if revision==SHA and name=='VERSION':return version
                return content[name]+(b'changed\n' if revision==SHA and name==tamper else b'')
            raise AssertionError(args)
        failure=None if ancestry else subprocess.CalledProcessError(1,['git','merge-base'])
        with patch('scripts.publish_release.subprocess.run',side_effect=failure) as ancestor, \
                patch('scripts.publish_release.subprocess.check_output',side_effect=git):
            validate_maintenance_share_scope(SHA,'0.4.22')
            self.assertEqual(ancestor.call_args.args[0],
                             ['git','merge-base','--is-ancestor',MAINTENANCE_SHARE_BASELINE,SHA])

    def fixtures(self):
        runs={};jobs={}
        for index,path in enumerate(sorted(MAINTENANCE_SHARE_REQUIRED),1):
            run={'id':index,'run_attempt':2,'head_sha':SHA,'status':'completed',
                 'conclusion':'success','path':'.github/workflows/'+path,'html_url':'https://example.test/'+path}
            runs[path]=run
            names=self.MANDATORY.get(path,set()) | BROWSER_SKIP_STEPS.get(path,set()) | {'Required nonbrowser work'}
            jobs[index]=[{'id':index*10+number,'run_id':index,'head_sha':SHA,
                          'status':'completed','conclusion':'success',
                          'steps':[{'name':name,'status':'completed',
                                    'conclusion':'skipped' if name in BROWSER_SKIP_STEPS.get(path,set()) else 'success'}
                                   for name in sorted(names)]}
                         for number in range(MAINTENANCE_SHARE_JOB_COUNTS[path])]
        return runs,jobs

    def qualify(self, runs, jobs, reread=None):
        def request(path):
            run_id=int(path.split('/')[2])
            if '/jobs?' in path:return {'jobs':jobs[run_id]}
            return (reread or {}).get(run_id,next(run for run in runs.values() if run['id']==run_id))
        with patch('scripts.publish_release.request',side_effect=request):
            validate_run_jobs(runs,SHA,maintenance_share_no_browser=True)

    def test_only_frozen_product_and_exact_release_documents_are_allowed(self):
        self.assertEqual(MAINTENANCE_SHARE_BASELINE,'72570cc449c2b4b8ac933588894360a5b284e329')
        self.assertEqual(MAINTENANCE_SHARE_FILES,{
            'scripts/publish_release.py','tests/test_release_promotion.py','.github/workflows/publish.yml',
            'AGENTS.md','README.md','CHANGELOG.md','docs/MAINTENANCE_SHARE_0422.md',
            'docs/DEVELOPMENT_HANDOFF.md','docs/README.md','docs/RELEASING.md',
            'docs/RELEASE_NOTES.md','docs/INSTALLATION.md','docs/API.md'})
        self.scope(changes='')
        self.scope(changes=''.join('M\t'+name+'\n' for name in sorted(MAINTENANCE_SHARE_FILES)))
        self.scope(changes='A\tdocs/MAINTENANCE_SHARE_0422.md\n')

    def test_scope_cannot_change_app_installer_ci_helpers_or_bootstrap(self):
        for name in ('VERSION','main.py','install.sh','setup.sh','.github/workflows/short-install.yml',
                     'app/api/maintenance.py','app/services/maintenance_share.py','web/nodes.html',
                     'scripts/system_ops.py','scripts/install_system.py','scripts/build_bundle.py',
                     'requirements-runtime.txt','scripts/core_runtime.py','scripts/fetch_test_cores.py',
                     '.github/workflows/oneclick.yml','.github/workflows/test.yml',
                     '.github/workflows/loopback.yml','tests/test_oneclick_system.py',
                     'tests/published_upgrade_helpers.py','scripts/system_maintenance_smoke.py',
                     'tests/unrelated.py','docs/unrelated.md','docs/SIMPLE_OPERATIONS_PROGRESS.md',
                     'app/middleware/auth.py','tests/test_maintenance_share_http.py'):
            with self.subTest(name=name),self.assertRaises(ReleaseGateError):self.scope(changes='M\t'+name+'\n')
        for name in ('main.py','install.sh'):
            with self.subTest(name=name),self.assertRaisesRegex(ReleaseGateError,'frozen product bytes'):
                self.scope(changes='',tamper=name)

    def test_wrong_version_ancestry_and_file_shapes_are_rejected(self):
        for version in ('0.4.19','0.4.20','0.4.21','0.4.23','0.5.0','0.4.22-rc1'):
            with self.subTest(version=version),patch('scripts.publish_release.subprocess.run') as git,self.assertRaises(ReleaseGateError):
                validate_maintenance_share_scope(SHA,version)
            git.assert_not_called()
        with self.assertRaisesRegex(ReleaseGateError,'ancestry'):self.scope(ancestry=False)
        for version in (b'0.4.20\n',b'0.4.22 \n',b'0.4.22'):
            with self.subTest(version=version),self.assertRaisesRegex(ReleaseGateError,'version identity'):self.scope(version=version)
        for change in ('D\tREADME.md\n','R100\tREADME.md\tdocs/README.md\n','T\tREADME.md\n',
                       'C100\tREADME.md\tdocs/README.md\n'):
            with self.subTest(change=change),self.assertRaises(ReleaseGateError):self.scope(changes=change)
        for mode in ('100755','120000','160000','040000'):
            with self.subTest(mode=mode),self.assertRaises(ReleaseGateError):self.scope(mode=mode)

    def test_nine_workflows_and_all_twelve_jobs_with_only_twelve_browser_skips(self):
        runs,jobs=self.fixtures()
        self.assertEqual(len(runs),9)
        self.assertEqual(sum(map(len,jobs.values())),12)
        self.assertEqual(sum(map(len,BROWSER_SKIP_STEPS.values())),12)
        self.qualify(runs,jobs)
        self.assertEqual(set(select_runs(list(runs.values()),SHA,maintenance_share_no_browser=True)),MAINTENANCE_SHARE_REQUIRED)
        for path in MAINTENANCE_SHARE_REQUIRED:
            reduced={name:run for name,run in runs.items() if name!=path}
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):self.qualify(reduced,jobs)
            with self.subTest(path=path),self.assertRaises(ReleaseGateError):
                select_runs(list(reduced.values()),SHA,maintenance_share_no_browser=True)
        with self.assertRaises(ReleaseGateError):self.qualify({**runs,'unexpected.yml':runs['test.yml']},jobs)
        for path in runs:
            for change in ('missing','extra','duplicate'):
                altered={key:list(value) for key,value in jobs.items()}
                target=altered[runs[path]['id']]
                if change=='missing':target.pop()
                elif change=='duplicate':target.append(target[0])
                else:target.append({**target[0],'id':9999})
                with self.subTest(path=path,change=change),self.assertRaises(ReleaseGateError):self.qualify(runs,altered)

    def test_latest_ninth_workflow_cannot_use_stale_wrong_revision_or_unsuccessful_evidence(self):
        runs,_=self.fixtures()
        for mutation in ('wrong_sha','failure','queued','newer_failed'):
            candidate=[dict(run) for run in runs.values()]
            short=next(run for run in candidate if run['path'].endswith('/short-install.yml'))
            if mutation=='wrong_sha':short['head_sha']='b'*40
            elif mutation=='failure':short['conclusion']='failure'
            elif mutation=='queued':short['status']='queued'
            else:candidate.append({**short,'id':999,'conclusion':'failure'})
            with self.subTest(mutation=mutation),self.assertRaises(ReleaseGateError):
                select_runs(candidate,SHA,maintenance_share_no_browser=True)

    def test_required_installer_seven_upgrades_core_template_diagnostic_and_loopback_cannot_skip(self):
        for path in set(self.MANDATORY) | set(BROWSER_SKIP_STEPS):
            for name in self.MANDATORY.get(path,set()) | BROWSER_SKIP_STEPS.get(path,set()):
                for state in ('missing','failure','cancelled','in_progress','skipped'):
                    if state=='skipped' and name in BROWSER_SKIP_STEPS.get(path,set()):continue
                    runs,jobs=self.fixtures();steps=jobs[runs[path]['id']][0]['steps']
                    target=next(step for step in steps if step['name']==name)
                    if state=='missing':steps.remove(target)
                    elif state=='in_progress':target['status']=state
                    else:target['conclusion']=state
                    with self.subTest(path=path,name=name,state=state),self.assertRaises(ReleaseGateError):self.qualify(runs,jobs)
        for path in MAINTENANCE_SHARE_REQUIRED:
            for name in ('Required nonbrowser work','Browser unapproved extra step'):
                runs,jobs=self.fixtures()
                jobs[runs[path]['id']][0]['steps'].append({'name':name,'status':'completed','conclusion':'skipped'})
                with self.subTest(path=path,name=name),self.assertRaises(ReleaseGateError):self.qualify(runs,jobs)

    def test_all_jobs_and_latest_attempt_are_bound_to_exact_commit(self):
        for path in MAINTENANCE_SHARE_REQUIRED:
            for key,value in (('head_sha','b'*40),('run_id',999),('conclusion','failure'),('status','in_progress')):
                runs,jobs=self.fixtures();jobs[runs[path]['id']][-1][key]=value
                with self.subTest(path=path,key=key),self.assertRaises(ReleaseGateError):self.qualify(runs,jobs)
            runs,jobs=self.fixtures();run=runs[path]
            with self.subTest(path=path),self.assertRaisesRegex(ReleaseGateError,'changed during'):
                self.qualify(runs,jobs,reread={run['id']:{**run,'run_attempt':3}})

    def test_exception_and_cli_cannot_mix_with_previous_flags(self):
        from scripts import publish_release
        with self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
            select_runs([],SHA,simple_operations_no_browser=True,maintenance_share_no_browser=True)
        argv=['publish_release.py','--commit',SHA,'--version','0.4.22',
              '--confirm','RELEASE 0.4.22 '+SHA,'--maintenance-share-no-browser']
        for name in self.FLAGS:
            with self.subTest(name=name),patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                validate_run_jobs({},SHA,maintenance_share_no_browser=True,**{name:True})
            request.assert_not_called()
            with self.subTest(cli=name),patch('sys.argv',argv+['--'+name.replace('_','-')]), \
                    patch('scripts.publish_release.request') as request,self.assertRaisesRegex(ReleaseGateError,'mutually exclusive'):
                publish_release.main()
            request.assert_not_called()

    def promotion(self, tamper_target=None):
        from scripts import publish_release
        runs,jobs=self.fixtures();writes=[];downloads=[]
        fixtures=ReleasePromotionTests()
        kit_name='vui-release-kit-'+SHA
        portable_names={'vui-linux-'+target+'-'+SHA:target for target in TARGETS[1:]}
        release={'id':900,'upload_url':'https://uploads.github.com/repos/ForceMind/V-UI/releases/900/assets{?name,label}',
                 'html_url':'https://example.test/release/900','draft':True}
        def artifact(name):return {'id':name,'name':name,'expired':False}
        def request(path,method='GET',data=None,binary=False):
            if method!='GET':
                writes.append((path,method,data))
                if path=='releases':return release
                if path.startswith('https://uploads.github.com/'):
                    return {'digest':'sha256:'+hashlib.sha256(data).hexdigest()}
                raise AssertionError((path,method))
            if path=='':return {'default_branch':'main'}
            if path=='git/ref/heads/main':return {'object':{'sha':SHA}}
            if path.startswith('actions/runs?'):return {'workflow_runs':list(runs.values())}
            if path.startswith(('git/ref/tags/','releases/tags/')):
                raise urllib.error.HTTPError(path,404,'absent',{},None)
            run_id=int(path.split('/')[2])
            if '/jobs?' in path:return {'jobs':jobs[run_id]}
            if '/artifacts?' in path:
                names=[kit_name] if run_id==runs['oneclick.yml']['id'] else list(portable_names)
                self.assertIn(run_id,(runs['oneclick.yml']['id'],runs['portable.yml']['id']))
                return {'artifacts':[artifact(name) for name in names]}
            return next(run for run in runs.values() if run['id']==run_id)
        def download(item):
            downloads.append(item['name'])
            if item['name']==kit_name:
                return fixtures.kit(commit='b'*40 if tamper_target==TARGETS[0] else SHA,version='0.4.22')
            target=portable_names[item['name']]
            return fixtures.portable(target,commit='b'*40 if target==tamper_target else SHA,version='0.4.22')
        argv=['publish_release.py','--commit',SHA,'--version','0.4.22',
              '--confirm','RELEASE 0.4.22 '+SHA,'--maintenance-share-no-browser']
        with patch('sys.argv',argv),patch.dict('os.environ',{'GITHUB_REPOSITORY':'ForceMind/V-UI','GITHUB_EVENT_NAME':'workflow_dispatch'}), \
                patch('scripts.publish_release.Path.read_text',return_value='0.4.22\n'), \
                patch('scripts.publish_release.subprocess.check_output',return_value=SHA+'\n'), \
                patch('scripts.publish_release.subprocess.run',side_effect=AssertionError('Publication must never build')), \
                patch('scripts.publish_release.validate_maintenance_share_scope') as scope, \
                patch('scripts.publish_release.request',side_effect=request), \
                patch('scripts.publish_release.download_artifact',side_effect=download),patch('builtins.print'):
            if tamper_target:
                with self.assertRaises(ReleaseGateError):publish_release.main()
                self.assertEqual(writes,[])
            else:
                publish_release.main()
            scope.assert_called_once_with(SHA,'0.4.22')
        return writes,downloads,runs

    def test_cli_qualifies_nine_workflows_and_promotes_same_commit_four_targets_without_building(self):
        writes,downloads,runs=self.promotion()
        self.assertEqual(len(downloads),4)
        self.assertEqual(set(downloads),{'vui-release-kit-'+SHA} | {'vui-linux-'+target+'-'+SHA for target in TARGETS[1:]})
        path,method,release=writes[0]
        self.assertEqual((path,method),('releases','POST'))
        self.assertEqual(release['target_commitish'],SHA)
        self.assertTrue(release['draft'])
        for text in ('0.4.22','浏览器未验证',MAINTENANCE_SHARE_BASELINE,'14/15/16/17/19/20/21','发布时不重建'):
            self.assertIn(text,release['body'])
        for run in runs.values():self.assertIn(run['html_url'],release['body'])
        uploaded={urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['name'][0]:data
                  for path,method,data in writes[1:]}
        self.assertEqual(set(uploaded),ASSETS)
        for target in TARGETS:
            with zipfile.ZipFile(io.BytesIO(uploaded['vui-linux-'+target+'.zip'])) as archive:
                manifest=json.loads(archive.read('MANIFEST.json'))
            self.assertEqual((manifest['source_commit'],manifest['version'],manifest['targets']),(SHA,'0.4.22',[target]))

    def test_new_flag_cannot_promote_any_target_from_another_commit(self):
        for target in TARGETS:
            with self.subTest(target=target):self.promotion(tamper_target=target)

    def test_workflow_dispatch_binds_new_boolean_and_cli(self):
        workflow=Path('.github/workflows/publish.yml').read_text()
        self.assertIn('      maintenance_share_no_browser:\n',workflow)
        self.assertIn('MAINTENANCE_SHARE_NO_BROWSER: ${{ inputs.maintenance_share_no_browser }}',workflow)
        self.assertIn("if [[ \"$MAINTENANCE_SHARE_NO_BROWSER\" == 'true' ]]; then args+=(--maintenance-share-no-browser); fi",workflow)


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



class ParallelPublisherAdapterTests(unittest.TestCase):
    def test_current_022_parallel_mode_refuses_before_network(self):
        from scripts import publish_release as p
        argv=['publish_release.py','--commit',SHA,'--version','0.4.22',
              '--confirm','RELEASE 0.4.22 '+SHA,'--parallel-acceptance-no-browser']
        with patch('sys.argv',argv),patch.object(p,'request') as request,self.assertRaisesRegex(p.ReleaseGateError,'after 0.4.22'):
            p.main()
        request.assert_not_called()

    def test_parallel_mode_is_exclusive_with_every_legacy_exception(self):
        from scripts import publish_release as p
        flags=('installer-only','ca-trust','install-interaction','startup','ui-redesign',
               'tls-certificate','remote-maintenance','simple-node','maintenance-180',
               'netlink-service','simple-operations','maintenance-share')
        for flag in flags:
            argv=['publish_release.py','--commit',SHA,'--version','0.4.23',
                  '--confirm','RELEASE 0.4.23 '+SHA,'--parallel-acceptance-no-browser','--'+flag+'-no-browser']
            with self.subTest(flag=flag),patch('sys.argv',argv),patch.object(p,'request') as request,\
                    self.assertRaisesRegex(p.ReleaseGateError,'mutually exclusive'):
                p.main()
            request.assert_not_called()

    def test_parallel_selection_requires_short_install_workflow(self):
        from scripts import publish_release as p
        rows=[{'id':i+1,'path':'.github/workflows/'+name,'head_sha':SHA,
               'status':'completed','conclusion':'success'}for i,name in enumerate(sorted(p.REQUIRED))]
        with self.assertRaisesRegex(p.ReleaseGateError,'short-install.yml'):
            p.select_runs(rows,SHA,parallel_acceptance_no_browser=True)
        rows.append({'id':99,'path':'.github/workflows/short-install.yml','head_sha':SHA,
                     'status':'completed','conclusion':'success'})
        self.assertEqual(set(p.select_runs(rows,SHA,parallel_acceptance_no_browser=True)),p.REQUIRED|{'short-install.yml'})
        with self.assertRaisesRegex(p.ReleaseGateError,'mutually exclusive'):
            p.select_runs(rows,SHA,parallel_acceptance_no_browser=True,maintenance_share_no_browser=True)

    def test_parallel_job_validation_delegates_fixed_contract(self):
        from scripts import publish_release as p, parallel_release_policy as policy
        sentinel={'kit_files':{},'portable_bundles':{},'evidence':{}}
        with patch.object(policy,'validate_parallel_runs',return_value=sentinel) as validate:
            self.assertIs(p.validate_run_jobs({},SHA,parallel_acceptance_no_browser=True,parallel_version='0.4.23'),sentinel)
        args,kwargs=validate.call_args
        self.assertEqual(args,({},SHA,'0.4.23'))
        self.assertIs(kwargs['api'],p.request)
        self.assertIs(kwargs['download'],p.download_artifact)
        self.assertIs(kwargs['browser_skip_steps'],p.BROWSER_SKIP_STEPS)
        self.assertIn('Verify actual bundle under one CPU and 320 MiB cgroup',kwargs['required_other_steps']['release.yml'])
        self.assertIn('Run tests',kwargs['required_other_steps']['test.yml'])

    def promotion(self, existing=False):
        from scripts import publish_release as p, parallel_release_policy as policy
        fixture=ReleasePromotionTests();version='0.4.23'
        files=p.validate_kit(fixture.kit(commit=SHA,version=version),SHA,version)
        bundles={target:p.validate_target_artifact(fixture.portable(target,commit=SHA,version=version),SHA,version,target)
                 for target in TARGETS[1:]}
        checks={name:{'id':i+1,'path':'.github/workflows/'+name,'head_sha':SHA,'run_attempt':1,
                     'status':'completed','conclusion':'success','html_url':'https://example.test/run/'+str(i+1)}
                for i,name in enumerate(sorted(p.REQUIRED|{'short-install.yml'}))}
        writes=[];events=[]
        release={'id':990,'draft':True,'html_url':'https://example.test/release/990',
                 'upload_url':'https://uploads.github.com/repos/ForceMind/V-UI/releases/990/assets{?name,label}'}
        def request(path,method='GET',data=None,binary=False):
            if method!='GET':
                events.append('write');writes.append((path,method,data))
                if path=='releases':return release
                if path.startswith('https://uploads.github.com/'):
                    return {'digest':'sha256:'+hashlib.sha256(data).hexdigest()}
                raise AssertionError((path,method))
            if path=='':return {'default_branch':'main'}
            if path=='git/ref/heads/main':return {'object':{'sha':SHA}}
            if path.startswith('actions/runs?'):return {'workflow_runs':list(checks.values())}
            if path.startswith(('git/ref/tags/','releases/tags/')):
                if existing:return {'already_exists':True}
                raise urllib.error.HTTPError(path,404,'absent',{},None)
            raise AssertionError(path)
        argv=['publish_release.py','--commit',SHA,'--version',version,'--confirm','RELEASE '+version+' '+SHA,
              '--parallel-acceptance-no-browser']
        result={'kit_files':files,'portable_bundles':bundles,'evidence':{}}
        with patch('sys.argv',argv),patch.dict('os.environ',{'GITHUB_REPOSITORY':'ForceMind/V-UI','GITHUB_EVENT_NAME':'workflow_dispatch'}),\
                patch.object(p.Path,'read_text',return_value=version+'\n'),\
                patch.object(p.subprocess,'check_output',return_value=SHA+'\n'),\
                patch.object(p.subprocess,'run',side_effect=AssertionError('Promotion must never build')),\
                patch.object(policy,'validate_source_scope',return_value={'product_commit':'b'*40}) as scope,\
                patch.object(policy,'validate_latest_published') as latest,\
                patch.object(p,'validate_run_jobs',return_value=result) as jobs,\
                patch.object(policy,'revalidate_runs',side_effect=lambda *a:events.append('revalidate')) as recheck,\
                patch.object(p,'download_artifact',side_effect=AssertionError('Validated material must not be redownloaded')),\
                patch.object(p,'request',side_effect=request),patch('builtins.print'):
            if existing:
                with self.assertRaisesRegex(p.ReleaseGateError,'overwrite'):p.main()
            else:p.main()
            scope.assert_called_once_with(SHA,version);latest.assert_called_once_with(version,p.request)
            self.assertTrue(jobs.call_args.kwargs['parallel_acceptance_no_browser'])
            self.assertEqual(jobs.call_args.kwargs['parallel_version'],version)
        return writes,events

    def test_future_parallel_cli_promotes_only_verified_four_targets_without_build(self):
        writes,events=self.promotion()
        self.assertEqual(events[0],'revalidate')
        self.assertEqual(events[-1],'revalidate')
        self.assertEqual(events.count('revalidate'),2)
        self.assertEqual(writes[0][0:2],('releases','POST'))
        self.assertEqual(writes[0][2]['target_commitish'],SHA)
        self.assertIn('浏览器未验证',writes[0][2]['body'])
        uploaded={urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['name'][0]:body
                  for path,method,body in writes[1:]}
        self.assertEqual(set(uploaded),ASSETS)
        for target in TARGETS:
            with zipfile.ZipFile(io.BytesIO(uploaded['vui-linux-'+target+'.zip']))as archive:
                manifest=json.loads(archive.read('MANIFEST.json'))
            self.assertEqual((manifest['source_commit'],manifest['version'],manifest['targets']),(SHA,'0.4.23',[target]))

    def test_existing_parallel_version_never_writes_a_release(self):
        writes,events=self.promotion(existing=True)
        self.assertEqual(writes,[])
        self.assertEqual(events,[])

    def test_workflow_binds_parallel_flag_and_defaults_closed(self):
        text=Path('.github/workflows/publish.yml').read_text()
        self.assertIn('      parallel_acceptance_no_browser:\n',text)
        self.assertIn('          PARALLEL_ACCEPTANCE_NO_BROWSER: ${{ inputs.parallel_acceptance_no_browser }}',text)
        self.assertIn('args+=(--parallel-acceptance-no-browser)',text)
        section=text.split('      parallel_acceptance_no_browser:\n',1)[1].split('      simple_operations_no_browser:',1)[0]
        self.assertIn('        default: false',section)


if __name__=='__main__':
    unittest.main()

"""Pure SDK handoff tests; no verifier, installer or Root program is executed."""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts import install_system as i


class InstallerOriginTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='vui-origin-bootstrap-')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.kit=self.root/'kit';self.kit.mkdir()
        self.code=b'raise SystemExit(0)\n'
        (self.kit/'install_ops_control.py').write_bytes(self.code)
        self.table=hashlib.sha256(self.code).hexdigest().encode()+b'  install_ops_control.py\n'
        (self.kit/'SHA256SUMS').write_bytes(self.table)
        self.args=argparse.Namespace(control_kit=self.kit,control_kit_sha=hashlib.sha256(self.table).hexdigest(),
            control_kit_local_target_only=False,archive_temp_dir=self.root,dry_run=False)
        self.snapshot=tempfile.TemporaryFile(dir=self.root);self.addCleanup(self.snapshot.close)
        self.snapshot.write(b'inert snapshot fixture');self.snapshot.flush()
    def call(self,callback):
        with patch.object(i.os,'geteuid',return_value=0),patch.object(i.subprocess,'run',side_effect=callback):
            return i.record_application_origin(self.args,self.snapshot)
    def test_verified_code_and_snapshot_use_held_fds_and_fixed_os_interpreter(self):
        seen=[]
        def command(argv,**kwargs):
            self.assertEqual(argv[:6],['/usr/bin/python3','-B','-I','-S','-c',argv[5]])
            code_fd=int(argv[6]);self.assertEqual(os.pread(code_fd,100,0),self.code)
            self.assertEqual(kwargs['pass_fds'],(self.snapshot.fileno(),code_fd))
            self.assertEqual(argv[7],hashlib.sha256(self.code).hexdigest())
            self.assertEqual(argv[-2:],['--record-origin-fd',str(self.snapshot.fileno())])
            self.assertEqual(kwargs['cwd'],'/');self.assertNotIn('PYTHONPATH',kwargs['env'])
            self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
            self.assertIn('hashlib.sha256(raw).hexdigest()==expected',argv[5])
            self.assertIn('sys.argv=',argv[5]);seen.append(code_fd)
            return subprocess.CompletedProcess(argv,0)
        self.assertTrue(self.call(command));self.assertEqual(len(seen),1)
        with self.assertRaises(OSError):os.fstat(seen[0])
    def test_modified_sdk_or_wrong_external_table_pin_never_runs_code(self):
        with patch.object(i.subprocess,'run') as command,patch.object(i.os,'geteuid',return_value=0):
            self.args.control_kit_sha='b'*64
            with self.assertRaises(i.InstallError):i.record_application_origin(self.args,self.snapshot)
            command.assert_not_called()
            self.args.control_kit_sha=hashlib.sha256(self.table).hexdigest()
            (self.kit/'install_ops_control.py').write_bytes(b'untrusted replacement')
            with self.assertRaises(i.InstallError):i.record_application_origin(self.args,self.snapshot)
            command.assert_not_called()
    def test_legacy_path_creates_no_origin_and_partial_options_fail_closed(self):
        empty=argparse.Namespace()
        with patch.object(i.subprocess,'run') as command:
            self.assertFalse(i.record_application_origin(empty,self.snapshot));command.assert_not_called()
        for attributes in ({'control_kit':self.kit},{'control_kit_sha':'a'*64},
                           {'control_kit_local_target_only':True}):
            args=argparse.Namespace(**attributes)
            with patch.object(i.os,'geteuid',return_value=0),self.assertRaises(i.InstallError):
                i.record_application_origin(args,self.snapshot)
    def test_dry_run_or_nonroot_never_passes_snapshot_to_root_helper(self):
        for dry,uid in ((True,0),(False,12345)):
            self.args.dry_run=dry
            with patch.object(i.os,'geteuid',return_value=uid),patch.object(i.subprocess,'run') as command:
                with self.assertRaises(i.InstallError):i.record_application_origin(self.args,self.snapshot)
                command.assert_not_called()
    def test_helper_failure_is_fixed_chinese_and_never_reports_origin_success(self):
        def fail(*args,**kwargs):raise subprocess.TimeoutExpired('private-code-token',300)
        with self.assertRaises(i.InstallError) as error:self.call(fail)
        self.assertIn('尚未执行候选准备',str(error.exception))
        self.assertNotIn('private-code-token',str(error.exception))
    def test_remote_partial_sdk_mode_is_explicitly_forwarded(self):
        self.args.control_kit_local_target_only=True
        def command(argv,**kwargs):
            self.assertEqual(argv[-1],'--local-target-only')
            return subprocess.CompletedProcess(argv,0)
        self.assertTrue(self.call(command))
    def test_origin_handoff_occurs_after_dry_run_return_before_first_user_and_stage(self):
        source=Path(i.__file__).read_text();tree=ast.parse(source)
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_verified')
        lines=source.splitlines()
        body='\n'.join(lines[function.lineno-1:function.end_lineno])
        self.assertLess(body.index('if args.dry_run:'),body.index('record_application_origin(args,snapshot)'))
        self.assertLess(body.index('record_application_origin(args,snapshot)'),body.index('account=create_service_user()'))
        self.assertLess(body.index('record_application_origin(args,snapshot)'),body.index('as_service('))

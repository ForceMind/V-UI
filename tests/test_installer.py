"""The root installer fails before destructive work on wrong inputs or foreign services."""
import argparse
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import install_system as installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
    def args(self, **changes):
        data=dict(domain='panel.example.test',email='a@example.test',admin='admin',port=8443,
                  bind='0.0.0.0',cert=None,key=None,accept_terms=True,node_port=10443,
                  open_firewall='no',assume_external_ports_open=True,dry_run=False,
                  upgrade=False,health_ca=None)
        data.update(changes);return argparse.Namespace(**data)
    def test_domains_flags_terms_and_ports_are_strict(self):
        for value in ('127.0.0.1','*.example.com','https://example.com','a.example:8443','--hook evil', 'a/example.com'):
            with self.subTest(value=value), self.assertRaises(installer.InstallError):
                installer.validate_options(self.args(domain=value))
        for changes in ({'accept_terms':False},{'port':443},{'email':'x@y.test\n--hook'}, {'admin':'$(id)'}, {'cert':'a','key':None}):
            with self.subTest(changes=changes), self.assertRaises(installer.InstallError):
                installer.validate_options(self.args(**changes))
        args=self.args(domain='PANEL.example.test');installer.validate_options(args)
        self.assertEqual(args.domain,'panel.example.test')
    def test_port_conflict_does_not_stop_listener(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));sock.listen()
            with self.assertRaises(installer.InstallError):installer.check_port(sock.getsockname()[1],'127.0.0.1')
            self.assertGreater(sock.fileno(),0)
    def test_systemd_units_run_no_root_and_no_shell(self):
        units=installer.unit_files({'ipv6':True,'bootstrap_python':'/opt/vui/python'})
        for name in ('v-ui.service','v-ui-http01.service'):
            self.assertIn('User=v-ui',units[name]);self.assertIn('NoNewPrivileges=true',units[name])
            self.assertNotIn('User=root',units[name]);self.assertNotIn('/bin/sh',units[name])
        self.assertIn('ListenStream=0.0.0.0:80',units['v-ui-http01.socket'])
        self.assertIn('ListenStream=[::]:80',units['v-ui-http01.socket'])
        self.assertNotIn('ListenStream=[::]:80',installer.unit_files({'ipv6':False})['v-ui-http01.socket'])
    def test_foreign_paths_and_units_are_not_adopted(self):
        root=self.root/'instance';control=self.root/'control';configdir=self.root/'cfg';unitdir=self.root/'units'
        unitdir.mkdir();root.mkdir()
        with patch.multiple(installer,ROOT=root,CONTROL=control,CONFIG_DIR=configdir,CONFIG=configdir/'service.json'), \
             patch.object(installer.service_support,'SYSTEMD_DIR',unitdir):
            with self.assertRaises(installer.InstallError):installer.check_reserved(False,'systemd')
            root.rmdir();(unitdir/'v-ui.service').write_text('not ours')
            with patch.object(installer.pwd,'getpwnam',side_effect=KeyError):
                with self.assertRaises(installer.InstallError):installer.check_reserved(False,'systemd')
            self.assertEqual((unitdir/'v-ui.service').read_text(),'not ours')
    def test_bundle_digest_and_archive_paths_are_checked_before_execution(self):
        path=self.root/'bundle.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('../outside','x')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(installer.InstallError):installer.verify_archive(path,'0'*64)
        with self.assertRaises(installer.InstallError):installer.verify_archive(path,sha)
        self.assertFalse((self.root.parent/'outside').exists())
    def test_hash_correct_bundle_must_have_complete_manifest_and_controller(self):
        files={'scripts/deploy.py':b'# controller','app/release_tools.py':b'# api','deploy/system_launcher.py':b'# launcher'}
        manifest={'schema':1,'kind':'release','release_id':'0.3.1-fixture','platform':'linux-multi-cpython312',
                  'targets':[installer.platform_support.target_key()],'files':{
            name:{'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':0o600} for name,data in files.items()}}
        path=self.root/'bundle.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('MANIFEST.json',json.dumps(manifest))
            for name,data in files.items():z.writestr(name,data)
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        raw,archive,meta=installer.verify_archive(path,sha);self.assertEqual(meta,manifest);archive.close();raw.close()
        with zipfile.ZipFile(path,'a') as z:z.writestr('extra','unexpected')
        with self.assertRaises(installer.InstallError):installer.verify_archive(path,hashlib.sha256(path.read_bytes()).hexdigest())
    def test_root_file_updates_refuse_symlink_ancestors(self):
        linked=self.root/'linked';linked.symlink_to('/tmp',target_is_directory=True)
        with self.assertRaises(installer.InstallError):installer.write_root_file(linked/'test',b'never write')
    def test_interrupted_first_install_only_stops_present_managed_units(self):
        units=self.root/'units';units.mkdir()
        with patch.object(installer,'UNIT_DIR',units), patch.object(installer,'command') as run:
            installer.stop_existing_units(installer.UNITS)
            run.assert_not_called()
            (units/'v-ui-http01.socket').write_text(installer.MARKER)
            installer.stop_existing_units(installer.UNITS)
            self.assertEqual(run.call_args.args[0],['systemctl','stop','v-ui-http01.socket'])

    def test_public_control_directories_are_readable_under_private_umask(self):
        config=self.root/'config';control=self.root/'control';private=self.root/'private'
        private.mkdir(mode=0o700)
        previous=os.umask(0o077)
        try:
            with patch.multiple(installer,CONFIG_DIR=config,CONTROL=control):
                installer.write_root_file(config/'service.json',b'{}')
                installer.write_root_file(control/'launcher.py',b'# launcher')
                installer.write_root_file(private/'secret',b'private',mode=0o600)
            for parent in (config,control):
                self.assertEqual(stat.S_IMODE(parent.stat().st_mode),0o755)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE((private/'secret').stat().st_mode),0o600)
        finally:os.umask(previous)

    def test_no_password_argument_or_root_package_exec(self):
        source=Path(installer.__file__).read_text()
        self.assertNotIn("add_argument('--password'",source)
        self.assertIn('os.setuid(account.pw_uid)',source)
        self.assertIn("with open('/dev/tty'",source)
        self.assertNotIn('shell=True',source)

if __name__=='__main__':unittest.main()

"""Offline archive, data recovery and atomic version-selection failure tests."""
from contextlib import ExitStack, redirect_stdout
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from app import release_tools as tools
from scripts.vendor_frontend import extract_selected


class ReleaseToolsTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-release-unit-')))
        self.source=self.root/'source';self.source.mkdir(mode=0o700)
        (self.source/'main.py').write_text('print("fixture")\n')
        self.metadata={'kind':'release','platform':tools.PLATFORM,'release_id':'fixture-one','targets':[tools.target_key()]}

    def archive(self, source=None, identity='one'):
        target=self.root/(identity+'.zip')
        checksum=tools.create_archive(source or self.source,target,self.metadata)
        return target,checksum

    def prepared(self, identity):
        self.metadata['release_id']=identity
        archive,checksum=self.archive(identity=identity)
        release=self.root/'releases'/identity;release.mkdir(parents=True,mode=0o700)
        tools.unpack_verified(archive,checksum,release/'payload')
        runtime=release/'runtime'/'python'/'bin';runtime.mkdir(parents=True)
        python=runtime/'python3';python.write_text('#! /bin/sh\n');python.chmod(0o700)
        tools.atomic_json(release/'READY.json',{'manifest_sha256':tools.digest((release/'payload'/MANIFEST).read_bytes()),
            'runtime_key':tools.target_key(),'runtime_tree_sha256':tools.runtime_tree_digest(release/'runtime')})
        return release

    def fixture_database(self):
        data=self.root/'data';data.mkdir(mode=0o700)
        with sqlite3.connect(data/'v-ui.db') as db:
            db.executescript('CREATE TABLE users(id INTEGER, username TEXT); CREATE TABLE inbounds(id INTEGER, remark TEXT);'
                'CREATE TABLE admin_sessions(token_hash TEXT); CREATE TABLE subscription_grants(revoked INTEGER);'
                "INSERT INTO users VALUES(1,'fixture'); INSERT INTO inbounds VALUES(1,'preserved');"
                "INSERT INTO admin_sessions VALUES('digest-only'); INSERT INTO subscription_grants VALUES(0);")
        (data/'routing.json').write_text('{"mode":"direct"}')
        return data

    def crafted(self, pairs):
        target=self.root/'crafted.zip'
        with zipfile.ZipFile(target,'w') as archive:
            for name,content in pairs: archive.writestr(name,content)
        return target,tools.digest(target.read_bytes())

    def test_manifest_hash_roundtrip_and_private_modes(self):
        archive,checksum=self.archive()
        target=self.root/'extract';meta=tools.unpack_verified(archive,checksum,target)
        self.assertEqual(tools.verify_payload(target),meta)
        self.assertEqual((target/'main.py').stat().st_mode&0o777,0o600)
        self.assertEqual(archive.stat().st_mode&0o777,0o600)

    def test_wrong_archive_digest_fails_before_extraction(self):
        archive,_=self.archive();target=self.root/'extract'
        with self.assertRaises(tools.ReleaseError):tools.unpack_verified(archive,'0'*64,target)
        self.assertFalse(target.exists())

    def test_payload_tamper_and_extra_file_are_detected(self):
        archive,checksum=self.archive();target=self.root/'extract'
        tools.unpack_verified(archive,checksum,target)
        (target/'main.py').write_text('changed')
        with self.assertRaises(tools.ReleaseError):tools.verify_payload(target)
        (target/'main.py').write_bytes((self.source/'main.py').read_bytes())
        (target/'unexpected').write_text('extra')
        with self.assertRaises(tools.ReleaseError):tools.verify_payload(target)

    def test_world_readable_payload_is_rejected(self):
        archive,checksum=self.archive();target=self.root/'extract'
        tools.unpack_verified(archive,checksum,target);(target/'main.py').chmod(0o644)
        with self.assertRaises(tools.ReleaseError):tools.verify_payload(target)

    def test_unsafe_archive_names_and_symlinks_are_rejected(self):
        for name in ('../escape','/absolute','a//b','a/../b','a\\b','a\x7fb'):
            with self.subTest(name=name):
                archive,checksum=self.crafted([('MANIFEST.json','{}'),(name,'x')])
                with self.assertRaises(tools.ReleaseError):tools.unpack_verified(archive,checksum,self.root/'extract')
                archive.unlink()
        info=zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK|0o777)<<16
        archive,checksum=self.crafted([('MANIFEST.json','{}'),(info,'/tmp')])
        with self.assertRaises(tools.ReleaseError):tools.unpack_verified(archive,checksum,self.root/'extract')
        self.assertFalse((self.root/'extract').exists())

    def test_invalid_manifests_fail_closed(self):
        for manifest in ('[]','{}','{"schema":1,"files":{}}'):
            with self.subTest(manifest=manifest):
                archive,checksum=self.crafted([('MANIFEST.json',manifest),('extra','x')])
                with self.assertRaises(tools.ReleaseError):tools.unpack_verified(archive,checksum,self.root/'extract')
                archive.unlink()

    def test_payload_link_and_insecure_root_are_refused(self):
        (self.source/'link').symlink_to('/tmp')
        with self.assertRaises(tools.ReleaseError):self.archive()
        self.source.chmod(0o755)
        with self.assertRaises(tools.ReleaseError):tools.private_root(self.source)

    def test_backup_restore_preserves_nodes_but_revokes_access(self):
        data=self.fixture_database();destination=self.root/'backup.zip'
        checksum=tools.backup(self.root,destination)
        (data/'routing.json').write_text('modified')
        with sqlite3.connect(data/'v-ui.db') as db:db.execute("UPDATE inbounds SET remark='changed'")
        tools.restore(self.root,destination,checksum)
        self.assertEqual((data/'routing.json').read_text(),'{"mode":"direct"}')
        with sqlite3.connect(data/'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0],'preserved')
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone()[0],1)
        self.assertEqual(len(list((self.root/'recovery').glob('before-*'))),1)

    def test_restore_checksum_or_root_mismatch_leaves_original_untouched(self):
        data=self.fixture_database();destination=self.root/'backup.zip';checksum=tools.backup(self.root,destination)
        before=(data/'v-ui.db').read_bytes()
        with self.assertRaises(tools.ReleaseError):tools.restore(self.root,destination,'0'*64)
        self.assertEqual(before,(data/'v-ui.db').read_bytes())
        other=self.root/'other';other.mkdir(mode=0o700)
        with self.assertRaises(tools.ReleaseError):tools.restore(other,destination,checksum)
        self.assertFalse((other/'data').exists())

    def test_operations_refuse_active_service(self):
        self.fixture_database()
        with tools.lease(self.root/'.panel.lease'):
            with self.assertRaises(tools.ReleaseError):tools.backup(self.root,self.root/'backup.zip')
            with self.assertRaises(tools.ReleaseError):tools.activate(self.root,'fixture')

    def test_failed_restore_rename_restores_original(self):
        data=self.fixture_database();archive=self.root/'backup.zip';checksum=tools.backup(self.root,archive)
        (data/'routing.json').write_text('latest-original')
        replace=os.replace
        def fail_new_data(source,destination):
            if str(source).startswith(str(self.root/'.restore-')) and Path(source).name=='data':raise OSError('injected')
            return replace(source,destination)
        with patch.object(tools.os,'replace',side_effect=fail_new_data):
            with self.assertRaises(tools.ReleaseError):tools.restore(self.root,archive,checksum)
        self.assertEqual((data/'routing.json').read_text(),'latest-original')
        self.assertFalse((self.root/'RESTORE_PENDING.json').exists())

    def test_interrupted_restore_journal_requires_explicit_recovery(self):
        data=self.fixture_database();recovery=self.root/'recovery';recovery.mkdir()
        old='recovery/before-fixture';os.replace(data,self.root/old)
        data.mkdir(mode=0o700);(data/'marker').write_text('partial')
        tools.atomic_json(self.root/'RESTORE_PENDING.json',{'old':old})
        with self.assertRaises(tools.ReleaseError):tools.backup(self.root,self.root/'backup.zip')
        tools.recover_restore(self.root)
        self.assertTrue((data/'v-ui.db').is_file());self.assertFalse((data/'marker').exists())
        self.assertTrue(list(recovery.glob('interrupted-*')))

    def test_prepared_activate_and_code_rollback_leave_data_alone(self):
        self.prepared('version-a');self.prepared('version-b');data=self.fixture_database()
        with patch.object(tools,'health_check'):
            tools.activate(self.root,'version-a');tools.activate(self.root,'version-b')
            self.assertEqual(tools.active(self.root)[1]['previous_id'],'version-a')
            tools.activate(self.root,'version-a')
        self.assertEqual(tools.active(self.root)[1]['release_id'],'version-a')
        self.assertEqual((data/'routing.json').read_text(),'{"mode":"direct"}')

    def test_candidate_health_failure_preserves_current_pointer(self):
        self.prepared('version-a');self.prepared('version-b')
        with patch.object(tools,'health_check'):tools.activate(self.root,'version-a')
        before=(self.root/'CURRENT.json').read_bytes()
        with patch.object(tools,'health_check',side_effect=tools.ReleaseError('candidate failed')):
            with self.assertRaises(tools.ReleaseError):tools.activate(self.root,'version-b')
        self.assertEqual(before,(self.root/'CURRENT.json').read_bytes())

    def test_frontend_integrity_is_checked_before_member_extraction(self):
        data=b'not-a-tar';pin={'integrity':'sha512-bad','files':{}}
        with self.assertRaises(ValueError):extract_selected(data,pin,self.root/'assets')
        self.assertFalse((self.root/'assets').exists())
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            info=tarfile.TarInfo('package/file.js');info.size=3;archive.addfile(info,io.BytesIO(b'abc'))
        data=output.getvalue();pin={'integrity':'sha512-'+base64.b64encode(hashlib.sha512(data).digest()).decode(),
                                 'files':{'package/file.js':'library.js'}}
        target=self.root/'assets'
        proof=extract_selected(data,pin,target)
        self.assertEqual(proof['library.js'],hashlib.sha256(b'abc').hexdigest())

    def test_portable_runtime_allows_internal_relative_symlink_but_blocks_escape(self):
        good=self.root/'good-runtime.tar.gz'
        with tarfile.open(good,'w:gz') as archive:
            directory=tarfile.TarInfo('python/bin');directory.type=tarfile.DIRTYPE;directory.mode=0o755
            archive.addfile(directory)
            target=tarfile.TarInfo('python/bin/python3.12');target.size=3;target.mode=0o755
            archive.addfile(target,io.BytesIO(b'bin'))
            link=tarfile.TarInfo('python/bin/python3');link.type=tarfile.SYMTYPE;link.linkname='python3.12'
            archive.addfile(link)
            relative=tarfile.TarInfo('python/bin/python');relative.type=tarfile.SYMTYPE;relative.linkname='../bin/python3.12'
            archive.addfile(relative)
        destination=self.root/'runtime-good'
        tools.extract_runtime(good,destination)
        self.assertTrue((destination/'python/bin/python3').is_symlink())
        self.assertEqual((destination/'python/bin/python3').resolve(),(destination/'python/bin/python3.12').resolve())

        bad=self.root/'bad-runtime.tar.gz'
        with tarfile.open(bad,'w:gz') as archive:
            directory=tarfile.TarInfo('python/bin');directory.type=tarfile.DIRTYPE;archive.addfile(directory)
            link=tarfile.TarInfo('python/bin/python3');link.type=tarfile.SYMTYPE;link.linkname='../../outside'
            archive.addfile(link)
        with self.assertRaises(tools.ReleaseError):
            tools.extract_runtime(bad,self.root/'runtime-bad')
        self.assertFalse((self.root/'runtime-bad').exists())

    def test_release_frontend_is_local_and_template_changes_fail_closed(self):
        from scripts.prepare_frontend import prepare
        source=Path(__file__).resolve().parents[1]
        target=self.root/'prepared-ui';(target/'web/js').mkdir(parents=True)
        shutil.copyfile(source/'web/index.html',target/'web/index.html')
        shutil.copyfile(source/'web/js/app.js',target/'web/js/app.js')
        prepare(target)
        page=(target/'web/index.html').read_text()
        self.assertNotIn('https://unpkg.com',page)
        self.assertNotIn('https://cdnjs.cloudflare.com',page)
        self.assertIn('vendor/vue.js',page)
        self.assertIn("security: 'tls'",(target/'web/js/app.js').read_text())
        xray=re.search(r'<el-alert\s+v-if="newInbound.core === \'xray\'"[^>]*>',page)
        self.assertIsNotNone(xray)
        self.assertIn('title="Xray REALITY 尚未验收，不能公开导出；已验收的 sing-box VLESS/direct TCP/REALITY/Vision 仅限 docs/COMPATIBILITY.md 中列明的范围。"',xray[0])
        self.assertIn('type="warning"',xray[0])
        sing_box=re.search(r'<el-alert\s+v-else-if="newInbound.core === \'sing-box\' && newInbound.profile.transport === \'direct\'"[^>]*>',page)
        self.assertIsNotNone(sing_box)
        self.assertIn('type="success"',sing_box[0])
        self.assertIn('title="REALITY UUID、私钥和 Short ID 保留在服务器，编辑页面不回显；空输入保持已有值。新建自动生成 X25519 密钥对，客户端导出仅包含所需 UUID、公钥和 Short ID。"',sing_box[0])
        self.assertNotIn('当前已验证导出仅覆盖 sing-box VLESS/TCP/TLS',page)
        self.assertNotIn('REALITY 在本版未验收',page)
        with self.assertRaises(ValueError):prepare(target)

    def test_generated_bundle_manifest_describes_bounded_cumulative_exports(self):
        source=Path(__file__).resolve().parents[1]
        # Run the real builder/prepare/archive path with temporary source and
        # mocked downloads. This fixture is never installed or executed.
        with patch.object(sys,'path',[str(source/'scripts'),*sys.path]),patch.object(sys,'dont_write_bytecode',True):
            from scripts import build_bundle
        tracked=('main.py','VERSION','requirements-runtime.txt','web/index.html',
                 'web/js/app.js','docs/COMPATIBILITY.md')
        for name in tracked:
            destination=self.source/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,destination)
        commit='a'*40;target=tools.target_key();bundle=self.root/'generated.zip'
        with ExitStack() as stack:
            previous_umask=os.umask(0o077);stack.callback(os.umask,previous_umask)
            stack.enter_context(patch.object(build_bundle,'ROOT',self.source))
            stack.enter_context(patch.object(sys,'argv',['build_bundle.py',str(bundle),'--source-commit',commit,'--target',target]))
            stack.enter_context(patch.object(build_bundle.subprocess,'check_output',side_effect=[commit+'\n',('\0'.join(tracked)+'\0').encode()]))
            stack.enter_context(patch.object(build_bundle.subprocess,'run'))
            for name in ('fetch_frontend','fetch_cores','fetch_runtimes','core_sources','wheel_lock'):
                stack.enter_context(patch.object(build_bundle,name))
            stack.enter_context(redirect_stdout(io.StringIO()))
            build_bundle.main()
        checksum=bundle.with_suffix('.zip.sha256').read_text().split()[0]
        manifest=tools.unpack_verified(bundle,checksum,self.root/'generated-payload')
        self.assertEqual(manifest['protocol_profile'],
            'bounded cumulative sing-box exports (including VLESS REALITY/Vision); see docs/COMPATIBILITY.md for exact combinations and application UDP limits')
        self.assertEqual(manifest['source_commit'],commit)
        self.assertEqual(manifest['version'],(source/'VERSION').read_text().strip())
        self.assertEqual(manifest['targets'],[target])
        self.assertIn('docs/COMPATIBILITY.md',manifest['files'])
        self.assertEqual(tools.verify_payload(self.root/'generated-payload'),manifest)

    def test_selected_environment_rejects_root_and_unknown_arch(self):
        with patch.object(tools.platform,'system',return_value='Linux'),patch.object(tools.platform,'machine',return_value='x86_64'),patch.object(tools.os,'geteuid',return_value=0):
            with self.assertRaises(tools.ReleaseError):tools.supported_environment()
        with patch.object(tools.platform,'machine',return_value='mips64'):
            with self.assertRaises(tools.ReleaseError):tools.target_key()

MANIFEST=tools.MANIFEST
if __name__=='__main__':unittest.main()

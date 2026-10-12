"""Bounded archive I/O, immutable verification snapshots and cleanup regressions."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools
from scripts import install_system as installer


class ArchiveStreamingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.payload = self.root / 'source'; self.payload.mkdir(mode=0o700)
        for name in ('scripts/deploy.py', 'app/release_tools.py', 'deploy/system_launcher.py'):
            path = self.payload / name; path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'# fixture\n')
        # Larger than one stream buffer, small enough to run in normal unit CI.
        (self.payload / 'large.bin').write_bytes(b'x' * (tools.STREAM_CHUNK * 3 + 17))
        self.meta = {'kind': 'release', 'platform': tools.PLATFORM,
                     'release_id': 'stream-fixture', 'targets': [tools.target_key()]}
        self.bundle = self.root / 'bundle.zip'
        self.sha = tools.create_archive(self.payload, self.bundle, self.meta)

    def verify_install(self):
        snapshot, archive, meta = installer.verify_archive(self.bundle, self.sha)
        self.stack.callback(snapshot.close); self.stack.callback(archive.close)
        return snapshot, archive, meta

    def test_pack_verify_unpack_and_hash_do_not_read_whole_files_or_members(self):
        original = zipfile.ZipFile.read
        def bounded_read(archive, name, *args, **kwargs):
            self.assertEqual(name, tools.MANIFEST, 'Only the size-capped manifest may be read whole')
            return original(archive, name, *args, **kwargs)
        member_read = zipfile.ZipExtFile.read
        def bounded_member_read(member, size=-1):
            if member.name != tools.MANIFEST:
                self.assertGreater(size, 0)
                self.assertLessEqual(size, tools.STREAM_CHUNK)
            return member_read(member, size)
        path_open = Path.open
        case = self
        class BoundedSource:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def read(self, size=-1):
                case.assertGreater(size, 0)
                case.assertLessEqual(size, tools.STREAM_CHUNK)
                return self.handle.read(size)
        def open_source(path, *args, **kwargs):
            handle = path_open(path, *args, **kwargs)
            return BoundedSource(handle) if path == self.bundle else handle
        with patch.object(Path, 'open', open_source), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('Whole-file read')), \
             patch.object(zipfile.ZipFile, 'read', bounded_read), \
             patch.object(zipfile.ZipExtFile, 'read', bounded_member_read):
            second = self.root / 'second.zip'
            digest = tools.create_archive(self.payload, second, self.meta)
            self.assertEqual(tools.file_digest(second), digest)
            snapshot, _, _ = self.verify_install()
            self.assertEqual(stat.S_IMODE(os.fstat(snapshot.fileno()).st_mode), 0o600)
            target = self.root / 'extract'
            manifest = tools.unpack_verified(self.bundle, self.sha, target)
            self.assertEqual(tools.verify_payload(target), manifest)

    def test_installer_snapshot_survives_original_overwrite_and_removal(self):
        snapshot, archive, _ = self.verify_install()
        self.bundle.write_bytes(b'replaced after checksum'); self.bundle.unlink()
        snapshot.seek(0)
        self.assertEqual(tools.stream_digest(snapshot)[0], self.sha)
        with archive.open('large.bin') as member:
            self.assertEqual(tools.stream_digest(member)[0], tools.file_digest(self.payload / 'large.bin'))

    def test_unpack_uses_snapshot_when_source_changes_after_hashing(self):
        original = zipfile.ZipFile
        def change_original(*args, **kwargs):
            self.bundle.write_bytes(b'changed during validation')
            return original(*args, **kwargs)
        with patch.object(tools.zipfile, 'ZipFile', side_effect=change_original):
            tools.unpack_verified(self.bundle, self.sha, self.root / 'extract')
        self.assertEqual(tools.file_digest(self.root / 'extract/large.bin'),
                         tools.file_digest(self.payload / 'large.bin'))

    def test_archive_size_limit_boundary_and_snapshot_cleanup(self):
        size = self.bundle.stat().st_size
        for module in (tools, installer):
            for limit in (size, size - 1):
                with self.subTest(module=module.__name__, limit=limit):
                    handles = []; original = tempfile.TemporaryFile
                    def track(*args, **kwargs):
                        handle = original(*args, **kwargs); handles.append(handle); return handle
                    with patch.object(module, 'MAX_ARCHIVE', limit), \
                         patch.object(module.tempfile, 'TemporaryFile', side_effect=track):
                        if module is tools:
                            def verify():
                                with tools.verified_snapshot(self.bundle, self.sha): pass
                        else:
                            def verify():
                                snapshot, archive, _ = installer.verify_archive(self.bundle, self.sha)
                                archive.close(); snapshot.close()
                        if limit == size: verify()
                        else:
                            with self.assertRaises(RuntimeError): verify()
                    self.assertTrue(handles)
                    self.assertTrue(all(handle.closed for handle in handles))

    def test_installer_closes_snapshot_and_archive_when_preflight_fails(self):
        snapshot, archive, meta = self.verify_install()
        with patch.object(installer, 'validate_options'), \
             patch.object(installer, 'verify_archive', return_value=(snapshot, archive, meta)), \
             patch.object(installer, 'check_platform', side_effect=installer.InstallError('preflight')):
            with self.assertRaisesRegex(installer.InstallError, 'preflight'):
                installer.install(type('Args', (), {'bundle': self.bundle, 'sha256': self.sha})())
        self.assertTrue(snapshot.closed); self.assertIsNone(archive.fp)

    def test_member_digest_failure_removes_partial_destination(self):
        manifest = {**self.meta, 'schema': 1, 'files': tools.files_in(self.payload)}
        manifest['files']['large.bin']['sha256'] = '0' * 64
        bad = self.root / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as archive:
            archive.writestr(tools.MANIFEST, json.dumps(manifest))
            for name in manifest['files']: archive.write(self.payload / name, name)
        target = self.root / 'extract'
        with self.assertRaisesRegex(tools.ReleaseError, 'Payload checksum'):
            tools.unpack_verified(bad, tools.file_digest(bad), target)
        self.assertFalse(target.exists())
        target.mkdir(); (target / 'keep').write_text('original')
        with self.assertRaises(tools.ReleaseError): tools.unpack_verified(bad, tools.file_digest(bad), target)
        self.assertEqual((target / 'keep').read_text(), 'original')

    def test_read_or_snapshot_write_failure_closes_file_and_leaves_no_payload(self):
        original = tempfile.TemporaryFile
        for module in (tools, installer):
            with self.subTest(module=module.__name__):
                handles = []
                def broken_file(*args, **kwargs):
                    handle = original(*args, **kwargs); handles.append(handle)
                    handle.write = lambda data: (_ for _ in ()).throw(OSError('disk full'))
                    return handle
                with patch.object(module.tempfile, 'TemporaryFile', side_effect=broken_file):
                    if module is tools:
                        with self.assertRaises(tools.ReleaseError):
                            tools.unpack_verified(self.bundle, self.sha, self.root / 'extract')
                    else:
                        with self.assertRaises(OSError): installer.verify_archive(self.bundle, self.sha)
                self.assertTrue(all(handle.closed for handle in handles))
                self.assertFalse((self.root / 'extract').exists())

    def test_root_file_writer_streams_and_preserves_atomic_failure(self):
        path = self.root / 'launcher.py'; path.write_bytes(b'old')
        class Broken(io.BytesIO):
            def read(self, size=-1):
                if self.tell(): raise OSError('read failure')
                return super().read(size)
        with self.assertRaises(OSError): installer.write_root_file(path, Broken(b'new'))
        self.assertEqual(path.read_bytes(), b'old')
        self.assertEqual(list(self.root.glob('.vui-*')), [])
        installer.write_root_file(path, io.BytesIO(b'new'))
        self.assertEqual(path.read_bytes(), b'new')

    def test_online_bootstrap_checksum_snippet_streams_and_rejects_tampering(self):
        import sys
        script = (Path(__file__).resolve().parents[1] / 'install.sh').read_text()
        marker = '''"$PYTHON" - "$WORK" "$BUNDLE" <<'PY'\n'''
        snippet = script.split(marker, 1)[1].split('\nPY\n', 1)[0]
        folder = self.root / 'online'; folder.mkdir()
        names = ('install_system.py', 'platform_support.py', 'firewall_support.py',
                 'service_support.py', 'vui-linux-x86_64-gnu.zip')
        checksums = []
        for name in names:
            path = folder / name; path.write_bytes(b'asset' * 300000)
            checksums.append(tools.file_digest(path) + '  ' + name)
        (folder / 'SHA256SUMS').write_text('\n'.join(checksums))
        with patch.object(sys, 'argv', ['-', str(folder), names[-1]]), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('Whole-file read')):
            exec(compile(snippet, 'install.sh checksum', 'exec'), {})
            self.assertEqual((folder / 'bundle.sha').read_text(), checksums[-1].split()[0])
            (folder / names[-1]).write_bytes(b'tampered')
            with self.assertRaisesRegex(SystemExit, 'checksum mismatch'):
                exec(compile(snippet, 'install.sh checksum', 'exec'), {})

    def test_unsafe_zip_metadata_is_rejected_by_both_verifiers(self):
        import struct
        import warnings
        for kind in ('duplicate', 'traversal', 'link', 'encrypted', 'expanded', 'manifest-size', 'member-size'):
            with self.subTest(kind=kind):
                bad = self.root / (kind + '.zip')
                manifest = {**self.meta, 'schema': 1, 'files': tools.files_in(self.payload)}
                if kind == 'member-size': manifest['files']['large.bin']['size'] += 1
                with warnings.catch_warnings(), zipfile.ZipFile(bad, 'w') as archive:
                    warnings.simplefilter('ignore', UserWarning)
                    archive.writestr(tools.MANIFEST, json.dumps(manifest))
                    for name in manifest['files']: archive.write(self.payload / name, name)
                    if kind == 'duplicate': archive.writestr('large.bin', b'x')
                    if kind == 'traversal': archive.writestr('../escape', b'x')
                    if kind == 'link':
                        info = zipfile.ZipInfo('link'); info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(info, b'/tmp')
                if kind in ('encrypted', 'expanded', 'manifest-size'):
                    raw = bytearray(bad.read_bytes()); offset = raw.index(b'PK\x01\x02')
                    if kind == 'encrypted': struct.pack_into('<H', raw, offset + 8, 1)
                    else: struct.pack_into('<I', raw, offset + 24, 1_100_000_001 if kind == 'expanded' else 4_000_001)
                    bad.write_bytes(raw)
                checksum = tools.file_digest(bad)
                with self.assertRaises(installer.InstallError): installer.verify_archive(bad, checksum, temp_dir=self.root)
                target = self.root / 'extract'
                with self.assertRaises(tools.ReleaseError): tools.unpack_verified(bad, checksum, target)
                self.assertFalse(target.exists())

    def test_release_snapshot_is_on_extraction_filesystem_and_installer_override_is_used(self):
        original = tempfile.TemporaryFile; directories = []
        def track(*args, **kwargs):
            directories.append(kwargs['dir']); return original(*args, **kwargs)
        with patch.object(tempfile, 'TemporaryFile', side_effect=track):
            tools.unpack_verified(self.bundle, self.sha, self.root / 'extract')
            snapshot, archive, _ = installer.verify_archive(self.bundle, self.sha, temp_dir=self.root)
            archive.close(); snapshot.close()
        self.assertEqual(directories, [self.root, self.root])

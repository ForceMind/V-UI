"""Advisory cache bounds must not weaken private archive integrity or I/O errors."""
import ast
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import release_tools
from scripts import install_system


class PrivateArchiveCacheTests(unittest.TestCase):
    def test_standalone_helpers_stay_identical(self):
        helpers = []
        for module in (release_tools, install_system):
            tree = ast.parse(Path(module.__file__).read_text())
            helpers.append(ast.dump(next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == '_PrivateArchiveFile')))
        self.assertEqual(*helpers)

    def test_writeback_precedes_advice_and_reads_discard_only_complete_pages(self):
        for module in (release_tools, install_system):
            with self.subTest(module=module.__name__), tempfile.TemporaryFile() as raw:
                events = []
                with patch.object(os, 'fsync', side_effect=lambda fd: events.append(('sync', fd))), \
                     patch.object(os, 'posix_fadvise', side_effect=lambda *args: events.append(('advice', *args))):
                    handle = module._PrivateArchiveFile(raw)
                    block = b'x' * (1024 * 1024)
                    for _ in range(7): handle.write(block)
                    self.assertEqual(events, [])
                    handle.write(block)
                    self.assertEqual([e[0] for e in events], ['sync', 'advice'])
                    handle.write(b'tail'); handle.finish_writes()
                    self.assertEqual([e[0] for e in events], ['sync', 'advice', 'sync', 'advice'])
                    events.clear(); handle.seek(17); handle.read_pending = 8 * 1024 * 1024
                    self.assertEqual(handle.read(2 * handle.page_size), b'x' * (2 * handle.page_size))
                    self.assertEqual(events, [('advice', raw.fileno(), 0,
                                              2 * handle.page_size, os.POSIX_FADV_DONTNEED)])
                    events.clear(); handle.read(7)
                    self.assertEqual(events, [])

    def test_unavailable_or_failed_hint_preserves_bytes_and_digest(self):
        for module in (release_tools, install_system):
            for advice in (None, lambda *a: (_ for _ in ()).throw(OSError('unsupported'))):
                with self.subTest(module=module.__name__, advice=advice), tempfile.TemporaryFile() as raw, \
                     patch.object(os, 'posix_fadvise', advice):
                    handle = module._PrivateArchiveFile(raw)
                    data = b'private snapshot' * 1000
                    handle.write(data); handle.finish_writes(); handle.seek(0)
                    self.assertEqual(hashlib.sha256(handle.read()).digest(), hashlib.sha256(data).digest())

    def test_writeback_failure_is_not_suppressed(self):
        for module in (release_tools, install_system):
            with self.subTest(module=module.__name__), tempfile.TemporaryFile() as raw:
                handle = module._PrivateArchiveFile(raw); handle.write(b'not yet persisted')
                with patch.object(os, 'fsync', side_effect=OSError('disk error')), \
                     patch.object(os, 'posix_fadvise') as advice:
                    with self.assertRaisesRegex(OSError, 'disk error'): handle.finish_writes()
                    advice.assert_not_called()

    def test_payload_verification_only_cools_immutable_install_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            names = ['wheels/x86_64-gnu/demo.whl', 'runtimes/x86_64-gnu.tar.gz',
                     'main.py', 'cores/x86_64/sing-box', 'data/v-ui.db',
                     'runtime/python/bin/python3', 'wheels/README.txt']
            for name in names:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            with patch.object(release_tools, 'file_digest', return_value='a' * 64) as digest:
                release_tools.files_in(root, cold_install_artifacts=True)
                cooled = {call.args[0].relative_to(root).as_posix()
                          for call in digest.call_args_list if call.kwargs['cold']}
                self.assertEqual(cooled, {names[0], names[1], names[3]})
                digest.reset_mock(); release_tools.files_in(root)
                self.assertTrue(all(not call.kwargs['cold'] for call in digest.call_args_list))

    def test_unpack_cools_only_release_packaging_and_preserves_manifest(self):
        for kind in ('release', 'backup'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); source = root / 'source'; source.mkdir()
                for name in ('wheels/test/demo.whl', 'runtimes/test.tar.gz', 'main.py', 'data/v-ui.db'):
                    path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'fixture')
                bundle = root / 'bundle.zip'
                sha = release_tools.create_archive(source, bundle, {'kind': kind})
                cooled = []; original = release_tools._PrivateArchiveFile.finish_writes
                def record(handle):
                    if isinstance(handle.handle.name, str): cooled.append(Path(handle.handle.name).name)
                    return original(handle)
                with patch.object(release_tools._PrivateArchiveFile, 'finish_writes', record):
                    result = release_tools.unpack_verified(bundle, sha, root / 'out')
                self.assertEqual(set(cooled), {'demo.whl', 'test.tar.gz'} if kind == 'release' else set())
                with patch.object(release_tools, 'file_digest', wraps=release_tools.file_digest) as digest:
                    self.assertEqual(result, release_tools.verify_payload(root / 'out'))
                    cooled_reads = {call.args[0].relative_to(root / 'out').as_posix()
                                    for call in digest.call_args_list if call.kwargs.get('cold')}
                self.assertEqual(cooled_reads, {'wheels/test/demo.whl', 'runtimes/test.tar.gz'}
                                 if kind == 'release' else set())

    def test_cold_digest_keeps_integrity_and_propagates_writeback_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'artifact.whl'; path.write_bytes(b'fixture' * 1024)
            self.assertEqual(release_tools.file_digest(path, cold=True), release_tools.file_digest(path))
            with patch.object(os, 'fsync', side_effect=OSError('writeback failed')):
                with self.assertRaisesRegex(OSError, 'writeback failed'):
                    release_tools.file_digest(path, cold=True)

    def test_writeback_failure_closes_snapshots_and_removes_partial_unpack(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = root / 'payload'; payload.mkdir()
            for name in ('scripts/deploy.py', 'app/release_tools.py', 'deploy/system_launcher.py',
                         'runtimes/test.tar.gz'):
                path = payload / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fake-controller-or-runtime')
            bundle = root / 'bundle.zip'
            sha = release_tools.create_archive(payload, bundle, {
                'kind': 'release', 'platform': release_tools.PLATFORM,
                'release_id': 'writeback-failure-fixture', 'targets': [release_tools.target_key()]})
            original = tempfile.TemporaryFile
            for module, fail_call in ((install_system, 1), (release_tools, 1), (release_tools, 2)):
                with self.subTest(module=module.__name__, fail_call=fail_call):
                    handles = []; calls = []
                    def track(*args, **kwargs):
                        handle = original(*args, **kwargs); handles.append(handle); return handle
                    def failing_sync(fd):
                        calls.append(fd)
                        if len(calls) == fail_call: raise OSError('injected writeback failure')
                    with patch.object(tempfile, 'TemporaryFile', side_effect=track), \
                         patch.object(os, 'fsync', side_effect=failing_sync):
                        if module is install_system:
                            with self.assertRaisesRegex(OSError, 'injected writeback'):
                                module.verify_archive(bundle, sha, temp_dir=root)
                        else:
                            with self.assertRaises(module.ReleaseError):
                                module.unpack_verified(bundle, sha, root / 'partial')
                    self.assertEqual(len(calls), fail_call)
                    self.assertTrue(handles)
                    self.assertTrue(all(handle.closed for handle in handles))
                    self.assertFalse((root / 'partial').exists())
                    self.assertEqual(release_tools.file_digest(bundle), sha)
